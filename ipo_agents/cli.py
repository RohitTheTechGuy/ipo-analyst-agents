"""Command line for the daily job (spec stories 1, 60): python run_daily.py [--date D] [--ipo ID [--force]] [--dry-run]"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import date, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from ipo_agents.adapters import Adapters
from ipo_agents.config import Config, load_config
from ipo_agents.contracts import Ipo
from ipo_agents.budget import BudgetMeter
from ipo_agents.runner import plan, run_daily
from ipo_agents.store import Store
from ipo_agents.wiring import MissingSecrets, build_adapters

log = logging.getLogger("ipo_agents.cli")

REPO_ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")


def today_in_india(now: datetime | None = None) -> date:
    """Today's date in India, whatever timezone this machine is set to."""
    return (now or datetime.now(IST)).astimezone(IST).date()


def remove_logging() -> None:
    """Removes the handlers setup_logging added (lets tests call main() repeatedly)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_ipo_agents", False):
            root.removeHandler(handler)
            handler.close()


def setup_logging(log_dir: Path) -> None:
    """Rotating file under log_dir plus stderr (which launchd captures into logs/launchd.err.log)."""
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    remove_logging()
    file_handler = RotatingFileHandler(log_dir / "ipo-agents.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    for handler in (file_handler, logging.StreamHandler(sys.stderr)):
        handler.setFormatter(fmt)
        handler._ipo_agents = True
        root.addHandler(handler)


class _FixedExchange:
    def __init__(self, ipos: list[Ipo]):
        self._ipos = ipos

    def open_ipos(self, today: date) -> list[Ipo]:
        return list(self._ipos)


class _Only:
    """Today's listing, narrowed to one IPO (ipo_id or symbol)."""

    def __init__(self, exchange, key: str):
        self._exchange, self._key = exchange, key

    def open_ipos(self, today: date) -> list[Ipo]:
        return [i for i in self._exchange.open_ipos(today) if _matches(i, self._key)]


def _matches(ipo: Ipo, key: str) -> bool:
    return key.casefold() in {ipo.ipo_id.casefold(), (ipo.symbol or "").casefold()}


def dry_run(today: date, adapters: Adapters, config: Config, only: str | None = None,
            force: bool = False) -> None:
    """Prints what a real run would do (the runner's own plan). Reads the store, never writes it."""
    if only:
        adapters = replace(adapters, exchange=_Only(adapters.exchange, only))
    todo = plan(today, adapters, config, force_ipo=only if force else None)
    if todo.discovery_error is not None:
        print(f"Dry run for {today:%Y-%m-%d}: no source could list open IPOs: {todo.discovery_error}")
        return
    print(f"Dry run for {today:%Y-%m-%d}: {len(todo.skip) + len(todo.hold) + len(todo.analyse)} open mainboard IPO(s)")
    for ipo, status in todo.skip:
        print(f"  skip ({status}): {ipo.ipo_id}  {ipo.name}")
    for ipo in todo.hold:
        print(f"  hold (waiting for RHP): {ipo.ipo_id}  {ipo.name}")
    for ipo in todo.analyse:
        print(f"  analyse: {ipo.ipo_id}  {ipo.name}")
    for ipo_id, name, close_date, _ in todo.remind:
        print(f"  remind (closes {close_date:%d %b}): {ipo_id}  {name}")


def remaining_budget(ipo: Ipo, config: Config) -> tuple[float, float]:
    """(spent, left) of this IPO's cap. Spend is logged per ipo_id and never resets."""
    store = Store(config.db_path)
    try:
        ipo_id = store.known_id(ipo)
    finally:
        store.close()
    spent = BudgetMeter(ipo_id, config).spent
    return spent, max(0.0, config.budget.cap_usd - spent)


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a YYYY-MM-DD date: {text!r}") from None


def main(argv: Sequence[str] | None = None, *, build: Callable[..., Adapters] = build_adapters,
         log_dir: Path = REPO_ROOT / "logs", env_file: Path = REPO_ROOT / ".env") -> int:
    parser = argparse.ArgumentParser(prog="run_daily.py", description="Analyse today's open mainboard IPOs.")
    parser.add_argument("--date", type=_parse_date, help="run as if on this date (YYYY-MM-DD); default: today in IST")
    parser.add_argument("--ipo", metavar="ID_OR_SYMBOL", help="analyse only this open IPO (ipo_id or symbol)")
    parser.add_argument("--force", action="store_true",
                        help="with --ipo: re-run it even if already done or failed_budget; it can only spend "
                             "what is left of its budget cap, which never resets")
    parser.add_argument("--dry-run", action="store_true",
                        help="discovery only: print what would be analysed, held or reminded; no model or Telegram calls")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config.yaml", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.force and not args.ipo:
        parser.error("--force needs --ipo")

    setup_logging(log_dir)
    load_dotenv(env_file)  # never overrides variables already set
    today = args.date or today_in_india()
    try:
        config = load_config(args.config)
        adapters = build(config, discovery_only=args.dry_run)
        if args.dry_run:
            dry_run(today, adapters, config, only=args.ipo, force=args.force)
            return 0
        if args.ipo:
            ipos = adapters.exchange.open_ipos(today)
            chosen = [i for i in ipos if _matches(i, args.ipo)]
            if not chosen:
                listed = ", ".join(f"{i.ipo_id} ({i.symbol})" if i.symbol else i.ipo_id for i in ipos) or "none"
                print(f"{args.ipo} is not among today's open IPOs ({today:%Y-%m-%d}). Open: {listed}", file=sys.stderr)
                return 2
            adapters = replace(adapters, exchange=_FixedExchange(chosen))
            if args.force:
                spent, left = remaining_budget(chosen[0], config)
                print(f"Forced re-run of {chosen[0].ipo_id}: ${spent:.2f} of its ${config.budget.cap_usd:.2f} "
                      f"budget already spent, ${left:.2f} left (spend never resets)")
        log.info("run starting for %s%s", today, f" (only {args.ipo})" if args.ipo else "")
        summary = run_daily(today, adapters, config, force_ipo=args.ipo if args.force else None)
        log.info("run finished: %s", summary.model_dump())
        if args.ipo and summary.skipped:
            log.warning("%s was already analysed (done or failed_budget); add --force to re-run it",
                        ", ".join(summary.skipped))
        return 0
    except MissingSecrets as exc:
        log.error("%s", exc)
        return 1
    except Exception:
        log.exception("run failed")
        return 1
