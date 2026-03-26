# Solana Liquidity Radar + DEX Paid (Token Boost) Bot

Monitors DexScreener for:

1. **New Solana liquidity** (token profiles feed) — original “liquidity radar” alerts.
2. **New DEX Paid listings** — [DexScreener Token Boosts](https://docs.dexscreener.com/api/reference) (`GET /token-boosts/latest/v1`), i.e. paid/boosted Solana tokens, with a detailed Telegram report (DexScreener + Rugcheck enrichment).

Uses only free public APIs: **DexScreener** and **Rugcheck** (no keys).

## Environment variables

Copy `.env.example` to `.env` and set:

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | Yes* | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Yes* | Channel `@username` or numeric chat ID |
| `POLL_INTERVAL` | No | Seconds between scans (default `60`) |
| `DEX_PAID_POLL_INTERVAL` | No | Override for DEX Paid loop only (defaults to `POLL_INTERVAL`) |
| `ENABLE_LIQUIDITY` | No | `true` / `false` — liquidity radar (default `true`) |
| `ENABLE_DEX_PAID` | No | `true` / `false` — token boost monitor (default `true`) |
| `MIN_LIQUIDITY` | No | Minimum USD liquidity for liquidity alerts (default `1000`) |

\*Legacy names `BOT_TOKEN` and `CHANNEL_ID` still work if `TELEGRAM_*` are not set.

## Local setup

From the project folder (`SolanaLiquidityRadarBot`):

```powershell
cd "C:\Projects\New folder\SolanaLiquidityRadarBot"
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` with `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`, then:

```powershell
python bot.py
```

Make the bot an **administrator** of your channel with permission to **post messages** (same as before).

## DEX Paid alert contents

For each **new** boosted mint (not seen before in this process):

- Name, symbol, contract (mint)
- Pair age (from DexScreener `pairCreatedAt`), price, market cap, liquidity (USD)
- LP status (from Rugcheck risk labels), mint authority / freeze authority (shown as revoked or not)
- Top 10 holders % (when Rugcheck exposes `topHolders`)
- Dev wallet and holding % (when present in Rugcheck)
- Links: **Axiom**, **DexScreener** (boost URL when available), **Rugcheck**, **Pump.fun**

The first poll **seeds** all current boosted mints without alerting, so you only get **new** boosts after startup (same pattern as the liquidity radar).

## Deploy on Railway

### One-time: CLI

1. Install the [Railway CLI](https://docs.railway.com/guides/cli) and log in:

   ```powershell
   npm i -g @railway/cli
   railway login
   ```

2. In the project directory, link and deploy:

   ```powershell
   cd "C:\Projects\New folder\SolanaLiquidityRadarBot"
   railway init
   railway up
   ```

   Or connect the GitHub repo in the Railway dashboard and set the **root directory** to this repo if it lives in a monorepo.

### Variables on Railway

In the Railway project → **Variables**, add at least:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

Optional: `POLL_INTERVAL`, `ENABLE_LIQUIDITY`, `ENABLE_DEX_PAID`, `MIN_LIQUIDITY`, `DEX_PAID_POLL_INTERVAL`.

`railway.toml` sets `startCommand` to `python bot.py` and uses the Nixpacks builder.

## Notes

- **“DEX Paid”** here follows DexScreener’s **Token Boosts** list (paid promotion on DexScreener). The separate **Ads** feed (`/ads/latest/v1`) is a different product; this bot uses boosts as the standard “paid DEX” signal.
- Seen tokens/pairs are kept **in memory**; restarts re-seed and only alert on **new** items after the first cycle.
- Rugcheck occasionally returns errors; the bot still sends the DexScreener block and shows **N/A** or **Unknown** for missing Rugcheck fields.

## License

Private / your license.
