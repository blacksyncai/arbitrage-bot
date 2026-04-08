#!/usr/bin/env python3
"""
Polymarket Sports Scalper — LIVE AUTO-TRADING BOT
===================================================
Scans Polymarket sports markets every 15 seconds, auto-buys cheap sides
with positive momentum, and auto-sells at your target ROI.

DRY RUN (default — safe to run, no real money):
    python bot_runner.py

LIVE TRADING (real orders, real money):
    Set in .env:   DRY_RUN=false  +  all POLY_* credentials
    Then run:      python bot_runner.py

Key flags:
    --entry   0.20   Max cheap-side price to enter (default 20¢)
    --target  0.10   Exit ROI target (default 10%)
    --stop    0.50   Stop-loss from entry (default -50%)
    --size    50     Max USDC per trade (default $50)
    --max     5      Max concurrent positions (default 5)
    --hold    45     Force-exit after N minutes (default 45)
    --scan    15     Scan interval in seconds (default 15)
    --momentum 0.003 Min momentum to enter (default +0.3¢)
    --volume  5000   Min market volume USD (default $5000)
"""
import argparse
import os
import sys
import time
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

load_dotenv()

from bot.auto_bot import AutoBot, BotConfig, Trade
from bot.models import SkewedMarket
from bot.price_tracker import PriceTracker
from bot.trader import PolymarketTrader

console = Console()


# ═══════════════════════════════════════════════════════════════════════════════
#  Display
# ═══════════════════════════════════════════════════════════════════════════════

def _roi_style(roi: float) -> str:
    if roi >= 0.20: return "bold bright_green"
    if roi >= 0.10: return "green"
    if roi >= 0.02: return "yellow"
    if roi < 0:     return "bold red"
    return "dim white"


def build_header(status: dict, dry_run: bool, config: BotConfig) -> Panel:
    s = status['stats']
    balance = status['balance']
    pnl = s['pnl']
    pnl_style = "green" if pnl >= 0 else "red"

    hdr = Text()
    hdr.append("⚡ POLYMARKET SPORTS SCALPER BOT", style="bold cyan")
    if dry_run:
        hdr.append("  [DRY RUN]", style="bold yellow")
    else:
        hdr.append("  [LIVE]", style="bold bright_red")
    hdr.append(f"  │  Scan #{s['scans']}", style="cyan")
    hdr.append(f"  │  {status['last_scan']}", style="dim")
    hdr.append(f"  │  Balance: ${balance:.2f}", style="white")
    hdr.append(f"  │  P&L: ", style="dim")
    hdr.append(f"${pnl:+.2f}", style=pnl_style)
    hdr.append(f"  │  W:{s['wins']} L:{s['losses']}", style="dim")
    hdr.append(f"  │  entry≤{config.max_entry_price:.0%}", style="dim")
    hdr.append(f"  │  target {config.target_roi:.0%}", style="dim")
    hdr.append(f"  │  stop {config.stop_loss:.0%}", style="dim")
    return Panel(Align.center(hdr), border_style="bright_blue", padding=(0, 1))


def build_open_trades(trades: List[Trade], pending: List[Trade]) -> Panel:
    t = Table(
        show_header=True, header_style="bold white",
        border_style="green", expand=True, padding=(0, 1),
    )
    t.add_column("ID",      width=9,  style="dim", no_wrap=True)
    t.add_column("Market",  min_width=35)
    t.add_column("Side",    width=4,  justify="center")
    t.add_column("Entry",   width=7,  justify="right")
    t.add_column("Now",     width=7,  justify="right")
    t.add_column("Target",  width=7,  justify="right")
    t.add_column("Stop",    width=7,  justify="right")
    t.add_column("Shares",  width=6,  justify="right")
    t.add_column("Cost",    width=7,  justify="right")
    t.add_column("P&L",     width=8,  justify="right")
    t.add_column("ROI",     width=7,  justify="right")
    t.add_column("Hold",    width=6,  justify="right")
    t.add_column("Status",  width=12, justify="center")

    all_trades = [(tr, False) for tr in trades] + [(tr, True) for tr in pending]

    for tr, is_pending in all_trades:
        roi      = tr.roi
        pnl      = (tr.current_price - tr.entry_price) * tr.size
        roi_sty  = _roi_style(roi)
        sign     = "+" if pnl >= 0 else ""

        if is_pending:
            status_cell = Text("⏳ filling", style="yellow")
            row_style   = "dim"
        elif tr.status == 'target_hit' or roi >= tr.target_roi:
            status_cell = Text("🎯 EXIT", style="bold bright_yellow")
            row_style   = ""
        else:
            status_cell = Text("● open", style="green")
            row_style   = ""

        side_style = "bright_green" if tr.cheap_side == "YES" else "magenta"

        t.add_row(
            tr.trade_id,
            tr.market_question[:38],
            Text(tr.cheap_side, style=side_style),
            Text(f"${tr.entry_price:.3f}", style="dim"),
            Text(f"${tr.current_price:.3f}", style="white"),
            Text(f"${tr.target_price:.3f}", style="green"),
            Text(f"${tr.stop_price:.3f}",   style="red"),
            Text(f"{tr.size:.0f}",           style="dim"),
            Text(f"${tr.cost:.2f}",          style="dim"),
            Text(f"{sign}${pnl:.2f}",        style=roi_sty),
            Text(f"{roi:+.1%}",              style=roi_sty),
            Text(f"{tr.hold_minutes:.0f}m",  style="dim"),
            status_cell,
        )

    if not all_trades:
        t.add_row(
            "—", "[dim]No open trades — scanning for signals...[/dim]",
            *["[dim]—[/dim]"] * 11,
        )

    total_pnl = sum((tr.current_price - tr.entry_price) * tr.size for tr in trades)
    pnl_sty   = "green" if total_pnl >= 0 else "red"
    title = (
        f"[bold green]💼 OPEN TRADES[/bold green]  "
        f"[dim]positions: {len(trades)} open + {len(pending)} pending[/dim]  "
        f"[{pnl_sty}]unrealized: ${total_pnl:+.2f}[/{pnl_sty}]"
    )
    return Panel(t, title=title, border_style="green", padding=0)


def build_skew_view(skewed: List[SkewedMarket], price_tracker: PriceTracker, n: int = 12) -> Panel:
    t = Table(
        show_header=True, header_style="bold white",
        border_style="yellow", expand=True, padding=(0, 1),
    )
    t.add_column("#",       width=3,  style="dim")
    t.add_column("Market",  min_width=38)
    t.add_column("Side",    width=4,  justify="center")
    t.add_column("Price",   width=7,  justify="right")
    t.add_column("Skew",    width=5,  justify="right")
    t.add_column("Vol",     width=7,  justify="right")
    t.add_column("Δ",       width=7,  justify="right")
    t.add_column("Signal",  width=10, justify="left")
    t.add_column("10% →",   width=7,  justify="right")
    t.add_column("5× →",    width=7,  justify="right")

    for sm in skewed[:n]:
        m   = sm.market
        vol = m.volume or 0
        vol_str = (
            f"{vol/1_000_000:.1f}M" if vol >= 1e6
            else f"{vol/1_000:.0f}K" if vol >= 1_000
            else f"{vol:.0f}"
        )

        momentum = sm.momentum
        if momentum >= 0.010:
            mom_txt, mom_sty = f"🔥+{momentum*100:.1f}¢", "bright_green"
        elif momentum >= 0.003:
            mom_txt, mom_sty = f"↑+{momentum*100:.1f}¢", "green"
        elif momentum <= -0.010:
            mom_txt, mom_sty = f"💀{momentum*100:.1f}¢", "bright_red"
        elif momentum <= -0.003:
            mom_txt, mom_sty = f"↓{momentum*100:.1f}¢", "red"
        else:
            mom_txt, mom_sty = "  flat", "dim"

        label = price_tracker.momentum_label(m.id, sm.cheap_side)
        if label == "PUMP":
            sig = Text("🔥 PUMP",   style="bright_green")
        elif label == "rising":
            sig = Text("↑ rising",  style="green")
        elif label == "DUMP":
            sig = Text("💀 DUMP",   style="bright_red")
        elif label == "fading":
            sig = Text("↓ fading",  style="red")
        else:
            sig = Text("  ·  ",     style="dim")

        from bot.skew_scanner import SkewScanner as SS
        live_tag = " [cyan]LIVE[/cyan]" if SS.is_likely_live(m) else ""
        side_sty = "bright_green" if sm.cheap_side == "YES" else "magenta"

        t.add_row(
            str(sm.display_num),
            m.question[:48] + live_tag,
            Text(sm.cheap_side, style=side_sty),
            Text(f"${sm.cheap_price:.3f}", style="bold yellow"),
            Text(f"{sm.skew_ratio:.0f}x",  style="dim"),
            Text(vol_str,                   style="dim"),
            Text(mom_txt,                   style=mom_sty),
            sig,
            Text(f"${sm.price_for_roi(0.10):.3f}", style="dim"),
            Text(f"${sm.price_for_roi(4.0):.3f}",  style="bold green"),
        )

    if not skewed:
        t.add_row("[dim]No skewed sports markets found — broadening threshold?[/dim]",
                  *["[dim]—[/dim]"] * 9)

    return Panel(
        t,
        title=f"[bold yellow]🎯 SKEW SCANNER[/bold yellow]  [dim]{len(skewed)} candidates[/dim]",
        border_style="yellow", padding=0,
    )


def build_closed_log(closed: List[Trade]) -> Panel:
    lines: List[Text] = []
    for tr in reversed(closed[-8:]):
        roi_sty = "green" if tr.pnl >= 0 else "red"
        t = Text()
        t.append(f"[{tr.trade_id}] ", style="dim")
        t.append(f"{tr.cheap_side} ", style="bright_green" if tr.cheap_side == "YES" else "magenta")
        t.append(f"{tr.market_question[:32]}...", style="white")
        t.append(f"  {tr.close_reason}", style="dim")
        t.append(f"  ROI {(tr.exit_price-tr.entry_price)/tr.entry_price:+.1%}", style=roi_sty)
        t.append(f"  P&L ${tr.pnl:+.2f}", style=roi_sty)
        lines.append(t)

    if not lines:
        lines.append(Text("  No closed trades this session", style="dim"))

    return Panel(Group(*lines),
                 title="[dim]📋 Closed Trades[/dim]",
                 border_style="dim", padding=(0, 1))


def build_event_log(log_lines: List[str]) -> Panel:
    style_map = {
        "FILLED": "green",  "BUY": "cyan",  "EXIT": "bold",
        "TARGET": "bold bright_green",  "STOP": "bold red",
        "ERROR": "red",  "CANCEL": "yellow",  "WARN": "yellow",
    }
    lines: List[Text] = []
    for line in log_lines[-10:]:
        t = Text(line)
        for kw, sty in style_map.items():
            if kw in line.upper():
                t.stylize(sty)
                break
        lines.append(t)

    if not lines:
        lines.append(Text("  Waiting for first scan...", style="dim"))

    return Panel(Group(*lines),
                 title="[dim]📡 Event Log[/dim]",
                 border_style="dim", padding=(0, 1))


def build_display(status: dict, dry_run: bool, config: BotConfig,
                  price_tracker: PriceTracker) -> Group:
    return Group(
        build_header(status, dry_run, config),
        build_open_trades(status['open_trades'], status['pending']),
        build_skew_view(status['skewed'], price_tracker),
        build_closed_log(status['closed']),
        build_event_log(status['log']),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="Polymarket Sports Scalper Bot")
    p.add_argument("--entry",    type=float, default=float(os.getenv("MAX_ENTRY_PRICE", "0.20")),
                   help="Max cheap-side price to enter (default 0.20)")
    p.add_argument("--target",   type=float, default=float(os.getenv("TARGET_ROI",   "0.10")),
                   help="Exit ROI target (default 0.10 = 10%%)")
    p.add_argument("--stop",     type=float, default=float(os.getenv("STOP_LOSS",    "0.50")),
                   help="Stop-loss from entry (default 0.50 = -50%%)")
    p.add_argument("--size",     type=float, default=float(os.getenv("MAX_POS_USDC", "50")),
                   help="Max USDC per trade (default 50)")
    p.add_argument("--max",      type=int,   default=int(os.getenv("MAX_POSITIONS",  "5")),
                   help="Max concurrent positions (default 5)")
    p.add_argument("--hold",     type=float, default=float(os.getenv("MAX_HOLD_MIN", "45")),
                   help="Force-exit after N minutes (default 45)")
    p.add_argument("--scan",     type=int,   default=int(os.getenv("SCAN_INTERVAL",  "15")),
                   help="Scan interval in seconds (default 15)")
    p.add_argument("--momentum", type=float, default=float(os.getenv("MIN_MOMENTUM", "0.003")),
                   help="Min momentum to enter (default 0.003)")
    p.add_argument("--volume",   type=float, default=float(os.getenv("MIN_VOLUME",   "5000")),
                   help="Min market volume USD (default 5000)")
    args = p.parse_args()

    # Dry-run defaults to True unless explicitly disabled
    dry_run = os.getenv("DRY_RUN", "true").lower() not in ("false", "0", "no")

    config = BotConfig(
        max_entry_price   = args.entry,
        min_momentum      = args.momentum,
        min_volume        = args.volume,
        max_position_usdc = args.size,
        max_positions     = args.max,
        target_roi        = args.target,
        stop_loss         = args.stop,
        max_hold_minutes  = args.hold,
        scan_interval     = args.scan,
    )

    console.print(Panel(
        f"[bold cyan]Polymarket Sports Scalper Bot[/bold cyan]\n"
        f"Mode: [{'bold yellow]DRY RUN' if dry_run else 'bold bright_red]LIVE TRADING'}]\n"
        f"[dim]entry ≤{config.max_entry_price:.0%}  "
        f"target {config.target_roi:.0%}  "
        f"stop {config.stop_loss:.0%}  "
        f"max ${config.max_position_usdc:.0f}/trade  "
        f"max {config.max_positions} positions  "
        f"hold {config.max_hold_minutes:.0f}min[/dim]",
        border_style="cyan",
    ))

    if not dry_run:
        console.print(Panel(
            "[bold red]⚠  LIVE TRADING MODE — real money, real orders[/bold red]\n"
            "[dim]Make sure POLY_PRIVATE_KEY, POLY_API_KEY, POLY_API_SECRET, "
            "POLY_API_PASSPHRASE are set in .env[/dim]",
            border_style="red",
        ))
        time.sleep(3)  # brief pause to let user abort if needed

    try:
        trader = PolymarketTrader(dry_run=dry_run)
    except ValueError as e:
        console.print(f"[bold red]Setup error:[/bold red] {e}")
        sys.exit(1)

    bot = AutoBot(config=config, trader=trader)

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            try:
                status = bot.run_once()
                live.update(
                    build_display(status, dry_run, config, bot.price_tracker)
                )
                time.sleep(config.scan_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[bold red]Fatal error:[/bold red] {e}")
                import traceback
                console.print(traceback.format_exc())
                time.sleep(10)

    # Session summary
    s = bot.stats
    console.print(Panel(
        f"[bold]Session Summary[/bold]\n"
        f"Scans: {s['scans']}  |  "
        f"Entered: {s['entered']}  |  "
        f"Exits: {s['exits']}  |  "
        f"Wins: {s['wins']}  |  "
        f"Losses: {s['losses']}  |  "
        f"P&L: [{'green' if s['pnl'] >= 0 else 'red'}]${s['pnl']:+.2f}[/]",
        border_style="cyan",
    ))


if __name__ == "__main__":
    main()
