"""Command line entry point: python -m airline_searcher [options]."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from airline_searcher.config import ConfigError, load_config
from airline_searcher.emailer import EmailConfigError, SmtpSettings, send_report
from airline_searcher.history import History
from airline_searcher.providers import ProviderError, get_provider
from airline_searcher.report import build_report
from airline_searcher.search import date_combinations, run_search

log = logging.getLogger("airline_searcher")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="airline_searcher",
                                description="Find the cheapest flights in a date window and email them.")
    p.add_argument("-c", "--config", default="config.yaml", help="path to the YAML config")
    p.add_argument("--provider", help="override settings.provider (serpapi, duffel, mock)")
    p.add_argument("--no-email", action="store_true", help="don't send the email")
    p.add_argument("--save-html", metavar="FILE", help="also write the HTML report to FILE")
    p.add_argument("--plan", action="store_true",
                   help="only print the date combinations that would be searched (no API calls)")
    p.add_argument("--no-history", action="store_true", help="don't read or update price history")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    today = date.today()

    try:
        config = load_config(args.config, today)
    except (OSError, ConfigError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.plan:
        for s in config.searches:
            pairs = date_combinations(s, today)
            print(f"{s.name}: {len(pairs)} searches")
            for depart, ret in pairs:
                print(f"  {depart}" + (f" → {ret}" if ret else ""))
        return 0

    try:
        provider = get_provider(args.provider or config.settings.provider)
    except ProviderError as exc:
        print(f"Provider error: {exc}", file=sys.stderr)
        return 2

    smtp = None
    if not args.no_email:
        try:
            smtp = SmtpSettings.from_env()
        except EmailConfigError as exc:
            print(f"Email error: {exc} (use --no-email to skip sending)", file=sys.stderr)
            return 2

    results = [run_search(s, provider, today, config.settings.max_requests_per_search)
               for s in config.searches]

    history = History(None if args.no_history else config.settings.history_file)
    report = build_report(results, history, config.settings.email_subject, today)

    print(report.text)
    if args.save_html:
        Path(args.save_html).write_text(report.html, encoding="utf-8")
        print(f"\nHTML report written to {args.save_html}")

    if smtp:
        send_report(report, smtp)
        print(f"\nEmail sent to {', '.join(smtp.recipients)}")

    for r in results:
        history.record(r, today)
    history.save()

    # Fail the run (so GitHub shows a red X) only if every single request failed.
    if all(r.requests_made and not r.options and len(r.errors) >= r.requests_made for r in results):
        print("\nAll searches failed — check your API key / quota.", file=sys.stderr)
        return 1
    return 0
