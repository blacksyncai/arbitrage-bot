# Arbitrage Bot

A multi-platform prediction market arbitrage scanner that monitors **Polymarket**, **Kalshi**, and **Gemini Predictions** simultaneously, using Claude AI to verify every opportunity before alerting.

## How It Works

1. Fetches active markets from all 3 platforms every 30 seconds
2. Matches markets across platforms using NLP similarity scoring
3. Calculates net profit after all platform fees
4. Sends every potential opportunity to **Claude AI** for verification (prevents false positives)
5. Alerts on verified opportunities above the profit threshold

## Platforms Covered

| Platform | Markets | Taker Fee | Maker Fee |
|---|---|---|---|
| Polymarket | 200+ | 0.75–1.80% (varies by category) | 0% |
| Kalshi | 99+ | ~2.0% | ~0.1% |
| Gemini Predictions | 500+ | ~1.75% | ~0.5% |

## Setup

### 1. Install dependencies

```bash
pip install requests anthropic python-dotenv rich
```

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

You need at minimum an **Anthropic API key** for Claude verification:
- Get one at [console.anthropic.com](https://console.anthropic.com)

For live trading (optional), you also need keys for each platform:
- **Polymarket**: [polymarket.com](https://polymarket.com) → Settings → API Keys
- **Kalshi**: [kalshi.com](https://kalshi.com) → Settings → API
- **Gemini**: [exchange.gemini.com](https://exchange.gemini.com) → Settings → API

### 3. Run the bot

```bash
python run_bot.py
```

The bot runs in **dry run mode by default** — it scans and alerts but does not place orders. To enable live trading, set `dry_run=False` in `run_bot.py` and ensure your platform API keys are configured.

## Architecture

```
arbitrage_bot/
├── run_bot.py              # Main entry point and scan loop
├── bot/
│   ├── polymarket_client.py  # Polymarket API client
│   ├── kalshi_client.py      # Kalshi API client  
│   ├── gemini_client.py      # Gemini Predictions API client
│   ├── matcher.py            # NLP-based cross-platform market matching
│   ├── detector.py           # Arbitrage opportunity detection + fee math
│   ├── scanner.py            # Multi-platform scan orchestration
│   ├── claude_verifier.py    # Claude AI opportunity verification layer
│   └── models.py             # Shared data models
└── .env.example              # API key template
```

## Fee Structure & ROI

The bot calculates **net profit after all fees**. The minimum threshold is configurable in `run_bot.py`:

```python
MIN_PROFIT_PCT = 0.005   # 0.5% minimum net profit
ALERT_THRESHOLD = 0.02   # 2%+ triggers a loud alert
```

Real arbitrage windows typically appear during:
- Breaking news events (one platform reprices faster than another)
- New market listings (price discovery period)
- Large whale trades temporarily moving one platform's price

## Disclaimer

This is a research tool. Prediction market trading involves financial risk. Always verify opportunities manually before executing trades.
