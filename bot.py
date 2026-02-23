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

DEXSCREENER_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

seen_pairs: set[str] = set()


def format_number(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    elif value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def build_alert_message(pair: dict, token_address: str) -> str:
    token_name = pair.get("baseToken", {}).get("name", "Unknown")
    token_symbol = pair.get("baseToken", {}).get("symbol", "???")
    dex_id = pair.get("dexId", "Unknown DEX").capitalize()
    liquidity = pair.get("liquidity", {}).get("usd", 0)
    pair_address = pair.get("pairAddress", "")
    dex_link = f"https://dexscreener.com/solana/{pair_address}"

    short_addr = f"{token_address[:6]}...{token_address[-4:]}"

    msg = (
        f"🚨 <b>New Liquidity Added on Solana!</b>\n\n"
        f"🪙 <b>Token:</b> {token_name} (${token_symbol})\n"
        f"📋 <b>Address:</b> <code>{token_address}</code>\n"
        f"🏦 <b>DEX:</b> {dex_id}\n"
        f"💧 <b>Liquidity:</b> {format_number(liquidity)}\n"
        f"🔗 <b>Chart:</b> <a href='{dex_link}'>DexScreener</a>"
    )
    return msg


async def fetch_new_solana_tokens(session: aiohttp.ClientSession) -> list[dict]:
    try:
        async with session.get(DEXSCREENER_URL, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logger.warning(f"Token profiles endpoint returned {resp.status}")
                return []
            data = await resp.json()
            # Filter to Solana only
            return [t for t in data if t.get("chainId") == "solana"]
    except Exception as e:
        logger.error(f"Error fetching token profiles: {e}")
        return []


async def fetch_pairs_for_token(session: aiohttp.ClientSession, address: str) -> list[dict]:
    try:
        url = DEXSCREENER_PAIRS_URL.format(address=address)
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
    logger.info("🔭 Solana Liquidity Radar started. Monitoring DexScreener...")
    async with aiohttp.ClientSession() as session:
        while True:
            tokens = await fetch_new_solana_tokens(session)
            logger.info(f"Found {len(tokens)} Solana token profiles.")

            for token in tokens:
                address = token.get("tokenAddress")
                if not address:
                    continue

                pairs = await fetch_pairs_for_token(session, address)

                for pair in pairs:
                    pair_address = pair.get("pairAddress")
                    if not pair_address or pair_address in seen_pairs:
                        continue

                    seen_pairs.add(pair_address)
                    liquidity = pair.get("liquidity", {}).get("usd", 0)

                    if liquidity and liquidity > 0:
                        msg = build_alert_message(pair, address)
                        await send_alert(bot, msg)
                        await asyncio.sleep(1)  # Avoid rate limits between messages

            logger.info(f"Cycle complete. Sleeping {POLL_INTERVAL}s...")
            await asyncio.sleep(POLL_INTERVAL)


async def main():
    bot = Bot(token=BOT_TOKEN)
    me = await bot.get_me()
    logger.info(f"Bot started as @{me.username}")
    await monitor(bot)


if __name__ == "__main__":
    asyncio.run(main())
