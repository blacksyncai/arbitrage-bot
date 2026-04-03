import requests
from typing import List, Dict, Any
from .models import Market


# Kalshi series tickers that produce clean single-question binary markets
# These are the series that overlap with Polymarket topics
SPORTS_SERIES = [
    ('KXNBA', 'basketball'),   # NBA Finals (team winner)
    ('KXNHL', 'hockey'),       # NHL Stanley Cup
    ('KXMLB', 'baseball'),     # MLB World Series / Championship
]

# Kalshi event tickers for non-sports markets (world/political)
# These are fetched individually and are fast (single API call each)
WORLD_EVENT_TICKERS = [
    'KXELONMARS-99',
    'KXNEWPOPE-70',
    'KXHUMANSMARS-50',
    'KXMARSRAIL-50',
    'KXSUPERVOLCANO-50',
    'KXCLIMATE2C-50',
    'KXROBOTMARS-50',
    'KXJOHNNYDEPP-50',
    'KXBONDJAMES-50',
    'KXOAIANTHROPIC-50',
    'KXRAMPBREX-50',
    'KXDEELRIPPLING-50',
    'KXFUSION-50',
    'KXEARTHQUAKECALIFORNIA-50',
    'KXHUMANOIDMARS-50',
    'KXPORTLANDTRAILBLAZERS-50',
]


class KalshiClient:
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self):
        self.session = requests.Session()

    def get_active_markets(self, limit: int = 100) -> List[Market]:
        """Fetch active single-question binary markets from Kalshi.
        
        Uses two fast approaches:
        1. Sports series (NBA, NHL, MLB) - 3 API calls, ~90 clean markets
        2. Known world/political event tickers - direct event market fetch
        """
        markets = []
        seen_ids = set()

        # --- Sports series (fast: 3 API calls) ---
        for series, sport_category in SPORTS_SERIES:
            series_markets = self._fetch_by_series(series, limit=50, category=sport_category)
            for m in series_markets:
                if m.id not in seen_ids:
                    markets.append(m)
                    seen_ids.add(m.id)

        # --- World/political events (fetch markets for known events) ---
        world_markets = self._fetch_world_markets()
        for m in world_markets:
            if m.id not in seen_ids:
                markets.append(m)
                seen_ids.add(m.id)

        return markets[:limit]

    def _fetch_by_series(self, series_ticker: str, limit: int = 50, category: str = None) -> List[Market]:
        """Fetch markets from a specific Kalshi series (single API call)."""
        url = f"{self.BASE_URL}/markets"
        params = {
            "series_ticker": series_ticker,
            "status": "open",
            "limit": limit
        }
        try:
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            raw = response.json().get('markets', [])
            return self._parse_markets(raw, category=category)
        except Exception:
            return []

    def _fetch_world_markets(self) -> List[Market]:
        """Fetch markets for known world/political event tickers."""
        markets = []
        for event_ticker in WORLD_EVENT_TICKERS:
            url = f"{self.BASE_URL}/markets"
            params = {
                "event_ticker": event_ticker,
                "status": "open",
                "limit": 10
            }
            try:
                response = self.session.get(url, params=params, timeout=8)
                if response.status_code != 200:
                    continue
                raw = response.json().get('markets', [])
                parsed = self._parse_markets(raw, category='politics')
                markets.extend(parsed)
            except Exception:
                continue
        return markets

    def _parse_markets(self, raw_markets: List[Dict[str, Any]], category: str = None) -> List[Market]:
        """Convert raw Kalshi market data to Market objects."""
        markets = []
        for data in raw_markets:
            title = data.get('title', '')

            # Skip multi-leg markets (contain commas)
            if ',' in title:
                continue
            if title.lower().startswith('yes ') or title.lower().startswith('no '):
                continue

            # Get prices - prefer ask price for realistic execution cost
            yes_bid = float(data.get('yes_bid_dollars', 0) or 0)
            no_bid = float(data.get('no_bid_dollars', 0) or 0)
            yes_ask = float(data.get('yes_ask_dollars', 0) or 0)
            no_ask = float(data.get('no_ask_dollars', 0) or 0)

            yes_price = yes_ask if yes_ask > 0 else yes_bid
            no_price = no_ask if no_ask > 0 else no_bid

            # Skip if no pricing at all
            if yes_price == 0 and no_price == 0:
                continue

            markets.append(Market(
                id=data['ticker'],
                platform='kalshi',
                question=title,
                yes_price=yes_price,
                no_price=no_price,
                ticker=data['ticker'],
                end_date=data.get('expiration_time'),
                volume=float(data.get('volume_fp', 0) or 0),
                category=category,
            ))

        return markets

    def get_order_book(self, ticker: str) -> Dict[str, Any]:
        """Fetch order book for a specific market from Kalshi API."""
        url = f"{self.BASE_URL}/markets/{ticker}/orderbook"
        response = self.session.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
