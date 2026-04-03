"""
Arbitrage detector with accurate per-platform fee structures.

Fee structures (taker, as of April 2026):
  Polymarket:
    - Sports:      0.75% peak (dynamic, bell-curve at 50¢)
    - Politics:    1.00% peak
    - Economics:   1.50% peak
    - Crypto:      1.80% peak
    - Geopolitics: 0.00% (FREE)
    - Effective avg at 50¢ midpoint: ~70% of peak rate
    - Maker rebates available (0% maker fee)

  Kalshi:
    - Taker: ~2.0% (0.07 per contract at 50¢ = ~14%, but capped)
    - Maker: ~0.1% (limit orders)
    - We model taker=2.0%, maker=0.1%

  Gemini:
    - Taker: 7% of edge = 0.07 × C × P × (1-P)
      At P=0.50: effective ~1.75%
      At P=0.25 or 0.75: effective ~1.31%
      At P=0.10 or 0.90: effective ~0.63%
    - Maker: 1.75% (flat)
"""
from typing import List, Tuple, Optional
from .models import Market, ArbitrageOpportunity


# Per-platform taker fee estimates (conservative, at ~50¢ midpoint)
PLATFORM_FEES = {
    "polymarket": {
        "sports":      0.0075,
        "politics":    0.010,
        "economics":   0.015,
        "crypto":      0.018,
        "geopolitics": 0.000,   # FREE
        "finance":     0.010,
        "culture":     0.0125,
        "tech":        0.010,
        "default":     0.010,
    },
    "kalshi": {
        "default":     0.020,   # taker
        "maker":       0.001,   # limit order
    },
    "gemini": {
        "default":     0.0175,  # taker at 50¢ (7% of edge × 0.25)
        "maker":       0.0175,  # flat maker fee
    },
}


def get_fee(platform: str, category: Optional[str] = None, order_type: str = "taker") -> float:
    """Return the appropriate fee for a platform/category/order_type combination."""
    fees = PLATFORM_FEES.get(platform, {})

    if order_type == "maker" and "maker" in fees:
        return fees["maker"]

    if category and category in fees:
        return fees[category]

    return fees.get("default", 0.01)


class ArbitrageDetector:
    def __init__(self, min_profit_margin: float = 0.005):
        # Default fees (taker) used for near-miss calculations
        self.poly_fee = 0.0075   # sports default (most common)
        self.kal_fee = 0.020
        self.gem_fee = 0.0175
        self.min_profit_margin = min_profit_margin

    def detect_opportunities(
        self,
        matches: List[Tuple[Market, Market]],
        platform_a: str = "polymarket",
        platform_b: str = "kalshi",
    ) -> List[ArbitrageOpportunity]:
        """
        Analyze matched markets for pricing discrepancies with realistic fees.
        Checks both directions: (buy YES on A + buy NO on B) and (buy YES on B + buy NO on A).
        """
        opportunities = []

        for market_a, market_b in matches:
            # Get category-specific fees
            fee_a = get_fee(platform_a, market_a.category)
            fee_b = get_fee(platform_b, market_b.category)

            # Direction 1: Buy YES on platform A, Buy NO on platform B
            # (covers all outcomes: YES pays $1, NO pays $1 → guaranteed $1)
            cost_1 = market_a.yes_price + market_b.no_price + fee_a + fee_b
            if cost_1 < 1.00:
                profit_1 = 1.00 - cost_1
                if profit_1 >= self.min_profit_margin:
                    opportunities.append(ArbitrageOpportunity(
                        market_a=market_a,
                        market_b=market_b,
                        profit_margin=profit_1,
                        strategy=(
                            f"Buy {platform_a.title()} YES (${market_a.yes_price:.3f}) + "
                            f"Buy {platform_b.title()} NO (${market_b.no_price:.3f}) | "
                            f"Fees: {fee_a:.2%}+{fee_b:.2%} | "
                            f"Net profit: {profit_1:.2%}"
                        ),
                    ))

            # Direction 2: Buy YES on platform B, Buy NO on platform A
            cost_2 = market_b.yes_price + market_a.no_price + fee_a + fee_b
            if cost_2 < 1.00:
                profit_2 = 1.00 - cost_2
                if profit_2 >= self.min_profit_margin:
                    opportunities.append(ArbitrageOpportunity(
                        market_a=market_a,
                        market_b=market_b,
                        profit_margin=profit_2,
                        strategy=(
                            f"Buy {platform_b.title()} YES (${market_b.yes_price:.3f}) + "
                            f"Buy {platform_a.title()} NO (${market_a.no_price:.3f}) | "
                            f"Fees: {fee_a:.2%}+{fee_b:.2%} | "
                            f"Net profit: {profit_2:.2%}"
                        ),
                    ))

        return opportunities

    def compute_roi(
        self,
        market_a: Market,
        market_b: Market,
        platform_a: str,
        platform_b: str,
    ) -> Tuple[float, float, str]:
        """
        Compute the best ROI for a matched pair.
        Returns (best_roi, best_cost, best_strategy_label)
        """
        fee_a = get_fee(platform_a, market_a.category)
        fee_b = get_fee(platform_b, market_b.category)

        cost_1 = market_a.yes_price + market_b.no_price + fee_a + fee_b
        cost_2 = market_b.yes_price + market_a.no_price + fee_a + fee_b

        roi_1 = (1.0 - cost_1) / cost_1
        roi_2 = (1.0 - cost_2) / cost_2

        if roi_1 >= roi_2:
            label = (
                f"YES {platform_a.title()} + NO {platform_b.title()} | "
                f"Cost={cost_1:.4f} | ROI={roi_1:.2%}"
            )
            return roi_1, cost_1, label
        else:
            label = (
                f"YES {platform_b.title()} + NO {platform_a.title()} | "
                f"Cost={cost_2:.4f} | ROI={roi_2:.2%}"
            )
            return roi_2, cost_2, label
