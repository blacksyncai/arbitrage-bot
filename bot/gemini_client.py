"""
Gemini Predictions API Client
Docs: https://docs.gemini.com/prediction-markets/getting-started
Base URL: https://api.gemini.com/v1/prediction-markets
Auth: HMAC-SHA384 signed headers (API key + secret) — only needed for trading
Fees: Maker 1.75% | Taker ~0.63-1.75% depending on price (7% of edge formula)
Available in all 50 US states (CFTC-regulated via Gemini Titan LLC)
"""
import os
import time
import hmac
import hashlib
import base64
import json
import requests
from typing import List, Optional
from .models import Market

BASE_URL = "https://api.gemini.com/v1/prediction-markets"

CATEGORY_MAP = {
    "Sports": "sports",
    "Crypto": "crypto",
    "Politics": "politics",
    "Economics": "economics",
    "Business": "finance",
    "Culture": "culture",
    "Tech": "tech",
    "Commodities": "commodities",
}


class GeminiClient:
    """
    Client for Gemini Predictions API.
    Public endpoints (market data) require no auth.
    Trading endpoints require API key + secret from Gemini account settings.
    """

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.api_secret = os.environ.get("GEMINI_API_SECRET", "")
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------ #
    #  Public market data (no auth required)                               #
    # ------------------------------------------------------------------ #

    def get_active_markets(self, limit: int = 200) -> List[Market]:
        """
        Fetch active prediction markets from Gemini.
        Returns a list of Market objects compatible with the matcher/detector.
        """
        markets = []
        try:
            resp = self.session.get(
                f"{BASE_URL}/events",
                params={"status": "active", "limit": min(limit, 100)},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return markets

        for event in data.get("data", []):
            title = event.get("title", "")
            category_raw = event.get("category", "")
            event_ticker = event.get("ticker", "")
            contracts = event.get("contracts", [])
            event_type = event.get("type", "")
            event_volume = float(event.get("volume", 0) or 0)

            if event_type == "binary" or len(contracts) == 1:
                contract = contracts[0] if contracts else {}
                market = self._parse_contract(
                    question=title,
                    contract=contract,
                    category_raw=category_raw,
                    event_ticker=event_ticker,
                    volume=event_volume,
                )
                if market:
                    markets.append(market)

            elif event_type == "categorical" and len(contracts) >= 2:
                for contract in contracts:
                    team_name = contract.get("label", "")
                    question = f"Will {team_name} win {title}?"
                    market = self._parse_contract(
                        question=question,
                        contract=contract,
                        category_raw=category_raw,
                        event_ticker=event_ticker,
                        volume=event_volume,
                    )
                    if market:
                        markets.append(market)

        return markets

    def _parse_contract(
        self,
        question: str,
        contract: dict,
        category_raw: str,
        event_ticker: str,
        volume: float = 0,
    ) -> Optional[Market]:
        """Parse a single Gemini contract into a Market object."""
        prices = contract.get("prices", {})
        if not prices:
            return None

        best_ask = prices.get("bestAsk")
        best_bid = prices.get("bestBid")

        if best_ask is None or best_bid is None:
            buy_yes = (prices.get("buy") or {}).get("yes")
            sell_yes = (prices.get("sell") or {}).get("yes")
            if buy_yes is None or sell_yes is None:
                return None
            best_ask = float(buy_yes)
            best_bid = float(sell_yes)

        best_ask = float(best_ask)
        best_bid = float(best_bid)
        yes_price = round((best_ask + best_bid) / 2, 4)
        no_price = round(1.0 - yes_price, 4)

        if yes_price <= 0.001 or yes_price >= 0.999:
            return None

        instrument_symbol = contract.get("instrumentSymbol", event_ticker)

        return Market(
            id=instrument_symbol,
            question=question,
            yes_price=yes_price,
            no_price=no_price,
            platform="gemini",
            category=CATEGORY_MAP.get(category_raw, category_raw.lower()),
            volume=volume,
            yes_token_id=instrument_symbol,
            no_token_id=instrument_symbol + "_NO",
        )

    # ------------------------------------------------------------------ #
    #  Authenticated trading endpoints                                     #
    # ------------------------------------------------------------------ #

    def _auth_headers(self, endpoint: str, payload: dict) -> dict:
        """Generate Gemini HMAC-SHA384 authentication headers."""
        if not self.api_key or not self.api_secret:
            raise ValueError("GEMINI_API_KEY and GEMINI_API_SECRET must be set for trading")

        nonce = str(int(time.time() * 1000))
        payload_with_meta = {**payload, "request": endpoint, "nonce": nonce}
        encoded = base64.b64encode(json.dumps(payload_with_meta).encode()).decode()
        signature = hmac.new(
            self.api_secret.encode(),
            encoded.encode(),
            hashlib.sha384,
        ).hexdigest()

        return {
            "X-GEMINI-APIKEY": self.api_key,
            "X-GEMINI-PAYLOAD": encoded,
            "X-GEMINI-SIGNATURE": signature,
        }

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        order_type: str = "limit",
    ) -> dict:
        """Place a prediction market order on Gemini."""
        endpoint = "/v1/prediction-markets/order/new"
        payload = {
            "symbol": symbol,
            "amount": str(quantity),
            "price": str(round(price, 2)),
            "side": side,
            "type": order_type,
        }
        headers = self._auth_headers(endpoint, payload)
        resp = self.session.post(
            f"https://api.gemini.com{endpoint}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by order ID."""
        endpoint = "/v1/prediction-markets/order/cancel"
        payload = {"order_id": order_id}
        headers = self._auth_headers(endpoint, payload)
        resp = self.session.post(
            f"https://api.gemini.com{endpoint}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_positions(self) -> list:
        """Get current open positions."""
        endpoint = "/v1/prediction-markets/positions"
        payload = {}
        headers = self._auth_headers(endpoint, payload)
        resp = self.session.post(
            f"https://api.gemini.com{endpoint}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_active_orders(self) -> list:
        """Get all open orders."""
        endpoint = "/v1/prediction-markets/orders"
        payload = {}
        headers = self._auth_headers(endpoint, payload)
        resp = self.session.post(
            f"https://api.gemini.com{endpoint}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
