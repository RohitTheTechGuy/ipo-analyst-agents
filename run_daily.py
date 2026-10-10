"""Entry point for the daily job: python run_daily.py"""
from __future__ import annotations

import sys

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()
    # Real exchange, downloader and Telegram adapters arrive in tickets 04 and 05;
    # until then the pipeline only runs through the test suite with fakes.
    print("run_daily: real adapters not wired yet (tickets 04/05)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
