"""
DexScreener Token Boost (DEX Paid) monitor for Solana.

Polls https://api.dexscreener.com/token-boosts/latest/v1 and enriches with
DexScreener pairs + Rugcheck report data.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiohttp
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"


def _chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHANNEL_ID", "")


def _poll_interval() -> int:
    return int(os.environ.get("DEX_PAID_POLL_INTERVAL", os.environ.get("POLL_INTERVAL", "60")))

seen_boost_tokens: set[str] = set()
dex_paid_first_run: bool = True


def _fmt_usd(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.2f}K"
    return f"${v:.2f}"


def _fmt_price(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value) if value is not None else "N/A"
    if v == 0:
        return "$0"
    if v < 0.00000001:
        return f"${v:.2e}"
    if v < 1:
        return f"${v:.8f}".rstrip("0").rstrip(".")
    return f"${v:.6f}".rstrip("0").rstrip(".")


def _human_age_from_ms(pair_created_ms: Any) -> str:
    try:
        ts = int(pair_created_ms) / 1000.0
    except (TypeError, ValueError):
        return "Unknown"
    created = datetime.fromtimestamp(ts, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - created
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hrs = mins // 60
    if hrs < 48:
        return f"{hrs}h"
    days = hrs // 24
    return f"{days}d"


def _authority_revoked(auth: Any) -> str:
    """Yes = authority revoked / none (typical safety signal)."""
    if auth is None:
        return "Yes"
    if isinstance(auth, str):
        s = auth.strip()
        if s == "" or s == "11111111111111111111111111111111":
            return "Yes"
    return "No"


def _lp_locked_from_risks(risks: list[dict[str, Any]] | None) -> str:
    if not risks:
        return "Unknown"
    for r in risks:
        name = (r.get("name") or "").lower()
        if "locked" in name and "unlock" not in name:
            return "Locked"
        if "unlocked" in name or "not locked" in name:
            return "Not locked"
    return "Unknown"


def _holder_pct(holder: dict[str, Any]) -> float:
    for key in ("pct", "percentage", "percent", "p"):
        if key in holder and holder[key] is not None:
            try:
                v = float(holder[key])
                if 0 < v <= 1.0:
                    return v * 100.0
                return v
            except (TypeError, ValueError):
                pass
    ui = holder.get("uiAmount")
    supply = holder.get("supply") or holder.get("totalSupply")
    try:
        if ui is not None and supply:
            return float(ui) / float(supply) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return 0.0


def _top10_pct(top_holders: list[dict[str, Any]] | None) -> str:
    if not top_holders:
        return "N/A"
    total = sum(_holder_pct(h) for h in top_holders[:10])
    return f"{total:.2f}%"


def _dev_wallet_and_pct(data: dict[str, Any]) -> tuple[str, str]:
    dev = data.get("creator") or data.get("creatorAddress") or data.get("deployer")
    if isinstance(dev, dict):
        dev = dev.get("address") or dev.get("pubkey")
    dev_str = dev if isinstance(dev, str) and len(dev) > 8 else "N/A"

    pct_str = "N/A"
    bal = data.get("creatorBalance")
    if bal is not None:
        try:
            b = float(bal)
            # Rugcheck sometimes returns raw amount; tokenMeta.totalSupply
            meta = data.get("tokenMeta") or data.get("token") or {}
            supply = meta.get("totalSupply") or meta.get("supply")
            if supply:
                pct_str = f"{100.0 * b / float(supply):.2f}%"
            else:
                pct_str = f"{b:.4f}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Insider / creator pct fields (varies by response)
    for key in ("creatorPercentage", "creatorPct", "insiderAllocation"):
        if key in data and data[key] is not None:
            try:
                pct_str = f"{float(data[key]):.2f}%"
            except (TypeError, ValueError):
                pass
            break

    return dev_str, pct_str


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any | None:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                logger.warning("GET %s -> %s", url, resp.status)
                return None
            return await resp.json()
    except Exception as e:
        logger.warning("Request failed %s: %s", url, e)
        return None


async def fetch_solana_boosts(session: aiohttp.ClientSession) -> list[dict[str, Any]]:
    data = await _fetch_json(session, BOOSTS_URL)
    if not isinstance(data, list):
        return []
    return [b for b in data if b.get("chainId") == "solana"]


async def fetch_pairs_for_token(session: aiohttp.ClientSession, address: str) -> list[dict[str, Any]]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    data = await _fetch_json(session, url)
    if not isinstance(data, dict):
        return []
    pairs = data.get("pairs") or []
    return [p for p in pairs if p.get("chainId") == "solana"]


def pick_best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pairs:
        return None

    def liq(p: dict[str, Any]) -> float:
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    return max(pairs, key=liq)


async def fetch_rugcheck_bundle(session: aiohttp.ClientSession, mint: str) -> dict[str, Any]:
    """Merge full report + summary when possible."""
    out: dict[str, Any] = {}
    report_url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report"
    summary_url = f"https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"

    rep = await _fetch_json(session, report_url)
    if isinstance(rep, dict):
        out.update(rep)

    summ = await _fetch_json(session, summary_url)
    if isinstance(summ, dict):
        for k, v in summ.items():
            if k not in out or out[k] in (None, [], {}):
                out[k] = v
    return out


def build_dex_paid_message(
    boost: dict[str, Any],
    pair: dict[str, Any],
    rug: dict[str, Any],
) -> str:
    base = pair.get("baseToken") or {}
    name = html.escape(str(base.get("name") or "Unknown"))
    symbol = html.escape(str(base.get("symbol") or "???"))
    mint = base.get("address") or boost.get("tokenAddress") or ""
    pair_addr = pair.get("pairAddress") or ""

    price = pair.get("priceUsd")
    mcap = pair.get("marketCap") or pair.get("fdv")
    liq_usd = (pair.get("liquidity") or {}).get("usd")
    age = _human_age_from_ms(pair.get("pairCreatedAt"))

    risks = rug.get("risks")
    if not isinstance(risks, list):
        risks = []
    lp_status = _lp_locked_from_risks(risks)

    mint_auth = rug.get("mintAuthority")
    freeze_auth = rug.get("freezeAuthority")
    mint_rev = _authority_revoked(mint_auth)
    freeze_rev = _authority_revoked(freeze_auth)

    top_h = rug.get("topHolders")
    if not isinstance(top_h, list):
        top_h = None
    top10 = _top10_pct(top_h)

    dev_wallet, dev_pct = _dev_wallet_and_pct(rug)

    dex_url = boost.get("url") or (f"https://dexscreener.com/solana/{pair_addr}" if pair_addr else f"https://dexscreener.com/solana/{mint}")
    axiom = f"https://axiom.trade/t/{mint}"
    rug_link = f"https://rugcheck.xyz/tokens/{mint}"
    pump = f"https://pump.fun/coin/{mint}"

    boost_amt = boost.get("amount") or boost.get("totalAmount")
    boost_line = ""
    if boost_amt is not None:
        boost_line = f"\n🚀 <b>Boost:</b> {boost_amt} SOL (package)\n"

    return (
        f"💎 <b>New DEX Paid (Token Boost)</b>\n\n"
        f"🪙 <b>Name:</b> {name}\n"
        f"📛 <b>Symbol:</b> ${symbol}\n"
        f"📋 <b>Contract:</b> <code>{mint}</code>\n"
        f"⏱️ <b>Pair age:</b> {age}\n"
        f"💵 <b>Price:</b> {_fmt_price(price)}\n"
        f"📊 <b>Mkt cap:</b> {_fmt_usd(mcap)}\n"
        f"💧 <b>Liquidity:</b> {_fmt_usd(liq_usd)}\n"
        f"🔐 <b>LP (Rugcheck):</b> {lp_status}\n"
        f"🧊 <b>Mint revoked:</b> {mint_rev}\n"
        f"❄️ <b>Freeze revoked:</b> {freeze_rev}\n"
        f"👥 <b>Top 10 holders:</b> {top10}\n"
        f"🧑‍💻 <b>Dev wallet:</b> <code>{dev_wallet}</code>\n"
        f"📦 <b>Dev holding:</b> {dev_pct}\n"
        f"{boost_line}"
        f"🔗 <a href=\"{axiom}\">Axiom</a> · <a href=\"{dex_url}\">DexScreener</a> · "
        f"<a href=\"{rug_link}\">Rugcheck</a> · <a href=\"{pump}\">Pump.fun</a>"
    )


async def send_dex_paid_alert(bot: Bot, text: str) -> None:
    cid = _chat_id()
    if not cid:
        logger.error("TELEGRAM_CHAT_ID / CHANNEL_ID not set for dex paid alerts")
        return
    try:
        await bot.send_message(
            chat_id=cid,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info("DEX Paid alert sent.")
    except TelegramError as e:
        logger.error("DEX Paid Telegram error: %s", e)


async def monitor_dex_paid(bot: Bot) -> None:
    global dex_paid_first_run

    interval = _poll_interval()
    logger.info(
        "DEX Paid monitor (Token Boosts) every %ss. Chat: %s",
        interval,
        _chat_id() or "(missing)",
    )

    async with aiohttp.ClientSession() as session:
        while True:
            boosts = await fetch_solana_boosts(session)
            alerts = 0

            for boost in boosts:
                mint = boost.get("tokenAddress")
                if not mint or not isinstance(mint, str):
                    continue

                if mint in seen_boost_tokens:
                    continue
                seen_boost_tokens.add(mint)

                if dex_paid_first_run:
                    continue

                pairs = await fetch_pairs_for_token(session, mint)
                pair = pick_best_pair(pairs)
                if not pair:
                    logger.info("No DexScreener pair yet for boosted %s — skipping alert", mint)
                    continue

                rug = await fetch_rugcheck_bundle(session, mint)
                msg = build_dex_paid_message(boost, pair, rug)
                # Telegram hard limit 4096; trim if needed
                if len(msg) > 4090:
                    msg = msg[:4087] + "..."

                await send_dex_paid_alert(bot, msg)
                alerts += 1
                await asyncio.sleep(1.2)

            if dex_paid_first_run:
                logger.info(
                    "DEX Paid first run: seeded %s boosted mints (no alerts).",
                    len(seen_boost_tokens),
                )
                dex_paid_first_run = False
            else:
                logger.info(
                    "DEX Paid cycle: %s new alerts. Tracking %s mints. Sleep %ss.",
                    alerts,
                    len(seen_boost_tokens),
                    interval,
                )

            await asyncio.sleep(interval)
