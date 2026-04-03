import time
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from bot.polymarket_client import PolymarketClient
from bot.kalshi_client import KalshiClient
from bot.matcher import MarketMatcher
from bot.detector import ArbitrageDetector
from bot.scanner import MarketScanner

console = Console()


def create_dashboard(opportunities, near_misses, scan_count, last_scan_time, poly_count, kal_count, match_count):
    from rich.layout import Layout
    from rich.columns import Columns

    # Status panel
    status = (
        f"[bold green]Scan #{scan_count}[/bold green]  "
        f"[dim]Last: {last_scan_time}[/dim]  "
        f"[cyan]Poly: {poly_count}[/cyan]  "
        f"[green]Kalshi: {kal_count}[/green]  "
        f"[yellow]Matched: {match_count}[/yellow]"
    )

    # Opportunities table
    opp_table = Table(
        title="[bold yellow]Arbitrage Opportunities[/bold yellow]",
        show_header=True,
        header_style="bold white",
        border_style="yellow"
    )
    opp_table.add_column("Strategy", style="cyan", no_wrap=True, min_width=40)
    opp_table.add_column("Polymarket", style="magenta", min_width=35)
    opp_table.add_column("Kalshi", style="green", min_width=35)
    opp_table.add_column("Profit", justify="right", style="bold yellow", min_width=8)

    if opportunities:
        for opp in opportunities:
            opp_table.add_row(
                opp.strategy,
                opp.market_a.question[:50],
                opp.market_b.question[:50],
                f"{opp.profit_margin:.2%}"
            )
    else:
        opp_table.add_row(
            "[dim]No opportunities found[/dim]",
            "[dim]-[/dim]",
            "[dim]-[/dim]",
            "[dim]-[/dim]"
        )

    # Near-misses table (closest to arbitrage)
    near_table = Table(
        title="[bold blue]Closest to Arbitrage (Near Misses)[/bold blue]",
        show_header=True,
        header_style="bold white",
        border_style="blue"
    )
    near_table.add_column("Polymarket", style="magenta", min_width=40)
    near_table.add_column("Kalshi", style="green", min_width=40)
    near_table.add_column("Best Cost", justify="right", style="bold red", min_width=10)
    near_table.add_column("Gap to Arb", justify="right", style="dim", min_width=10)

    for cost, p, k in near_misses[:8]:
        gap = cost - 1.0
        near_table.add_row(
            p.question[:45],
            k.question[:45],
            f"{cost:.4f}",
            f"+{gap:.4f}"
        )

    from rich.console import Group
    return Group(
        Panel(status, title="[bold]Polymarket ↔ Kalshi Arbitrage Scanner[/bold]", border_style="bright_blue"),
        opp_table,
        near_table
    )


def main():
    console.print(Panel(
        "[bold blue]Polymarket <-> Kalshi Arbitrage Bot[/bold blue]\n"
        "[italic]Initializing clients and starting scan loop...[/italic]\n"
        "[dim]Scanning: NBA Finals | NHL Stanley Cup | MLB Championship | World Events[/dim]"
    ))

    poly_client = PolymarketClient()
    kal_client = KalshiClient()
    matcher = MarketMatcher(threshold=0.65)  # Lowered from 0.80 for better coverage
    detector = ArbitrageDetector(min_profit_margin=0.01)
    scanner = MarketScanner(poly_client, kal_client, matcher, detector)

    scan_count = 0
    all_opportunities = []
    near_misses = []
    poly_count = kal_count = match_count = 0

    with Live(console=console, refresh_per_second=1) as live:
        while True:
            scan_count += 1
            last_scan_time = time.strftime('%H:%M:%S')
            try:
                # Fetch markets
                poly_markets = poly_client.get_active_markets(limit=200)
                kal_markets = kal_client.get_active_markets(limit=200)
                poly_count = len(poly_markets)
                kal_count = len(kal_markets)

                # Match markets
                matches = matcher.find_matches(poly_markets, kal_markets)
                match_count = len(matches)

                # Detect opportunities
                all_opportunities = detector.detect_opportunities(matches)

                # Compute near-misses (closest to arbitrage)
                fees = detector.poly_fee + detector.kal_fee
                near_candidates = []
                for p, k in matches:
                    cost_1 = p.yes_price + k.no_price + fees
                    cost_2 = k.yes_price + p.no_price + fees
                    best = min(cost_1, cost_2)
                    if best > 1.0:  # Not yet arbitrage
                        near_candidates.append((best, p, k))
                near_candidates.sort(key=lambda x: x[0])
                near_misses = near_candidates[:8]

                live.update(create_dashboard(
                    all_opportunities, near_misses,
                    scan_count, last_scan_time,
                    poly_count, kal_count, match_count
                ))
                time.sleep(60)

            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[bold red]Error during scan:[/bold red] {e}")
                import traceback
                console.print(traceback.format_exc())
                time.sleep(10)


if __name__ == "__main__":
    main()
