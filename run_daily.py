"""Entry point for the daily job: python run_daily.py [--date YYYY-MM-DD] [--ipo ID_OR_SYMBOL] [--dry-run]"""
from __future__ import annotations

import sys

from ipo_agents.cli import main, reexec_with_renderer_env

if __name__ == "__main__":
    reexec_with_renderer_env()  # so the PDF renderer can load Homebrew's pango on a manual run
    sys.exit(main())
