#!/usr/bin/env python3.11
"""
Multi-Platform Arbitrage Bot
Platforms: Polymarket | Kalshi | Gemini Predictions
Scans all 3 platform pairs every SCAN_INTERVAL seconds.
Logs all results to arbitrage_log.txt
"""
import time
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from bot.polymarket_client import PolymarketClient
from bot.kalshi_client import KalshiClient
from bot.gemini_client import GeminiClient
from bot.matcher import MarketMatcher
from bot.detector import ArbitrageDetector
from bot.claude_verifier import verify_opportunities

# ── Configuration ────────────────────────────────────────────────────────────
SCAN_INTERVAL = 30          # seconds between scans (30s = fast enough for live arb)
MIN_PROFIT_PCT = 0.005      # 0.5% minimum net profit to flag as opportunity
ALERT_THRESHOLD = 0.02      # 2%+ profit triggers a loud alert
LOG_FILE = os.path.join(os.path.dirname(__file__), "arbitrage_log.txt")

# API keys are loaded from .env file (see .env.example)

# Platform pairs to scan
PLATFORM_PAIRS = [
    ("polymarket", "kalshi"),
    ("polymarket", "gemini"),
    ("kalshi",     "gemini"),
]

# ── Logging ──────────────────────────────────────────────────────────────────
def log(msg: str, level: str = "INFO", also_print: bool = True):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    if also_print:
        print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def alert(msg: str):
    """High-priority alert — also prints a visual separator."""
    print("\n" + "!" * 70, flush=True)
    log(msg, level="ALERT")
    print("!" * 70 + "\n", flush=True)


# ── Execution stubs ───────────────────────────────────────────────────────────
def execute_opportunity(opp, platform_a: str, platform_b: str, dry_run: bool = True):
    """
    Execute an arbitrage opportunity.
    Set dry_run=False to place real orders (requires API keys in environment).

    Order flow:
      1. Place order on platform A (buy YES or NO as indicated)
      2. Place order on platform B (buy the opposite side)
      Both orders must fill for the arb to be risk-free.

    Required environment variables:
      Polymarket: POLY_PRIVATE_KEY, POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE
      Kalshi:     KALSHI_API_KEY, KALSHI_API_SECRET
      Gemini:     GEMINI_API_KEY, GEMINI_API_SECRET
    """
    if dry_run:
        log(f"[DRY RUN] Would execute: {opp.strategy}", level="EXEC")
        log(f"[DRY RUN]   Market A ({platform_a}): {opp.market_a.question[:60]}", level="EXEC")
        log(f"[DRY RUN]   Market B ({platform_b}): {opp.market_b.question[:60]}", level="EXEC")
        log(f"[DRY RUN]   Expected profit: {opp.profit_margin:.2%}", level="EXEC")
        return

    # TODO: Implement live execution
    # Polymarket execution requires:
    #   - py-clob-client library (pip install py-clob-client)
    #   - Polygon wallet with USDC
    #   - API keys from polymarket.com/profile
    #
    # Kalshi execution:
    #   - POST /trade-api/v2/orders with KALSHI_API_KEY + KALSHI_API_SECRET
    #   - Use limit orders (maker) for lower fees (~0.1% vs 2.0% taker)
    #
    # Gemini execution:
    #   - POST /v1/prediction-markets/order/new with GEMINI_API_KEY + GEMINI_API_SECRET
    #   - Use limit orders for maker fee (1.75% flat)
    log(f"[LIVE] Executing: {opp.strategy}", level="EXEC")


# ── Main scan loop ────────────────────────────────────────────────────────────
def run_scan(clients: dict, matcher: MarketMatcher, detector: ArbitrageDetector, scan_num: int):
    """Fetch markets from all 3 platforms and scan all platform pairs."""
    log(f"=== SCAN #{scan_num} ===")

    # Fetch all markets
    t0 = time.time()
    markets = {}
    for name, client in clients.items():
        try:
            m = client.get_active_markets(limit=200)
            markets[name] = m
            log(f"  {name.title():12}: {len(m):3} markets")
        except Exception as e:
            log(f"  {name.title():12}: ERROR - {e}", level="WARN")
            markets[name] = []

    fetch_time = time.time() - t0
    log(f"  Fetch time: {fetch_time:.1f}s")

    # Scan all platform pairs
    all_opportunities = []
    total_matches = 0

    for platform_a, platform_b in PLATFORM_PAIRS:
        ma = markets.get(platform_a, [])
        mb = markets.get(platform_b, [])
        if not ma or not mb:
            continue

        matches = matcher.find_matches(ma, mb)
        opps = detector.detect_opportunities(matches, platform_a, platform_b)
        total_matches += len(matches)

        pair_label = f"{platform_a[:4]}<->{platform_b[:4]}"
        log(f"  {pair_label:12}: {len(matches):2} matches, {len(opps):2} raw opportunities")

        # Claude verification — filter out false positives
        if opps:
            log(f"  Sending {len(opps)} opportunities to Claude for verification...")
            verified_opps = verify_opportunities(opps, platform_a, platform_b)
            log(f"  Claude verified: {len(verified_opps)}/{len(opps)} are real opportunities")
            for opp in verified_opps:
                all_opportunities.append((opp, platform_a, platform_b))
        
    log(f"  Total: {total_matches} matched pairs, {len(all_opportunities)} verified opportunities")

    # Sort by profit descending
    all_opportunities.sort(key=lambda x: x[0].profit_margin, reverse=True)

    # Report opportunities
    if all_opportunities:
        log("*** VERIFIED ARBITRAGE OPPORTUNITIES ***", level="OPP")
        for opp, pa, pb in all_opportunities:
            reason = getattr(opp, 'verification_reason', 'Verified')
            log(f"  [{pa}<->{pb}] Profit: {opp.profit_margin:.2%} | Claude: {reason}", level="OPP")
            log(f"    A ({pa}): {opp.market_a.question[:70]}", level="OPP")
            log(f"    B ({pb}): {opp.market_b.question[:70]}", level="OPP")
            log(f"    {opp.strategy}", level="OPP")

            # High-value alert
            if opp.profit_margin >= ALERT_THRESHOLD:
                alert(f"HIGH-VALUE ARB: {opp.profit_margin:.2%} profit | {pa}<->{pb}")
                alert(f"  {opp.market_a.question[:60]}")
                alert(f"  {opp.strategy}")

            # Execute (dry run by default — set dry_run=False with API keys configured)
            execute_opportunity(opp, pa, pb, dry_run=True)
    else:
        log("  No opportunities this scan.")

    # Near-misses (closest to arbitrage)
    log("  Near-misses (top 3 closest to arb):")
    near = []
    for platform_a, platform_b in PLATFORM_PAIRS:
        ma = markets.get(platform_a, [])
        mb = markets.get(platform_b, [])
        if not ma or not mb:
            continue
        from bot.detector import get_fee
        matches = matcher.find_matches(ma, mb)
        for mka, mkb in matches:
            fee_a = get_fee(platform_a, mka.category)
            fee_b = get_fee(platform_b, mkb.category)
            c1 = mka.yes_price + mkb.no_price + fee_a + fee_b
            c2 = mkb.yes_price + mka.no_price + fee_a + fee_b
            best_cost = min(c1, c2)
            near.append((best_cost, mka, mkb, platform_a, platform_b))

    near.sort(key=lambda x: x[0])
    for cost, mka, mkb, pa, pb in near[:3]:
        gap = cost - 1.0
        log(f"    Cost={cost:.4f} ({gap:+.4f}) [{pa}<->{pb}] [{mka.category}]")
        log(f"      {mka.question[:65]}")
        log(f"      {mkb.question[:65]}")

    log("")
    return all_opportunities


def main():
    print("=" * 70)
    print("  Multi-Platform Arbitrage Bot")
    print("  Platforms: Polymarket | Kalshi | Gemini Predictions")
    print(f"  Scan interval: {SCAN_INTERVAL}s | Min profit: {MIN_PROFIT_PCT:.1%}")
    print(f"  Log file: {LOG_FILE}")
    print("=" * 70)
    print()
    print("  Account setup required for live trading:")
    print("  1. Polymarket: polymarket.com → connect wallet → get API keys")
    print("     Set: POLY_PRIVATE_KEY, POLY_API_KEY, POLY_API_SECRET, POLY_API_PASSPHRASE")
    print("  2. Kalshi: kalshi.com → create account → Settings → API")
    print("     Set: KALSHI_API_KEY, KALSHI_API_SECRET")
    print("  3. Gemini: exchange.gemini.com → create account → API Settings")
    print("     Set: GEMINI_API_KEY, GEMINI_API_SECRET")
    print()
    print("  Currently running in DRY RUN mode (no real orders placed).")
    print("  To enable live trading: set dry_run=False in execute_opportunity()")
    print()

    log("Bot started", also_print=False)

    # Initialize clients
    clients = {
        "polymarket": PolymarketClient(),
        "kalshi":     KalshiClient(),
        "gemini":     GeminiClient(),
    }

    matcher = MarketMatcher(threshold=0.70)
    detector = ArbitrageDetector(min_profit_margin=MIN_PROFIT_PCT)

    scan_num = 0
    total_opportunities = 0

    while True:
        scan_num += 1
        try:
            opps = run_scan(clients, matcher, detector, scan_num)
            total_opportunities += len(opps)
            print(
                f"\n[Scan #{scan_num} complete] "
                f"Opportunities found so far: {total_opportunities} | "
                f"Next scan in {SCAN_INTERVAL}s... (Ctrl+C to stop)\n",
                flush=True
            )
        except KeyboardInterrupt:
            log("Bot stopped by user.")
            print("\nBot stopped.")
            break
        except Exception as e:
            import traceback
            log(f"ERROR during scan: {e}", level="ERROR")
            log(traceback.format_exc(), level="ERROR")

        try:
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            log("Bot stopped by user.")
            print("\nBot stopped.")
            break


if __name__ == "__main__":
    main()
