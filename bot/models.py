import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Market:
    id: str
    platform: str  # 'polymarket', 'kalshi', or 'gemini'
    question: str
    yes_price: float
    no_price: float
    clob_token_ids: Optional[List[str]] = None  # Polymarket specific
    ticker: Optional[str] = None  # Kalshi specific
    end_date: Optional[str] = None
    volume: Optional[float] = None
    category: Optional[str] = None  # sports, politics, crypto, etc.
    yes_token_id: Optional[str] = None  # generic order token (Gemini instrumentSymbol)
    no_token_id: Optional[str] = None


@dataclass
class ArbitrageOpportunity:
    market_a: Market
    market_b: Market
    profit_margin: float
    strategy: str  # e.g., 'Buy Polymarket YES, Buy Kalshi NO'


# ── Sports Scalper models ─────────────────────────────────────────────────────

@dataclass
class PriceSnapshot:
    """A single price observation at a point in time."""
    timestamp: float
    yes_price: float
    no_price: float


@dataclass
class SkewedMarket:
    """
    A market where one side is very cheap (e.g. 5¢ YES vs 95¢ NO).
    We buy the cheap side and exit on a small upward move.

    Example: buy YES at $0.05, exit at $0.06 → 20% ROI
             buy YES at $0.01, exit at $0.05 → 5x (400% ROI)
    """
    market: Market
    cheap_side: str          # 'YES' or 'NO'
    cheap_price: float       # current price of the cheap side (e.g. 0.05)
    expensive_price: float   # current price of the expensive side (e.g. 0.95)
    momentum: float          # price change of cheap side vs N scans ago (+/-)
    last_updated: float      # unix timestamp of last price update
    display_num: int = 0     # sequential number shown in terminal (for commands)

    @property
    def skew_ratio(self) -> float:
        """e.g. 19.0 for a 5¢/95¢ market (expensive/cheap)."""
        if self.cheap_price <= 0:
            return 0.0
        return round(self.expensive_price / self.cheap_price, 1)

    def roi_at_price(self, new_price: float) -> float:
        """ROI% if the cheap side moves to new_price."""
        if self.cheap_price <= 0:
            return 0.0
        return (new_price - self.cheap_price) / self.cheap_price

    def price_for_roi(self, roi: float) -> float:
        """What price the cheap side needs for the given ROI target."""
        return self.cheap_price * (1.0 + roi)


@dataclass
class Position:
    """
    A tracked (paper or live) position in a skewed market.

    P&L tracks ROI on the cheap side only — we never bet the expensive side.
    """
    pos_id: str
    market_id: str
    market_question: str
    side: str            # 'YES' or 'NO'
    entry_price: float   # price paid per share (e.g. 0.05)
    shares: int          # number of shares bought
    target_roi: float    # exit target, e.g. 0.10 for 10%
    current_price: float = 0.0
    opened_at: float = 0.0
    status: str = 'open'  # 'open' | 'target_hit' | 'closed'

    def __post_init__(self):
        if self.opened_at == 0.0:
            self.opened_at = time.time()
        if self.current_price == 0.0:
            self.current_price = self.entry_price

    @property
    def cost(self) -> float:
        """Total capital deployed."""
        return self.entry_price * self.shares

    @property
    def current_value(self) -> float:
        return self.current_price * self.shares

    @property
    def roi(self) -> float:
        """Current ROI as a fraction (e.g. 0.20 = 20%)."""
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def pnl(self) -> float:
        """Absolute P&L in dollars."""
        return (self.current_price - self.entry_price) * self.shares

    @property
    def target_hit(self) -> bool:
        return self.roi >= self.target_roi
