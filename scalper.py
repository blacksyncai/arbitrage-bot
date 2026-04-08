#!/usr/bin/env python3
"""
Polymarket Sports Scalper Terminal
===================================
Finds heavily skewed sports markets (1-30¢ cheap side) and tracks
momentum for quick-exit scalp trades.

Strategy:
  • Buy the cheap side of a skewed market (e.g. 5¢ YES on a 5/95 market)
  • Exit at 6-20% ROI — never hold to resolution
  • Key insight: 1¢ → 5¢ = 5x your money (400% ROI)

Usage:
  python scalper.py [--threshold 0.30] [--interval 15] [--min-vol 500]

In-terminal commands (type and press Enter):
  buy <#> <Y|N> <price> <shares> [target%]   Track a position
  sell <ID>                                   Close a position
  threshold <0.30>                            Change skew threshold
  interval <15>                               Change scan interval (seconds)
  volume <500>                                Change min volume filter
  help                                        Show commands
  quit / q                                    Exit
"""
import argparse
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

load_dotenv()

from bot.models import SkewedMarket, Position
from bot.polymarket_client import PolymarketClient
from bot.price_tracker import PriceTracker
from bot.skew_scanner import SkewScanner
from bot.position_manager import PositionManager

# ── Paths ─────────────────────────────────────────────────────────────────────
POSITIONS_FILE = Path(__file__).parent / "positions.json"

# ── Colour palette ────────────────────────────────────────────────────────────
C_BRAND    = "bold cyan"
C_PROFIT   = "bold green"
C_LOSS     = "bold red"
C_WARN     = "bold yellow"
C_DIM      = "dim white"
C_PUMP     = "bright_green"
C_FADE     = "bright_red"
C_NEUTRAL  = "white"
C_TARGET   = "bold bright_yellow"
C_LIVE     = "bright_cyan"

console = Console()


# ═══════════════════════════════════════════════════════════════════════════════
#  Display builders
# ═══════════════════════════════════════════════════════════════════════════════

def _momentum_style(momentum: float) -> Tuple[str, str]:
    """Return (text, style) for momentum column."""
    if momentum >= 0.010:
        return (f"🔥+{momentum*100:.1f}¢", C_PUMP)
    if momentum >= 0.003:
        return (f"↑+{momentum*100:.1f}¢", "green")
    if momentum <= -0.010:
        return (f"💀{momentum*100:.1f}¢", C_FADE)
    if momentum <= -0.003:
        return (f"↓{momentum*100:.1f}¢", "red")
    return (f"  {momentum*100:+.1f}¢", C_DIM)


def _signal_text(sm: SkewedMarket, price_tracker: PriceTracker) -> Text:
    label = price_tracker.momentum_label(sm.market.id, sm.cheap_side)
    if label == "PUMP":
        return Text("🔥 PUMP", style=C_PUMP)
    if label == "rising":
        return Text("↑ rising", style="green")
    if label == "DUMP":
        return Text("💀 DUMP", style=C_FADE)
    if label == "fading":
        return Text("↓ fading", style="red")
    return Text("  ·  ", style=C_DIM)


def _roi_style(roi: float) -> Tuple[str, str]:
    pct = roi * 100
    sign = "+" if pct >= 0 else ""
    text = f"{sign}{pct:.1f}%"
    if roi >= 0.20:
        return (text, "bold bright_green")
    if roi >= 0.10:
        return (text, "green")
    if roi >= 0.05:
        return (text, "yellow")
    if roi < 0:
        return (text, C_FADE)
    return (text, C_DIM)


def build_header(
    scan_num: int,
    last_scan: str,
    total_markets: int,
    sports_count: int,
    skew_count: int,
    threshold: float,
    interval: int,
) -> Panel:
    status = Text()
    status.append("⚡ POLYMARKET SPORTS SCALPER", style=C_BRAND)
    status.append("  │  ", style=C_DIM)
    status.append(f"Scan #{scan_num}", style="cyan")
    status.append("  │  ", style=C_DIM)
    status.append(f"{last_scan}", style=C_DIM)
    status.append("  │  ", style=C_DIM)
    status.append(f"{total_markets} total", style=C_DIM)
    status.append("  │  ", style=C_DIM)
    status.append(f"{sports_count} sports", style="cyan")
    status.append("  │  ", style=C_DIM)
    status.append(f"{skew_count} skewed ≤{threshold:.0%}", style=C_WARN)
    status.append("  │  ", style=C_DIM)
    status.append(f"refresh {interval}s", style=C_DIM)
    return Panel(Align.center(status), border_style="bright_blue", padding=(0, 1))


def build_skew_table(
    skewed: List[SkewedMarket],
    price_tracker: PriceTracker,
    max_rows: int = 20,
) -> Panel:
    t = Table(
        show_header=True,
        header_style="bold white",
        border_style="yellow",
        row_styles=["", "dim"],
        expand=True,
        padding=(0, 1),
    )
    t.add_column("#", style="dim", width=3, no_wrap=True)
    t.add_column("Market Question", min_width=38)
    t.add_column("Side", width=4, justify="center")
    t.add_column("Price", width=6, justify="right")
    t.add_column("Expensive", width=9, justify="right")
    t.add_column("Skew", width=5, justify="right")
    t.add_column("Volume", width=7, justify="right")
    t.add_column("Δ5scan", width=7, justify="right")
    t.add_column("Signal", width=10, justify="left")
    t.add_column("10% exit", width=8, justify="right")
    t.add_column("20% exit", width=8, justify="right")
    t.add_column("5× exit", width=8, justify="right")

    for sm in skewed[:max_rows]:
        m = sm.market

        # Momentum column
        mom_txt, mom_style = _momentum_style(sm.momentum)

        # Volume
        vol = m.volume or 0
        if vol >= 1_000_000:
            vol_str = f"{vol/1_000_000:.1f}M"
        elif vol >= 1_000:
            vol_str = f"{vol/1_000:.0f}K"
        else:
            vol_str = f"{vol:.0f}"

        # Live indicator
        from bot.skew_scanner import SkewScanner as SS
        live_tag = " [cyan]LIVE[/cyan]" if SS.is_likely_live(m) else ""

        question = m.question[:50] + live_tag

        side_style = C_PUMP if sm.cheap_side == "YES" else "magenta"

        t.add_row(
            str(sm.display_num),
            question,
            Text(sm.cheap_side, style=side_style),
            Text(f"${sm.cheap_price:.3f}", style=C_WARN),
            Text(f"${sm.expensive_price:.3f}", style=C_DIM),
            Text(f"{sm.skew_ratio:.0f}x", style=C_DIM),
            Text(vol_str, style=C_DIM),
            Text(mom_txt, style=mom_style),
            _signal_text(sm, price_tracker),
            Text(f"${sm.price_for_roi(0.10):.3f}", style=C_DIM),
            Text(f"${sm.price_for_roi(0.20):.3f}", style=C_DIM),
            Text(f"${sm.price_for_roi(4.0):.3f}", style=C_PROFIT),
        )

    if not skewed:
        t.add_row("[dim]No markets match current filters[/dim]",
                  *["[dim]—[/dim]"] * 11)

    return Panel(
        t,
        title=f"[bold yellow]🎯 SKEW SNIPER[/bold yellow]  [dim]cheap side ≤ {len(skewed)} markets[/dim]",
        border_style="yellow",
        padding=0,
    )


def build_positions_table(pos_mgr: PositionManager) -> Panel:
    positions = pos_mgr.get_open()

    t = Table(
        show_header=True,
        header_style="bold white",
        border_style="green",
        expand=True,
        padding=(0, 1),
    )
    t.add_column("ID", width=7, style=C_DIM, no_wrap=True)
    t.add_column("Market", min_width=35)
    t.add_column("Side", width=4, justify="center")
    t.add_column("Entry", width=7, justify="right")
    t.add_column("Now", width=7, justify="right")
    t.add_column("Shares", width=6, justify="right")
    t.add_column("Cost", width=7, justify="right")
    t.add_column("P&L", width=8, justify="right")
    t.add_column("ROI", width=7, justify="right")
    t.add_column("Target", width=7, justify="right")
    t.add_column("Status", width=12, justify="center")

    for pos in positions:
        roi_txt, roi_style = _roi_style(pos.roi)
        pnl_txt, pnl_style = _roi_style(pos.roi)  # reuse style logic
        pnl_display = f"${pos.pnl:+.2f}"

        if pos.status == 'target_hit':
            status_cell = Text("🎯 EXIT NOW", style=C_TARGET)
            row_style = "on dark_green"
        else:
            status_cell = Text("tracking", style=C_DIM)
            row_style = ""

        t.add_row(
            pos.pos_id,
            pos.market_question[:40],
            Text(pos.side, style=C_PUMP if pos.side == "YES" else "magenta"),
            Text(f"${pos.entry_price:.3f}", style=C_DIM),
            Text(f"${pos.current_price:.3f}", style="white"),
            Text(str(pos.shares), style=C_DIM),
            Text(f"${pos.cost:.2f}", style=C_DIM),
            Text(pnl_display, style=pnl_style),
            Text(roi_txt, style=roi_style),
            Text(f"{pos.target_roi:.0%}", style=C_DIM),
            status_cell,
        )

    if not positions:
        t.add_row(
            "[dim]—[/dim]",
            "[dim]No open positions  ·  type: buy <#> <Y/N> <price> <shares> [target%][/dim]",
            *["[dim]—[/dim]"] * 9,
        )

    total_pnl = pos_mgr.total_pnl()
    total_cost = pos_mgr.total_cost()
    pnl_style = C_PROFIT if total_pnl >= 0 else C_LOSS

    title_parts = (
        f"[bold green]💼 POSITIONS[/bold green]  "
        f"[dim]open: {len(positions)}[/dim]  "
        f"[dim]deployed: ${total_cost:.2f}[/dim]  "
        f"[{pnl_style}]P&L: ${total_pnl:+.2f}[/{pnl_style}]"
    )
    return Panel(t, title=title_parts, border_style="green", padding=0)


def build_trade_log(pos_mgr: PositionManager, n: int = 5) -> Panel:
    closed = pos_mgr.get_closed(n)
    lines: List[Text] = []
    for pos in reversed(closed):
        roi_txt, roi_style = _roi_style(pos.roi)
        t = Text()
        t.append(f"[{pos.pos_id}] ", style=C_DIM)
        t.append(f"{pos.side} ", style=C_PUMP if pos.side == "YES" else "magenta")
        t.append(f"{pos.market_question[:35]}", style="white")
        t.append(f"  entry ${pos.entry_price:.3f}", style=C_DIM)
        t.append(f"  exit ${pos.current_price:.3f}", style=C_DIM)
        t.append(f"  ROI {roi_txt}", style=roi_style)
        t.append(f"  P&L ${pos.pnl:+.2f}", style=C_PROFIT if pos.pnl >= 0 else C_LOSS)
        lines.append(t)

    if not lines:
        lines.append(Text("  No closed trades yet", style=C_DIM))

    content = Group(*lines)
    return Panel(content, title="[dim]📋 Trade Log (last closed)[/dim]",
                 border_style="dim", padding=(0, 1))


def build_command_hint() -> Panel:
    txt = Text(justify="center")
    txt.append("buy ", style="bold cyan")
    txt.append("<#> <Y|N> <price> <shares> [target%]", style="white")
    txt.append("    sell ", style="bold cyan")
    txt.append("<ID>", style="white")
    txt.append("    threshold ", style="bold cyan")
    txt.append("<0.30>", style="white")
    txt.append("    interval ", style="bold cyan")
    txt.append("<15>", style="white")
    txt.append("    quit", style="bold cyan")
    return Panel(txt, border_style="dim", padding=(0, 1))


def build_display(
    scan_num: int,
    last_scan: str,
    total_markets: int,
    sports_count: int,
    skewed: List[SkewedMarket],
    price_tracker: PriceTracker,
    pos_mgr: PositionManager,
    threshold: float,
    interval: int,
) -> Group:
    return Group(
        build_header(scan_num, last_scan, total_markets, sports_count,
                     len(skewed), threshold, interval),
        build_skew_table(skewed, price_tracker),
        build_positions_table(pos_mgr),
        build_trade_log(pos_mgr),
        build_command_hint(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Command parser
# ═══════════════════════════════════════════════════════════════════════════════

def parse_command(
    line: str,
    skewed: List[SkewedMarket],
    pos_mgr: PositionManager,
    config: dict,
) -> Optional[str]:
    """
    Parse and execute a typed command.
    Returns a status message string (shown briefly), or None on parse error.
    """
    parts = line.strip().split()
    if not parts:
        return None

    cmd = parts[0].lower()

    # ── buy ──────────────────────────────────────────────────────────────
    if cmd == "buy":
        # buy <#> <Y|N> <price> <shares> [target%]
        if len(parts) < 5:
            return "[red]Usage: buy <#> <Y|N> <price> <shares> [target%][/red]"
        try:
            num = int(parts[1])
            side_raw = parts[2].upper()
            if side_raw not in ("Y", "N", "YES", "NO"):
                return "[red]Side must be Y or N[/red]"
            side = "YES" if side_raw in ("Y", "YES") else "NO"
            price = float(parts[3])
            shares = int(parts[4])
            target_roi = float(parts[5]) / 100.0 if len(parts) >= 6 else config['default_target']
        except (ValueError, IndexError):
            return "[red]Invalid buy command. Example: buy 3 Y 0.05 200 10[/red]"

        # Find the market by display number
        market = next((s.market for s in skewed if s.display_num == num), None)
        if not market:
            return f"[red]Market #{num} not found in current scan[/red]"

        pos = pos_mgr.add(
            market_id=market.id,
            market_question=market.question,
            side=side,
            entry_price=price,
            shares=shares,
            target_roi=target_roi,
        )
        cost = price * shares
        return (
            f"[green]✓ Position [{pos.pos_id}] opened: "
            f"{side} {market.question[:35]}... "
            f"@ ${price:.3f} × {shares} = ${cost:.2f} | "
            f"target {target_roi:.0%}[/green]"
        )

    # ── sell ─────────────────────────────────────────────────────────────
    if cmd == "sell":
        if len(parts) < 2:
            return "[red]Usage: sell <position-ID>[/red]"
        pos = pos_mgr.close(parts[1])
        if not pos:
            return f"[red]Position {parts[1].upper()} not found[/red]"
        roi_pct = pos.roi * 100
        pnl = pos.pnl
        sign = "+" if pnl >= 0 else ""
        style = "green" if pnl >= 0 else "red"
        return (
            f"[{style}]✓ Closed [{pos.pos_id}] "
            f"{pos.side} {pos.market_question[:30]}...  "
            f"ROI: {roi_pct:+.1f}%  P&L: {sign}${pnl:.2f}[/{style}]"
        )

    # ── threshold ────────────────────────────────────────────────────────
    if cmd in ("threshold", "t"):
        if len(parts) < 2:
            return "[red]Usage: threshold <value>  e.g. threshold 0.20[/red]"
        try:
            val = float(parts[1])
            config['threshold'] = val
            return f"[cyan]Skew threshold → {val:.0%}[/cyan]"
        except ValueError:
            return "[red]Invalid threshold value[/red]"

    # ── interval ─────────────────────────────────────────────────────────
    if cmd in ("interval", "i"):
        if len(parts) < 2:
            return "[red]Usage: interval <seconds>  e.g. interval 30[/red]"
        try:
            val = int(parts[1])
            config['interval'] = max(5, val)
            return f"[cyan]Scan interval → {config['interval']}s[/cyan]"
        except ValueError:
            return "[red]Invalid interval[/red]"

    # ── volume ───────────────────────────────────────────────────────────
    if cmd in ("volume", "vol", "v"):
        if len(parts) < 2:
            return "[red]Usage: volume <min_usd>  e.g. volume 1000[/red]"
        try:
            val = float(parts[1])
            config['min_volume'] = val
            return f"[cyan]Min volume → ${val:.0f}[/cyan]"
        except ValueError:
            return "[red]Invalid volume[/red]"

    # ── target ───────────────────────────────────────────────────────────
    if cmd in ("target",):
        if len(parts) < 2:
            return "[red]Usage: target <pct>  e.g. target 15[/red]"
        try:
            val = float(parts[1]) / 100.0
            config['default_target'] = val
            return f"[cyan]Default ROI target → {val:.0%}[/cyan]"
        except ValueError:
            return "[red]Invalid target[/red]"

    # ── help ─────────────────────────────────────────────────────────────
    if cmd in ("help", "h", "?"):
        return (
            "[cyan]Commands:[/cyan]\n"
            "  [white]buy[/white] [dim]<#> <Y|N> <price> <shares> [target%][/dim]  — open a position\n"
            "  [white]sell[/white] [dim]<ID>[/dim]                                — close a position\n"
            "  [white]threshold[/white] [dim]<0.30>[/dim]                         — skew filter (e.g. 0.10 = 10¢)\n"
            "  [white]interval[/white] [dim]<15>[/dim]                            — scan interval in seconds\n"
            "  [white]volume[/white] [dim]<500>[/dim]                             — min market volume in USD\n"
            "  [white]target[/white] [dim]<10>[/dim]                              — default exit target %\n"
            "  [white]quit[/white] / [white]q[/white]                                          — exit"
        )

    # ── quit ─────────────────────────────────────────────────────────────
    if cmd in ("quit", "q", "exit"):
        return "__QUIT__"

    return f"[dim]Unknown command '{cmd}'. Type 'help' for a list.[/dim]"


# ═══════════════════════════════════════════════════════════════════════════════
#  Main loop
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Polymarket Sports Scalper")
    parser.add_argument("--threshold", type=float, default=0.30,
                        help="Max cheap-side price to scan (default 0.30)")
    parser.add_argument("--interval", type=int, default=15,
                        help="Seconds between scans (default 15)")
    parser.add_argument("--min-vol", type=float, default=500.0,
                        help="Min market volume in USD (default 500)")
    args = parser.parse_args()

    # Mutable config shared between display loop and command parser
    config: dict = {
        'threshold': args.threshold,
        'interval': args.interval,
        'min_volume': args.min_vol,
        'default_target': 0.10,  # 10% default ROI target
    }

    # Shared state
    poly_client = PolymarketClient()
    price_tracker = PriceTracker(max_snapshots=30)
    pos_mgr = PositionManager(save_file=POSITIONS_FILE)

    skewed: List[SkewedMarket] = []
    all_markets_count = 0
    sports_count = 0
    scan_num = 0
    last_scan = "—"
    status_msg: Optional[str] = None
    status_expires = 0.0

    # ── stdin command thread ──────────────────────────────────────────────
    cmd_queue: queue.Queue = queue.Queue()

    def _read_stdin():
        while True:
            try:
                line = sys.stdin.readline()
                if line:
                    cmd_queue.put(line.strip())
            except (EOFError, OSError):
                break

    t = threading.Thread(target=_read_stdin, daemon=True)
    t.start()

    # ── main display + scan loop ──────────────────────────────────────────
    with Live(console=console, refresh_per_second=2, screen=False) as live:

        while True:
            now = time.time()

            # ── fetch & scan ─────────────────────────────────────────────
            try:
                markets = poly_client.get_active_markets(limit=300)
                all_markets_count = len(markets)

                # Update price history before scanning
                price_tracker.update(markets)

                scanner = SkewScanner(
                    max_cheap_price=config['threshold'],
                    min_volume=config['min_volume'],
                    sports_only=True,
                )
                from bot.skew_scanner import SPORTS_CATEGORIES
                skewed = scanner.scan(markets, price_tracker)
                sports_count = sum(
                    1 for m in markets if m.category in SPORTS_CATEGORIES
                )

                # Update position prices
                market_prices = {
                    m.id: (m.yes_price, m.no_price) for m in markets
                }
                newly_hit = pos_mgr.update_prices(market_prices)

                scan_num += 1
                last_scan = time.strftime('%H:%M:%S')

                if newly_hit:
                    status_msg = (
                        " ".join(
                            f"[bold yellow]🎯 TARGET HIT [{p.pos_id}] "
                            f"{p.side} {p.market_question[:25]}... ROI {p.roi:.0%}[/bold yellow]"
                            for p in newly_hit
                        )
                    )
                    status_expires = now + 30.0

            except Exception as e:
                status_msg = f"[red]Scan error: {e}[/red]"
                status_expires = now + 10.0

            # ── process commands ──────────────────────────────────────────
            while not cmd_queue.empty():
                try:
                    raw = cmd_queue.get_nowait()
                    result = parse_command(raw, skewed, pos_mgr, config)
                    if result == "__QUIT__":
                        console.print("[bold cyan]Goodbye.[/bold cyan]")
                        return
                    if result:
                        status_msg = result
                        status_expires = now + 8.0
                except queue.Empty:
                    break

            # ── clear stale status ────────────────────────────────────────
            if status_msg and time.time() > status_expires:
                status_msg = None

            # ── render ───────────────────────────────────────────────────
            display = build_display(
                scan_num=scan_num,
                last_scan=last_scan,
                total_markets=all_markets_count,
                sports_count=sports_count,
                skewed=skewed,
                price_tracker=price_tracker,
                pos_mgr=pos_mgr,
                threshold=config['threshold'],
                interval=config['interval'],
            )

            if status_msg:
                from rich.console import Group as RGroup
                from rich.padding import Padding
                display = RGroup(
                    display,
                    Panel(status_msg, border_style="cyan", padding=(0, 2)),
                )

            live.update(display)

            # ── wait for next scan ────────────────────────────────────────
            wait_until = time.time() + config['interval']
            while time.time() < wait_until:
                # Poll command queue quickly during the wait window
                while not cmd_queue.empty():
                    try:
                        raw = cmd_queue.get_nowait()
                        result = parse_command(raw, skewed, pos_mgr, config)
                        if result == "__QUIT__":
                            console.print("[bold cyan]Goodbye.[/bold cyan]")
                            return
                        if result:
                            status_msg = result
                            status_expires = time.time() + 8.0
                            # Re-render immediately after a command
                            disp = build_display(
                                scan_num=scan_num,
                                last_scan=last_scan,
                                total_markets=all_markets_count,
                                sports_count=sports_count,
                                skewed=skewed,
                                price_tracker=price_tracker,
                                pos_mgr=pos_mgr,
                                threshold=config['threshold'],
                                interval=config['interval'],
                            )
                            from rich.console import Group as RGroup2
                            disp = RGroup2(
                                disp,
                                Panel(status_msg, border_style="cyan", padding=(0, 2)),
                            )
                            live.update(disp)
                    except queue.Empty:
                        break
                time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold cyan]Scalper stopped.[/bold cyan]")
