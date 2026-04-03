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
