#!/usr/bin/env python3
"""
Interactive credential setup wizard.
Run this once to configure your Polymarket keys.
"""
import os
import sys
from pathlib import Path

def main():
    env_file = Path(".env")
    if not env_file.exists():
        env_file.write_text(Path(".env.example").read_text())

    print("""
  ══════════════════════════════════════════════════
   Polymarket Credential Setup
  ══════════════════════════════════════════════════

  You need 3 things from Polymarket:

  1. A Polygon wallet with USDC
     → MetaMask → switch to Polygon network
     → Buy USDC via polymarket.com/deposit

  2. Your wallet PRIVATE KEY
     → MetaMask → ⋮ → Account Details → Export Private Key
     → Starts with 0x

  3. Polymarket API credentials
     → polymarket.com → top-right avatar → API Keys → Create
     → Copies you a key, secret, and passphrase

  ══════════════════════════════════════════════════
""")

    private_key = input("  Paste your wallet PRIVATE KEY (0x...): ").strip()
    api_key     = input("  Paste POLY_API_KEY:         ").strip()
    api_secret  = input("  Paste POLY_API_SECRET:      ").strip()
    api_pass    = input("  Paste POLY_API_PASSPHRASE:  ").strip()

    content = env_file.read_text()
    replacements = {
        "POLY_PRIVATE_KEY=0x...": f"POLY_PRIVATE_KEY={private_key}",
        "POLY_API_KEY=":          f"POLY_API_KEY={api_key}",
        "POLY_API_SECRET=":       f"POLY_API_SECRET={api_secret}",
        "POLY_API_PASSPHRASE=":   f"POLY_API_PASSPHRASE={api_pass}",
        "DRY_RUN=true":           "DRY_RUN=false",
    }
    for old, new in replacements.items():
        content = content.replace(old, new)

    env_file.write_text(content)

    print("""
  ✓ Credentials saved to .env
  ✓ DRY_RUN set to false

  Launch the live bot:
    python bot_runner.py

""")

if __name__ == "__main__":
    main()
