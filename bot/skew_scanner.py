"""
Skew Scanner — finds Polymarket sports markets where one side is very cheap.

Strategy:
  - Buy the cheap side (e.g. 5¢ YES on a 5/95 market)
  - Exit at a small % gain (e.g. 10% → 5.5¢, giving $0.5 per 100 shares)
  - Never hold to resolution; pure price momentum play

Why it works:
  - In live sports, a trailing team can score quickly → odds shift fast
  - Very cheap contracts are highly leveraged: 1¢ → 5¢ = 5x your money
  - Market makers re-price in bursts; we front-run the re-price
"""
import time
from typing import List

from .models import Market, SkewedMarket
from .price_tracker import PriceTracker

# Sports categories the scanner cares about (all live-game focused)
SPORTS_CATEGORIES = {
    'basketball', 'hockey', 'baseball', 'football',
    'golf', 'soccer', 'tennis', 'motorsport',
    'mma', 'boxing', 'sports',
}

# Minimum volume to bother showing a market (avoid dead/no-liquidity markets)
MIN_VOLUME_DEFAULT = 500.0


class SkewScanner:
    """
    Scans a list of Polymarket markets and returns the most promising
    skewed-odds candidates, sorted by cheapest side first.

    Parameters
    ----------
    max_cheap_price : float
        Maximum price of the cheap side to qualify (default 0.30 = 30¢).
        Lower = more extreme skews only (e.g. 0.10 for 1-10¢ setups).
    min_volume : float
        Minimum market volume in USD. Filters out illiquid ghost markets.
    sports_only : bool
        If True (default), only return sports-category markets.
    """

    def __init__(
        self,
        max_cheap_price: float = 0.30,
        min_volume: float = MIN_VOLUME_DEFAULT,
        sports_only: bool = True,
    ):
        self.max_cheap_price = max_cheap_price
        self.min_volume = min_volume
        self.sports_only = sports_only

    def scan(
        self,
        markets: List[Market],
        price_tracker: PriceTracker,
    ) -> List[SkewedMarket]:
        """
        Filter and rank markets by skew.

        Returns a list of SkewedMarket objects sorted:
          1. Markets with positive momentum (pumping) first
          2. Then by cheapest price ascending (most extreme skew)
        """
        results: List[SkewedMarket] = []

        for market in markets:
            # Sports filter
            if self.sports_only and market.category not in SPORTS_CATEGORIES:
                continue

            # Volume filter
            vol = market.volume or 0.0
            if self.min_volume > 0 and vol < self.min_volume:
                continue

            yes_p = market.yes_price
            no_p = market.no_price

            # Skip invalid/zero prices
            if yes_p <= 0.0 or no_p <= 0.0:
                continue
            # Skip near-50/50 markets (not skewed enough)
            cheap_p = min(yes_p, no_p)
            if cheap_p > self.max_cheap_price:
                continue

            # Determine cheap side
            if yes_p <= no_p:
                cheap_side = 'YES'
                cheap_price = yes_p
                expensive_price = no_p
            else:
                cheap_side = 'NO'
                cheap_price = no_p
                expensive_price = yes_p

            momentum = price_tracker.get_momentum(market.id, cheap_side, lookback=5)

            results.append(SkewedMarket(
                market=market,
                cheap_side=cheap_side,
                cheap_price=cheap_price,
                expensive_price=expensive_price,
                momentum=momentum,
                last_updated=time.time(),
            ))

        # Sort: positive momentum first, then cheapest price
        results.sort(key=lambda x: (-max(x.momentum, 0), x.cheap_price))

        # Assign sequential display numbers starting at 1
        for i, sm in enumerate(results, start=1):
            sm.display_num = i

        return results

    # ------------------------------------------------------------------ #
    #  Helpers for terminal display                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def signal_label(sm: SkewedMarket, price_tracker: PriceTracker) -> str:
        """
        Short human-readable signal string for display.
        Examples: '🔥 PUMP', '↑ rising', '↓ fading', 'flat'
        """
        label = price_tracker.momentum_label(sm.market.id, sm.cheap_side)
        if label == "PUMP":
            return "🔥 PUMP"
        if label == "rising":
            return "↑ rising"
        if label == "DUMP":
            return "↓ DUMP"
        if label == "fading":
            return "↓ fading"
        return "flat"

    @staticmethod
    def roi_targets(sm: SkewedMarket) -> str:
        """
        Show what price the cheap side needs for 10%, 20%, and 5x gains.
        e.g.  '10%→$0.055  20%→$0.060  5x→$0.250'
        """
        p = sm.cheap_price
        t10 = p * 1.10
        t20 = p * 1.20
        t5x = p * 5.0
        return f"10%→{t10:.3f}  20%→{t20:.3f}  5x→{t5x:.3f}"

    @staticmethod
    def is_likely_live(market: Market) -> bool:
        """
        Heuristic: is this market likely for a game happening right now?
        Uses end_date proximity and high volume as proxies.
        """
        if market.volume and market.volume > 50_000:
            return True
        if market.end_date:
            try:
                import datetime
                end = datetime.datetime.fromisoformat(
                    market.end_date.replace('Z', '+00:00')
                )
                now = datetime.datetime.now(datetime.timezone.utc)
                hours_left = (end - now).total_seconds() / 3600
                return 0 <= hours_left <= 24
            except Exception:
                pass
        return False
