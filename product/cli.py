from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .clients import FetchError, KalshiClient, PolymarketClient
from .db import connect, init_db, reclassify_markets, upsert_snapshots
from .radar import format_markdown, radar_items


DEFAULT_DB = "data/prediction_markets.sqlite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prediction market data pipeline")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the SQLite database")
    subparsers.add_parser("reclassify", help="Refresh stored market categories using the current taxonomy")

    ingest = subparsers.add_parser("ingest", help="Fetch public market data and store snapshots")
    ingest.add_argument("--source", choices=("all", "polymarket", "kalshi"), default="all")
    ingest.add_argument("--limit", type=int, default=200, help="Maximum markets per source")
    ingest.add_argument("--timeout", type=float, default=20, help="HTTP timeout in seconds")

    radar = subparsers.add_parser("radar", help="Print a markdown forecast radar")
    radar.add_argument("--lookback-hours", type=int, default=24)
    radar.add_argument("--limit", type=int, default=25)
    radar.add_argument("--min-volume", type=float)
    radar.add_argument(
        "--min-probability",
        type=float,
        default=0.02,
        help="Minimum probability as a decimal (default: 0.02). Use 0 to include long-tail outcomes.",
    )
    radar.add_argument(
        "--max-per-event",
        type=int,
        default=2,
        help="Maximum markets per event in the report (default: 2). Use 0 for no cap.",
    )
    radar.add_argument("--category")
    radar.add_argument(
        "--all-categories",
        action="store_true",
        help="Include sports, culture, weather, and uncategorized markets",
    )
    radar.add_argument("--out", help="Optional markdown output path")

    return parser


def ingest_command(args: argparse.Namespace) -> int:
    clients = []
    if args.source in ("all", "polymarket"):
        clients.append(PolymarketClient(timeout=args.timeout))
    if args.source in ("all", "kalshi"):
        clients.append(KalshiClient(timeout=args.timeout))

    all_snapshots = []
    for client in clients:
        try:
            snapshots = client.fetch_active_markets(limit=args.limit)
        except FetchError as exc:
            print(f"warning: {exc}", file=sys.stderr)
            continue
        all_snapshots.extend(snapshots)
        print(f"fetched {len(snapshots)} markets from {snapshots[0].source if snapshots else client.__class__.__name__}")

    connection = connect(args.db)
    count = upsert_snapshots(connection, all_snapshots)
    print(f"stored {count} snapshots in {args.db}")
    return 0 if count else 1


def radar_command(args: argparse.Namespace) -> int:
    connection = connect(args.db)
    init_db(connection)
    items = radar_items(
        connection,
        lookback_hours=args.lookback_hours,
        limit=args.limit,
        min_volume=args.min_volume,
        min_probability=args.min_probability,
        category=args.category,
        investment_only=not args.all_categories,
        max_per_event=args.max_per_event or None,
    )
    markdown = format_markdown(items, lookback_hours=args.lookback_hours)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(markdown)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        connection = connect(args.db)
        init_db(connection)
        print(f"initialized {args.db}")
        return 0
    if args.command == "ingest":
        return ingest_command(args)
    if args.command == "reclassify":
        connection = connect(args.db)
        count = reclassify_markets(connection)
        print(f"reclassified {count} markets in {args.db}")
        return 0
    if args.command == "radar":
        return radar_command(args)

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
