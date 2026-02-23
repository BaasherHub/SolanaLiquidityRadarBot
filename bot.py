import os
import asyncio
import aiohttp
import logging
from datetime import datetime, timezone
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # e.g. @SolLiquidityRadar or -100xxxxxxxxxx
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))  # seconds

DEXSCREENER_NEW_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/solana"
DEXSCREENER_LATEST_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q=solana"

# Use the new pairs endpoint that returns recently added pairs
NEW_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs/solana"

seen_pairs: set[str] = set()
is_first_run: bool = True


def format_number(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def build_alert_message(pair: dict) -> str:
    token_name = pair.get("baseToken", {}).get("name", "Unknown")
    token_symbol = pair.get("baseToken", {}).get("symbol", "???")
    token_address = pair.get("baseToken", {}).get("address", "")
    dex_id = pair.get("dexId", "Unknown DEX").capitalize()
    liquidity = pair.get("liquidity", {}).get("usd", 0)
    pair_address = pair.get("pairAddress", "")
    price_usd = pair.get("priceUsd", "N/A")
    dex_link = f"https://dexscreener.com/solana/{pair_address}"

    msg = (
        f"🚨 <b>New Liquidity Added on Solana!</b>\n\n"
        f"🪙 <b>Token:</b> {token_name} (${token_symbol})\n"
        f"📋 <b>Address:</b> <code>{token_address}</code>\n"
        f"🏦 <b>DEX:</b> {dex_id}\n"
        f"💧 <b>Liquidity:</b> {format_number(liquidity)}\n"
        f"💰 <b>Price:</b> ${price_usd}\n"
        f"🔗 <b>Chart:</b> <a href='{dex_link}'>DexScreener</a>"
    )
    return msg


async def fetch_latest_solana_pairs(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch the latest Solana pairs from DexScreener."""
    # Use the token-profiles endpoint to get newest tokens, then fetch their pairs
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
    logger.info("🔭 Solana Liquidity Radar started. Monitoring DexScreener...")

    async with aiohttp.ClientSession() as session:
        while True:
            tokens = await fetch_latest_solana_pairs(session)
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

                    # On first run, just seed seen_pairs without alerting
                    if is_first_run:
                        continue

                    liquidity = pair.get("liquidity", {}).get("usd", 0)
                    if liquidity and liquidity > 0:
                        msg = build_alert_message(pair)
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
