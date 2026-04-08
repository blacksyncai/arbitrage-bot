"""
Position Manager — tracks paper/live scalp positions and their P&L.

Usage flow:
  1. User sees a SkewedMarket with positive momentum in the terminal
  2. Types: buy 3 YES 0.05 200 10
     (market #3, buy YES at $0.05, 200 shares, 10% ROI target)
  3. PositionManager records the position
  4. On every price update, the manager checks for target hits
  5. When ROI >= target, the position status flips to 'target_hit'
  6. User types: sell <pos_id> to close the position and log the gain

Positions are persisted to a JSON file so they survive restarts.
"""
import json
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Position


class PositionManager:
    def __init__(self, save_file: Optional[Path] = None):
        self._positions: Dict[str, Position] = {}
        self._closed: List[Position] = []  # last 50 closed positions
        self.save_file = save_file
        if save_file and Path(save_file).exists():
            self._load()

    # ------------------------------------------------------------------ #
    #  Position lifecycle                                                  #
    # ------------------------------------------------------------------ #

    def add(
        self,
        market_id: str,
        market_question: str,
        side: str,
        entry_price: float,
        shares: int,
        target_roi: float = 0.10,
    ) -> Position:
        """
        Open a new position.

        Parameters
        ----------
        market_id       : Polymarket market ID
        market_question : Short question text for display
        side            : 'YES' or 'NO'
        entry_price     : Price paid per share (e.g. 0.05 for 5¢)
        shares          : Number of shares
        target_roi      : Exit target as a fraction (0.10 = 10%)
        """
        pos_id = uuid.uuid4().hex[:6].upper()
        pos = Position(
            pos_id=pos_id,
            market_id=market_id,
            market_question=market_question[:60],
            side=side.upper(),
            entry_price=entry_price,
            shares=shares,
            target_roi=target_roi,
            current_price=entry_price,
            opened_at=time.time(),
            status='open',
        )
        self._positions[pos_id] = pos
        self._save()
        return pos

    def close(self, pos_id: str) -> Optional[Position]:
        """
        Close a position and move it to the closed log.
        Returns the closed Position or None if not found.
        """
        pos_id = pos_id.upper()
        pos = self._positions.pop(pos_id, None)
        if pos:
            pos.status = 'closed'
            self._closed.append(pos)
            if len(self._closed) > 50:
                self._closed = self._closed[-50:]
            self._save()
        return pos

    # ------------------------------------------------------------------ #
    #  Price updates                                                       #
    # ------------------------------------------------------------------ #

    def update_prices(self, market_prices: Dict[str, Tuple[float, float]]) -> List[Position]:
        """
        Update current prices for all open positions.

        market_prices: { market_id: (yes_price, no_price) }

        Returns a list of positions that JUST crossed their target ROI
        (newly flipped to 'target_hit').
        """
        newly_hit: List[Position] = []
        for pos in self._positions.values():
            if pos.status == 'closed':
                continue
            prices = market_prices.get(pos.market_id)
            if prices is None:
                continue
            yes_p, no_p = prices
            pos.current_price = yes_p if pos.side == 'YES' else no_p

            was_open = pos.status == 'open'
            if pos.target_hit and was_open:
                pos.status = 'target_hit'
                newly_hit.append(pos)

        if newly_hit:
            self._save()
        return newly_hit

    # ------------------------------------------------------------------ #
    #  Queries                                                             #
    # ------------------------------------------------------------------ #

    def get_open(self) -> List[Position]:
        """All positions that are open or at target (not closed)."""
        return [p for p in self._positions.values() if p.status != 'closed']

    def get_target_hits(self) -> List[Position]:
        """Positions that have reached their ROI target — EXIT NOW."""
        return [p for p in self._positions.values() if p.status == 'target_hit']

    def get_closed(self, n: int = 10) -> List[Position]:
        """Last N closed positions for the trade log."""
        return self._closed[-n:]

    def total_pnl(self) -> float:
        """Sum P&L across all open positions."""
        return sum(p.pnl for p in self.get_open())

    def total_cost(self) -> float:
        """Total capital in open positions."""
        return sum(p.cost for p in self.get_open())

    # ------------------------------------------------------------------ #
    #  Persistence                                                         #
    # ------------------------------------------------------------------ #

    def _save(self) -> None:
        if not self.save_file:
            return
        data = {
            'positions': [self._to_dict(p) for p in self._positions.values()],
            'closed': [self._to_dict(p) for p in self._closed],
        }
        Path(self.save_file).write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        try:
            data = json.loads(Path(self.save_file).read_text())
            for d in data.get('positions', []):
                p = self._from_dict(d)
                self._positions[p.pos_id] = p
            for d in data.get('closed', []):
                self._closed.append(self._from_dict(d))
        except Exception:
            pass  # corrupt/empty file — start fresh

    @staticmethod
    def _to_dict(p: Position) -> dict:
        return {
            'pos_id': p.pos_id,
            'market_id': p.market_id,
            'market_question': p.market_question,
            'side': p.side,
            'entry_price': p.entry_price,
            'shares': p.shares,
            'target_roi': p.target_roi,
            'current_price': p.current_price,
            'opened_at': p.opened_at,
            'status': p.status,
        }

    @staticmethod
    def _from_dict(d: dict) -> Position:
        return Position(
            pos_id=d['pos_id'],
            market_id=d['market_id'],
            market_question=d['market_question'],
            side=d['side'],
            entry_price=float(d['entry_price']),
            shares=int(d['shares']),
            target_roi=float(d['target_roi']),
            current_price=float(d['current_price']),
            opened_at=float(d['opened_at']),
            status=d['status'],
        )
