"""
Price history tracker for the sports scalper.

Keeps a rolling window of PriceSnapshot objects per market and exposes
momentum helpers so the scanner can rank markets by price acceleration
on the cheap side.
"""
import time
from collections import deque
from typing import Dict, List, Optional, Deque

from .models import Market, PriceSnapshot


class PriceTracker:
    """
    Stores rolling price history for each market and computes momentum.

    momentum = (cheap_side_price_now) - (cheap_side_price N snapshots ago)

    A positive momentum means the cheap side is rising — exactly what we
    want to see before entering a scalp position.
    """

    def __init__(self, max_snapshots: int = 30):
        self.max_snapshots = max_snapshots
        # market_id -> circular buffer of PriceSnapshot
        self._history: Dict[str, Deque[PriceSnapshot]] = {}

    # ------------------------------------------------------------------ #
    #  Ingestion                                                           #
    # ------------------------------------------------------------------ #

    def update(self, markets: List[Market]) -> None:
        """Record a new price snapshot for every market in the list."""
        ts = time.time()
        for market in markets:
            if market.id not in self._history:
                self._history[market.id] = deque(maxlen=self.max_snapshots)
            snap = PriceSnapshot(
                timestamp=ts,
                yes_price=market.yes_price,
                no_price=market.no_price,
            )
            self._history[market.id].append(snap)

    # ------------------------------------------------------------------ #
    #  Queries                                                             #
    # ------------------------------------------------------------------ #

    def get_momentum(self, market_id: str, side: str, lookback: int = 5) -> float:
        """
        Return the change in `side` price vs `lookback` snapshots ago.

        Positive  → price rising  (bullish for a long position)
        Negative  → price falling (bearish)
        Zero      → new / no history yet
        """
        history = self._history.get(market_id)
        if not history or len(history) < 2:
            return 0.0

        buf = list(history)
        current = buf[-1]
        past = buf[-min(lookback, len(buf))]

        if side.upper() == "YES":
            return round(current.yes_price - past.yes_price, 4)
        else:
            return round(current.no_price - past.no_price, 4)

    def get_latest(self, market_id: str) -> Optional[PriceSnapshot]:
        """Return the most recent snapshot for a market, or None."""
        history = self._history.get(market_id)
        if not history:
            return None
        return history[-1]

    def get_history(self, market_id: str) -> List[PriceSnapshot]:
        """Return the full price history list for a market (oldest first)."""
        return list(self._history.get(market_id, []))

    def snapshot_count(self, market_id: str) -> int:
        return len(self._history.get(market_id, []))

    def all_market_ids(self) -> List[str]:
        return list(self._history.keys())

    # ------------------------------------------------------------------ #
    #  Signal helpers                                                      #
    # ------------------------------------------------------------------ #

    def is_pumping(self, market_id: str, side: str, threshold: float = 0.005) -> bool:
        """
        True if the cheap side gained ≥ threshold in the last 5 snapshots.
        Default threshold of 0.5¢ catches meaningful moves without noise.
        """
        return self.get_momentum(market_id, side, lookback=5) >= threshold

    def is_fading(self, market_id: str, side: str, threshold: float = 0.005) -> bool:
        """True if the cheap side lost ≥ threshold — short-term bearish."""
        return self.get_momentum(market_id, side, lookback=5) <= -threshold

    def momentum_label(self, market_id: str, side: str) -> str:
        """
        Human-readable momentum signal for display in the terminal.
        Returns one of: 'PUMP', 'rising', 'fading', 'flat', ''
        """
        m = self.get_momentum(market_id, side, lookback=5)
        if m >= 0.010:
            return "PUMP"
        if m >= 0.004:
            return "rising"
        if m <= -0.010:
            return "DUMP"
        if m <= -0.004:
            return "fading"
        return "flat"
