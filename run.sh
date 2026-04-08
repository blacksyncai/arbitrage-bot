#!/bin/bash
# One-command setup: installs everything and launches the bot in dry-run
set -e

echo ""
echo "  Polymarket Sports Scalper — Setup"
echo ""

# Install dependencies silently
pip install rich requests python-dotenv py-clob-client -q

# Create .env if it doesn't exist
if [ ! -f .env ]; then
  cp .env.example .env
fi

echo "  Done. Launching bot in DRY RUN mode..."
echo "  (No real money — just watching the markets)"
echo ""

python bot_runner.py "$@"
