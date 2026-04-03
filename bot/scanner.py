"""
Multi-platform market scanner.
Scans all pairs: Polymarket<->Kalshi, Polymarket<->Gemini, Kalshi<->Gemini
"""
from typing import List, Tuple, Dict
from .polymarket_client import PolymarketClient
from .kalshi_client import KalshiClient
from .gemini_client import GeminiClient
from .matcher import MarketMatcher
from .detector import ArbitrageDetector
from .models import ArbitrageOpportunity, Market


class MarketScanner:
    def __init__(
        self,
        poly_client: PolymarketClient,
        kal_client: KalshiClient,
        gem_client: GeminiClient,
        matcher: MarketMatcher,
        detector: ArbitrageDetector,
    ):
        self.poly_client = poly_client
        self.kal_client = kal_client
        self.gem_client = gem_client
        self.matcher = matcher
        self.detector = detector

    def scan(self) -> Tuple[List[ArbitrageOpportunity], Dict]:
        """
        Perform a complete 3-platform scan for arbitrage opportunities.
        Returns (opportunities, stats_dict)
        """
        # 1. Fetch active markets from all platforms
        poly_markets = self.poly_client.get_active_markets(limit=200)
        kal_markets = self.kal_client.get_active_markets(limit=200)
        gem_markets = self.gem_client.get_active_markets(limit=200)

        stats = {
            "poly_count": len(poly_markets),
            "kal_count": len(kal_markets),
            "gem_count": len(gem_markets),
            "matches": {},
            "opportunities": [],
        }

        all_opportunities = []

        # 2. Scan all platform pairs
        pairs = [
            ("polymarket", "kalshi", poly_markets, kal_markets),
            ("polymarket", "gemini", poly_markets, gem_markets),
            ("kalshi", "gemini", kal_markets, gem_markets),
        ]

        for platform_a, platform_b, markets_a, markets_b in pairs:
            pair_key = f"{platform_a}<->{platform_b}"
            matches = self.matcher.find_matches(markets_a, markets_b)
            opportunities = self.detector.detect_opportunities(matches, platform_a, platform_b)
            stats["matches"][pair_key] = len(matches)
            all_opportunities.extend(opportunities)

        # Sort by profit margin descending
        all_opportunities.sort(key=lambda x: x.profit_margin, reverse=True)
        stats["opportunities"] = all_opportunities

        return all_opportunities, stats

    def get_near_misses(
        self,
        poly_markets: List[Market],
        kal_markets: List[Market],
        gem_markets: List[Market],
        top_n: int = 10,
    ) -> List[Tuple[float, Market, Market, str]]:
        """
        Return the closest-to-arbitrage pairs across all platform combinations.
        Returns list of (cost, market_a, market_b, pair_label) sorted by cost ascending.
        """
        near = []

        pairs = [
            ("Poly<->Kal", poly_markets, kal_markets,
             self.detector.poly_fee, self.detector.kal_fee),
            ("Poly<->Gem", poly_markets, gem_markets,
             self.detector.poly_fee, self.detector.gem_fee),
            ("Kal<->Gem", kal_markets, gem_markets,
             self.detector.kal_fee, self.detector.gem_fee),
        ]

        for label, markets_a, markets_b, fee_a, fee_b in pairs:
            matches = self.matcher.find_matches(markets_a, markets_b)
            for ma, mb in matches:
                c1 = ma.yes_price + mb.no_price + fee_a + fee_b
                c2 = mb.yes_price + ma.no_price + fee_a + fee_b
                best = min(c1, c2)
                near.append((best, ma, mb, label))

        near.sort(key=lambda x: x[0])
        return near[:top_n]
