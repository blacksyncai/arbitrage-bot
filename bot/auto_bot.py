"""
Automated Sports Scalper — Entry/Exit Engine
=============================================
Runs the full trading loop:
  1. Fetch Polymarket sports markets
  2. Find heavily skewed odds (cheap side ≤ max_entry_price)
  3. Enter when momentum signal fires
  4. Exit at target ROI, stop-loss, or time limit — never hold to resolution

Default risk config (all tunable via CLI flags or .env):
  MAX_ENTRY_PRICE    0.20   Only buy cheap sides ≤ 20¢
  MIN_MOMENTUM       0.003  Cheap side must be rising (+0.3¢ over 5 scans)
  MIN_VOLUME        5000    Min market volume in USD (liquidity filter)
  MAX_POSITION_USDC   50    Max spend per trade
  MAX_POSITIONS        5    Max concurrent open trades
  TARGET_ROI          0.10  Sell at +10% ROI
  STOP_LOSS           0.50  Cut at -50% (cheap contracts are volatile)
  MAX_HOLD_MINUTES    45    Force-exit after 45 minutes regardless
"""
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import Market, SkewedMarket
from .polymarket_client import PolymarketClient
from .price_tracker import PriceTracker
from .skew_scanner import SkewScanner, SPORTS_CATEGORIES
from .trader import PolymarketTrader, OrderResult

log = logging.getLogger("auto_bot")


# ─── Trade record ─────────────────────────────────────────────────────────────

@dataclass
class Trade:
    trade_id: str
    market_id: str
    market_question: str
    cheap_side: str       # 'YES' or 'NO'
    token_id: str
    entry_price: float
    size: float           # shares
    target_roi: float
    stop_loss: float
    max_hold_minutes: float
    entry_order_id: str
    opened_at: float = field(default_factory=time.time)
    current_price: float = 0.0
    status: str = 'pending_fill'   # pending_fill | open | closed
    close_reason: str = ''
    exit_price: float = 0.0
    pnl: float = 0.0

    @property
    def roi(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def target_price(self) -> float:
        return round(self.entry_price * (1.0 + self.target_roi), 4)

    @property
    def stop_price(self) -> float:
        return round(self.entry_price * (1.0 - self.stop_loss), 4)

    @property
    def hold_minutes(self) -> float:
        return (time.time() - self.opened_at) / 60.0

    @property
    def cost(self) -> float:
        return self.entry_price * self.size


# ─── Bot config ───────────────────────────────────────────────────────────────

@dataclass
class BotConfig:
    max_entry_price: float   = 0.20   # only buy cheap sides ≤ this price
    min_momentum: float      = 0.003  # minimum +Δ to trigger entry
    min_volume: float        = 5_000  # USD volume filter
    max_position_usdc: float = 50.0   # max $ per trade
    max_positions: int       = 5      # max concurrent trades
    target_roi: float        = 0.10   # exit target (10%)
    stop_loss: float         = 0.50   # cut loss at -50%
    max_hold_minutes: float  = 45.0   # force exit after N minutes
    scan_interval: int       = 15     # seconds between scans
    fill_timeout_sec: int    = 90     # cancel unfilled buys after N seconds


# ─── Bot ──────────────────────────────────────────────────────────────────────

class AutoBot:
    """
    The core trading engine.

    Usage:
        bot = AutoBot(config=BotConfig(), trader=PolymarketTrader(dry_run=True))
        while True:
            status = bot.run_once()
            display(status)
            time.sleep(config.scan_interval)
    """

    def __init__(self, config: BotConfig, trader: PolymarketTrader):
        self.config  = config
        self.trader  = trader

        self.poly_client   = PolymarketClient()
        self.price_tracker = PriceTracker(max_snapshots=30)
        self.scanner       = SkewScanner(
            max_cheap_price=config.max_entry_price,
            min_volume=config.min_volume,
            sports_only=True,
        )

        # Active trades: market_id → Trade
        self.trades: Dict[str, Trade] = {}
        # Pending buy orders: order_id → Trade (waiting for fill)
        self.pending: Dict[str, Trade] = {}
        # Completed trades log
        self.closed: List[Trade] = []

        # Session stats
        self.stats = {
            'scans':   0,
            'entered': 0,
            'exits':   0,
            'wins':    0,
            'losses':  0,
            'pnl':     0.0,
        }

        self._last_markets: List[Market] = []
        self._last_skewed:  List[SkewedMarket] = []
        self._log: List[str] = []   # recent event log for display

    # ------------------------------------------------------------------ #
    #  Main cycle                                                          #
    # ------------------------------------------------------------------ #

    def run_once(self) -> dict:
        """
        Execute one full scan + action cycle.
        Returns a status dict for the display layer.
        """
        try:
            # 1. Fetch fresh market data
            markets = self.poly_client.get_active_markets(limit=300)
            self._last_markets = markets
            self.price_tracker.update(markets)

            # Market price lookup: market_id → (yes_price, no_price)
            prices: Dict[str, Tuple[float, float]] = {
                m.id: (m.yes_price, m.no_price) for m in markets
            }

            # 2. Check pending buys for fills / timeouts
            self._check_pending(prices)

            # 3. Check exits for all open trades
            self._check_exits(prices)

            # 4. Scan for new entry signals
            skewed = self.scanner.scan(markets, self.price_tracker)
            self._last_skewed = skewed

            open_count = len(self.trades) + len(self.pending)
            if open_count < self.config.max_positions:
                for sm in skewed:
                    if open_count >= self.config.max_positions:
                        break
                    if self._should_enter(sm):
                        self._enter(sm)
                        open_count += 1

            self.stats['scans'] += 1

        except Exception as e:
            self._emit(f"SCAN ERROR: {e}", level="error")
            log.exception("run_once error")

        return self._build_status()

    # ------------------------------------------------------------------ #
    #  Entry logic                                                         #
    # ------------------------------------------------------------------ #

    def _should_enter(self, sm: SkewedMarket) -> bool:
        mid = sm.market.id

        # Already trading this market (open or pending fill)
        if mid in self.trades:
            return False
        if any(t.market_id == mid for t in self.pending.values()):
            return False

        # Momentum must be positive
        if sm.momentum < self.config.min_momentum:
            return False

        # Cheap price sanity check
        if sm.cheap_price <= 0.001 or sm.cheap_price > self.config.max_entry_price:
            return False

        # Need a valid token ID to trade
        if not self._get_token_id(sm):
            return False

        # Check we have enough USDC
        balance = self.trader.get_usdc_balance()
        if balance < self.config.max_position_usdc * 0.5:
            self._emit(f"Low balance (${balance:.2f}), skipping entry", level="warn")
            return False

        return True

    def _enter(self, sm: SkewedMarket) -> None:
        """Place a limit buy on the cheap side."""
        # Slightly aggressive price to help fill (add 0.5% above mid)
        entry_price = round(min(sm.cheap_price * 1.005, 0.97), 4)
        size = round(self.config.max_position_usdc / entry_price, 2)
        if size < 1.0:
            return

        token_id = self._get_token_id(sm)
        if not token_id:
            return

        result: OrderResult = self.trader.buy(
            token_id=token_id,
            price=entry_price,
            size=size,
        )

        if result.status in ("error",):
            self._emit(
                f"BUY FAILED [{sm.market.question[:30]}]: {result.error}",
                level="error",
            )
            return

        trade = Trade(
            trade_id=result.order_id[:8],
            market_id=sm.market.id,
            market_question=sm.market.question[:60],
            cheap_side=sm.cheap_side,
            token_id=token_id,
            entry_price=entry_price,
            size=size,
            target_roi=self.config.target_roi,
            stop_loss=self.config.stop_loss,
            max_hold_minutes=self.config.max_hold_minutes,
            entry_order_id=result.order_id,
            current_price=entry_price,
            status='pending_fill',
        )

        self.pending[result.order_id] = trade
        cost = entry_price * size
        self._emit(
            f"BUY {sm.cheap_side} [{trade.trade_id}] "
            f"{sm.market.question[:35]}... "
            f"@ ${entry_price:.3f} × {size:.0f}sh = ${cost:.2f} "
            f"| target ${trade.target_price:.3f}",
            level="entry",
        )

    # ------------------------------------------------------------------ #
    #  Fill checks                                                         #
    # ------------------------------------------------------------------ #

    def _check_pending(self, prices: Dict[str, Tuple[float, float]]) -> None:
        """Poll pending buy orders; promote filled ones to open trades."""
        expired_order_ids = []

        for order_id, trade in self.pending.items():
            # Timeout: cancel unfilled orders
            if (time.time() - trade.opened_at) > self.config.fill_timeout_sec:
                self.trader.cancel(order_id)
                expired_order_ids.append(order_id)
                self._emit(
                    f"CANCEL (timeout) [{trade.trade_id}] {trade.market_question[:30]}",
                    level="warn",
                )
                continue

            status = self.trader.get_order_status(order_id)

            if status in ("filled", "dry_run"):
                trade.status = 'open'
                trade.opened_at = time.time()  # reset hold timer on fill
                self.trades[trade.market_id] = trade
                expired_order_ids.append(order_id)
                self.stats['entered'] += 1
                self._emit(
                    f"FILLED [{trade.trade_id}] {trade.market_question[:35]}...",
                    level="entry",
                )
            elif status in ("cancelled", "error"):
                expired_order_ids.append(order_id)

        for oid in expired_order_ids:
            self.pending.pop(oid, None)

    # ------------------------------------------------------------------ #
    #  Exit logic                                                          #
    # ------------------------------------------------------------------ #

    def _check_exits(self, prices: Dict[str, Tuple[float, float]]) -> None:
        """Update prices and exit any trade that hits a condition."""
        to_close: List[Tuple[str, Trade, str]] = []

        for mid, trade in self.trades.items():
            pair = prices.get(mid)
            if pair is None:
                continue
            yes_p, no_p = pair
            trade.current_price = yes_p if trade.cheap_side == 'YES' else no_p

            reason = self._exit_reason(trade)
            if reason:
                to_close.append((mid, trade, reason))

        for mid, trade, reason in to_close:
            self._exit(trade, reason)
            del self.trades[mid]

    def _exit_reason(self, trade: Trade) -> Optional[str]:
        roi = trade.roi
        if roi >= trade.target_roi:
            return f"TARGET +{roi:.1%}"
        if roi <= -trade.stop_loss:
            return f"STOP {roi:.1%}"
        if trade.hold_minutes >= trade.max_hold_minutes:
            return f"TIME {trade.hold_minutes:.0f}min"
        return None

    def _exit(self, trade: Trade, reason: str) -> None:
        """Place a limit sell and record the closed trade."""
        # Slightly passive sell (we're fine with a tiny slip on exit)
        sell_price = round(max(trade.current_price * 0.99, 0.001), 4)

        result = self.trader.sell(
            token_id=trade.token_id,
            price=sell_price,
            size=trade.size,
        )

        pnl  = (sell_price - trade.entry_price) * trade.size
        roi  = (sell_price - trade.entry_price) / trade.entry_price

        trade.status      = 'closed'
        trade.close_reason = reason
        trade.exit_price  = sell_price
        trade.pnl         = pnl

        self.closed.append(trade)
        if len(self.closed) > 100:
            self.closed = self.closed[-100:]

        self.stats['exits'] += 1
        self.stats['pnl']   += pnl
        if pnl >= 0:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1

        sign  = "+" if pnl >= 0 else ""
        level = "win" if pnl >= 0 else "loss"
        self._emit(
            f"EXIT [{trade.trade_id}] {trade.market_question[:30]}... "
            f"| {reason} | ROI {roi:+.1%} | P&L {sign}${pnl:.2f}",
            level=level,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _get_token_id(self, sm: SkewedMarket) -> Optional[str]:
        """Return the CLOB ERC1155 token ID for the cheap side."""
        ids = sm.market.clob_token_ids
        if not ids or len(ids) < 2:
            return None
        return ids[0] if sm.cheap_side == 'YES' else ids[1]

    def _emit(self, msg: str, level: str = "info") -> None:
        ts = time.strftime('%H:%M:%S')
        self._log.append(f"[{ts}] {msg}")
        if len(self._log) > 50:
            self._log = self._log[-50:]
        getattr(log, level if level in ("info","warning","error","warn") else "info")(msg)

    def _build_status(self) -> dict:
        return {
            'scan_num':    self.stats['scans'],
            'last_scan':   time.strftime('%H:%M:%S'),
            'balance':     self.trader.get_usdc_balance(),
            'markets':     self._last_markets,
            'skewed':      self._last_skewed,
            'open_trades': list(self.trades.values()),
            'pending':     list(self.pending.values()),
            'closed':      self.closed[-10:],
            'stats':       dict(self.stats),
            'log':         list(self._log[-15:]),
        }
