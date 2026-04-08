"""
Polymarket CLOB Trading Wrapper
================================
Handles all order placement, cancellation, and status checks via the
official py-clob-client library.

Authentication requires:
  POLY_PRIVATE_KEY       - Polygon wallet private key (0x...)
  POLY_API_KEY           - from polymarket.com/profile → API Keys
  POLY_API_SECRET        - same
  POLY_API_PASSPHRASE    - same

Run with DRY_RUN=true (default) to simulate without spending real money.
Set DRY_RUN=false to place real orders on-chain.
"""
import os
import time
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

log = logging.getLogger("trader")

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID  = 137   # Polygon Mainnet (80002 = Amoy testnet)


@dataclass
class OrderResult:
    order_id: str
    status: str        # 'live', 'filled', 'cancelled', 'error', 'dry_run'
    price: float
    size: float
    side: str          # 'buy' or 'sell'
    token_id: str
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class PolymarketTrader:
    """
    Thin wrapper around py-clob-client that adds dry-run support,
    clean error handling, and order tracking.

    Dry-run mode (default):
      All orders are simulated in-memory. No network calls to CLOB.
      Switch to live with DRY_RUN=false in your .env.

    Live mode:
      Orders are signed with your private key and submitted to the
      Polymarket CLOB. Fills are near-instant for liquid markets.
    """

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._client = None
        self._fake_balance = 1_000.0  # dry-run simulated USDC balance

        if not dry_run:
            self._client = self._build_client()

    # ------------------------------------------------------------------ #
    #  Client setup                                                        #
    # ------------------------------------------------------------------ #

    def _build_client(self):
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        private_key = os.environ.get("POLY_PRIVATE_KEY", "")
        api_key     = os.environ.get("POLY_API_KEY", "")
        api_secret  = os.environ.get("POLY_API_SECRET", "")
        api_pass    = os.environ.get("POLY_API_PASSPHRASE", "")

        missing = [k for k, v in {
            "POLY_PRIVATE_KEY": private_key,
            "POLY_API_KEY":     api_key,
            "POLY_API_SECRET":  api_secret,
            "POLY_API_PASSPHRASE": api_pass,
        }.items() if not v]

        if missing:
            raise ValueError(
                f"Missing required env vars for live trading: {missing}\n"
                "  Set them in your .env file — see .env.example"
            )

        creds = ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_pass,
        )
        return ClobClient(
            host=CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID,
            creds=creds,
        )

    # ------------------------------------------------------------------ #
    #  Orders                                                              #
    # ------------------------------------------------------------------ #

    def buy(self, token_id: str, price: float, size: float) -> OrderResult:
        """
        Place a limit BUY order.

        Parameters
        ----------
        token_id : CLOB ERC1155 token ID (YES or NO side)
        price    : limit price (0.01 – 0.99)
        size     : number of shares (USDC / price)
        """
        price = round(price, 4)
        size  = round(size, 2)
        log.info(f"BUY token={token_id[:8]}... price={price} size={size}")

        if self.dry_run:
            cost = price * size
            self._fake_balance -= cost
            return OrderResult(
                order_id=f"DRY-{uuid.uuid4().hex[:8].upper()}",
                status="dry_run",
                price=price,
                size=size,
                side="buy",
                token_id=token_id,
            )

        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import BUY
            args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
            resp = self._client.create_and_post_order(args)
            return OrderResult(
                order_id=resp.get("orderID", "unknown"),
                status=resp.get("status", "unknown").lower(),
                price=price,
                size=size,
                side="buy",
                token_id=token_id,
                raw=resp,
            )
        except Exception as e:
            log.error(f"Buy order failed: {e}")
            return OrderResult(
                order_id="ERR",
                status="error",
                price=price,
                size=size,
                side="buy",
                token_id=token_id,
                error=str(e),
            )

    def sell(self, token_id: str, price: float, size: float) -> OrderResult:
        """Place a limit SELL order."""
        price = round(price, 4)
        size  = round(size, 2)
        log.info(f"SELL token={token_id[:8]}... price={price} size={size}")

        if self.dry_run:
            proceeds = price * size
            self._fake_balance += proceeds
            return OrderResult(
                order_id=f"DRY-{uuid.uuid4().hex[:8].upper()}",
                status="dry_run",
                price=price,
                size=size,
                side="sell",
                token_id=token_id,
            )

        try:
            from py_clob_client.clob_types import OrderArgs
            from py_clob_client.order_builder.constants import SELL
            args = OrderArgs(price=price, size=size, side=SELL, token_id=token_id)
            resp = self._client.create_and_post_order(args)
            return OrderResult(
                order_id=resp.get("orderID", "unknown"),
                status=resp.get("status", "unknown").lower(),
                price=price,
                size=size,
                side="sell",
                token_id=token_id,
                raw=resp,
            )
        except Exception as e:
            log.error(f"Sell order failed: {e}")
            return OrderResult(
                order_id="ERR",
                status="error",
                price=price,
                size=size,
                side="sell",
                token_id=token_id,
                error=str(e),
            )

    def cancel(self, order_id: str) -> bool:
        if self.dry_run or order_id.startswith("DRY-"):
            return True
        try:
            self._client.cancel(order_id)
            return True
        except Exception as e:
            log.warning(f"Cancel failed for {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> str:
        """Return order status string: 'live', 'filled', 'cancelled', etc."""
        if self.dry_run or order_id.startswith("DRY-"):
            # Dry-run: simulate instant fill after 1 check
            return "filled"

        try:
            resp = self._client.get_order(order_id)
            return resp.get("status", "unknown").lower()
        except Exception as e:
            log.warning(f"get_order_status({order_id}) failed: {e}")
            return "error"

    # ------------------------------------------------------------------ #
    #  Account                                                             #
    # ------------------------------------------------------------------ #

    def get_usdc_balance(self) -> float:
        """Return available USDC balance."""
        if self.dry_run:
            return round(self._fake_balance, 2)
        try:
            resp = self._client.get_balance()
            # Balance is in wei (6 decimals for USDC on Polygon)
            return float(resp) / 1e6
        except Exception as e:
            log.warning(f"get_balance failed: {e}")
            return 0.0

    def get_open_orders(self) -> list:
        """Return list of open orders from the exchange."""
        if self.dry_run:
            return []
        try:
            return self._client.get_orders() or []
        except Exception:
            return []
