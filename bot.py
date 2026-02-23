import os
import asyncio
import aiohttp
import logging
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
MIN_LIQUIDITY = float(os.environ.get("MIN_LIQUIDITY", "1000"))  # Default $1,000

seen_pairs: set[str] = set()
is_first_run: bool = True


def format_number(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


async def check_liquidity_locked(session: aiohttp.ClientSession, token_address: str) -> tuple[bool, str]:
    """Check if liquidity is locked via Rugcheck API. Returns (is_locked, detail_string)."""
    try:
        url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report/summary"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return False, "❓ Unknown"
            data = await resp.json()
            risks = data.get("risks", [])
            lp_unlocked = any("unlocked" in r.get("name", "").lower() for r in risks)
            lp_locked = any("locked" in r.get("name", "").lower() for r in risks)

            if lp_locked:
                return True, "🔒 Locked"
            elif lp_unlocked:
                return False, "🔓 Unlocked"
            else:
                return False, "❓ Unknown"
    except Exception as e:
        logger.warning(f"Rugcheck lookup failed for {token_address}: {e}")
        return False, "❓ Unknown"


def build_alert_message(pair: dict, lp_lock_status: str) -> str:
    token_name = pair.get("baseToken", {}).get("name", "Unknown")
    token_symbol = pair.get("baseToken", {}).get("symbol", "???")
    token_address = pair.get("baseToken", {}).get("address", "")
    dex_id = pair.get("dexId", "Unknown DEX").capitalize()
    liquidity = pair.get("liquidity", {}).get("usd", 0)
    pair_address = pair.get("pairAddress", "")
    price_usd = pair.get("priceUsd", "N/A")

    dex_link = f"https://dexscreener.com/solana/{pair_address}"
    axiom_link = f"https://axiom.trade/t/{token_address}"

    msg = (
        f"🚨 <b>New Liquidity Added on Solana!</b>\n\n"
        f"🪙 <b>Token:</b> {token_name} (${token_symbol})\n"
        f"📋 <b>Address:</b> <code>{token_address}</code>\n"
        f"🏦 <b>DEX:</b> {dex_id}\n"
        f"💧 <b>Liquidity:</b> {format_number(liquidity)}\n"
        f"🔐 <b>LP Lock:</b> {lp_lock_status}\n"
        f"💰 <b>Price:</b> ${price_usd}\n\n"
        f"🔗 <a href='{dex_link}'>DexScreener</a>  |  ⚡ <a href='{axiom_link}'>Axiom</a>"
    )
    return msg


async def fetch_latest_solana_tokens(session: aiohttp.ClientSession) -> list[dict]:
    try:
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"Token profiles returned {resp.status}")
                return []
            data = await resp.json()
            solana_tokens = [t for t in data if t.get("chainId") == "solana"]
            logger.info(f"Found {len(solana_tokens)} Solana token profiles.")
            return solana_tokens
    except Exception as e:
        logger.error(f"Error fetching token profiles: {e}")
        return []


async def fetch_pairs_for_token(session: aiohttp.ClientSession, address: str) -> list[dict]:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("pairs") or []
    except Exception as e:
        logger.error(f"Error fetching pairs for {address}: {e}")
        return []


async def send_alert(bot: Bot, message: str):
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        logger.info("Alert sent.")
    except TelegramError as e:
        logger.error(f"Failed to send Telegram message: {e}")


async def monitor(bot: Bot):
    global is_first_run
    logger.info(f"🔭 Solana Liquidity Radar started. Min liquidity: ${MIN_LIQUIDITY:,.0f}")

    async with aiohttp.ClientSession() as session:
        while True:
            tokens = await fetch_latest_solana_tokens(session)
            new_alerts = 0

            for token in tokens:
                address = token.get("tokenAddress")
                if not address:
                    continue

                pairs = await fetch_pairs_for_token(session, address)

                for pair in pairs:
                    pair_address = pair.get("pairAddress")
                    if not pair_address:
                        continue

                    if pair_address in seen_pairs:
                        continue

                    seen_pairs.add(pair_address)

                    if is_first_run:
                        continue

                    liquidity = pair.get("liquidity", {}).get("usd", 0) or 0
                    if liquidity < MIN_LIQUIDITY:
                        logger.info(f"Skipping {pair_address} — liquidity ${liquidity:.0f} below minimum")
                        continue

                    _, lp_lock_status = await check_liquidity_locked(session, address)

                    msg = build_alert_message(pair, lp_lock_status)
                    await send_alert(bot, msg)
                    new_alerts += 1
                    await asyncio.sleep(1)

            if is_first_run:
                logger.info(f"First run complete. Seeded {len(seen_pairs)} existing pairs. Now watching for NEW pairs...")
                is_first_run = False
            else:
                logger.info(f"Cycle complete. Sent {new_alerts} alerts. Watching {len(seen_pairs)} pairs. Sleeping {POLL_INTERVAL}s...")

            await asyncio.sleep(POLL_INTERVAL)


async def main():
    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    logger.info(f"Bot started as @{me.username}")
    await monitor(bot)


if __name__ == "__main__":
    asyncio.run(main())
