# 🔭 Solana Liquidity Radar Bot

Monitors DexScreener for new Solana liquidity events and sends alerts to a Telegram channel.

## Setup

### 1. Clone / upload these files to a GitHub repo

Railway deploys directly from GitHub.

### 2. Set Environment Variables in Railway

In your Railway project → **Variables**, add:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Your Telegram bot token from @BotFather |
| `CHANNEL_ID` | Your channel username e.g. `@SolLiquidityRadar` or numeric ID |
| `POLL_INTERVAL` | (Optional) Seconds between scans. Default: `60` |

### 3. Make the bot an admin of your channel

In Telegram:
- Go to your channel → Edit → Administrators
- Add your bot and give it **"Post Messages"** permission

### 4. Deploy on Railway

- Create a new project → **Deploy from GitHub repo**
- Select your repo
- Railway will auto-detect Python and run `python bot.py`

## How It Works

1. Every `POLL_INTERVAL` seconds, the bot hits the DexScreener **token-profiles** endpoint to get the latest Solana token listings.
2. For each new token, it fetches the associated trading pairs.
3. Any pair with liquidity > $0 that hasn't been seen before triggers a Telegram alert.
4. Seen pairs are tracked in memory (resets on restart — this is fine for a polling bot).

## Alert Format

```
🚨 New Liquidity Added on Solana!

🪙 Token: MyToken ($MYT)
📋 Address: ABC123...XYZ9
🏦 DEX: Raydium
💧 Liquidity: $12.5K
🔗 Chart: DexScreener
```

## Notes

- The bot uses DexScreener's free public API — no API key needed.
- To avoid Telegram rate limits, there's a 1s delay between consecutive alerts.
- If you want to add a minimum liquidity filter later, set a `MIN_LIQUIDITY` env var and add a check in `bot.py`.
