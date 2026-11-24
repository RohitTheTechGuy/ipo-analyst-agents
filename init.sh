#!/usr/bin/env bash
# One-time (and safely repeatable) setup for IPO Analyst Agents. Mirrors "Setup" in README.md.
#   ./init.sh           set up and run the dry-run check
#   ./init.sh --test    also run the offline test suite
# It does not install the launchd scheduler; that step is printed at the end.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$*"; }
die() { printf 'init.sh: %s\n' "$*" >&2; exit 1; }

[[ "$(uname)" == "Darwin" ]] || die "needs macOS (the scheduler uses launchd)."
command -v brew >/dev/null || die "Homebrew not found. Install it from https://brew.sh first."

say "System libraries (PDF rendering and OCR)"
for pkg in pango tesseract; do
  if brew list --versions "$pkg" >/dev/null 2>&1; then
    echo "$pkg: already installed"
  else
    brew install "$pkg"
  fi
done

say "Python virtual environment"
if [[ ! -x .venv/bin/python ]]; then
  # The venv was built with Python 3.14; fall back to whatever python3 is.
  PY="$(command -v python3.14 || command -v python3)" || die "python3 not found."
  "$PY" -m venv .venv
fi
.venv/bin/python --version
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
echo "requirements installed"

say "Environment file"
if [[ -f .env ]]; then
  echo ".env: exists, left untouched"
else
  cp .env.example .env
  echo ".env: created from .env.example. Fill in the keys before a real run."
fi
missing=()
for key in OPENAI_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do
  grep -Eq "^${key}=.+" .env || missing+=("$key")
done
if (( ${#missing[@]} )); then
  echo "Required keys still empty in .env: ${missing[*]}"
fi

mkdir -p logs data reports

if [[ "${1:-}" == "--test" ]]; then
  say "Offline tests (no network, no spend)"
  .venv/bin/pytest -q -p no:warnings
fi

say "Dry run (no model calls, no Telegram)"
.venv/bin/python run_daily.py --dry-run

say "Done. Next steps"
cat <<'EOF'
  .venv/bin/python run_daily.py --smoke           # ~$0.02 check of every model and schema
  .venv/bin/python scheduler/install.py install   # schedule the daily run and the Telegram picks job
EOF
