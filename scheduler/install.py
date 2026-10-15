"""Installs the launchd jobs (spec stories 1-2): python scheduler/install.py install|uninstall|status
com.ipoagents.daily: weekdays at 10:30, sends the daily updates and offers new IPOs on Telegram.
com.ipoagents.picks: every 5 minutes, analyses the IPOs picked by Telegram reply."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from string import Template
from xml.sax.saxutils import escape

LABEL = "com.ipoagents.daily"
PICKS_LABEL = "com.ipoagents.picks"
LABELS = (LABEL, PICKS_LABEL)
REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run(args: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True)


def render(repo: Path, label: str = LABEL) -> str:
    repo = Path(repo).resolve()
    template = TEMPLATE_DIR / f"{label}.plist.template"
    values = {"label": label, "python": repo / ".venv/bin/python", "script": repo / "run_daily.py",
              "repo": repo, "logs": repo / "logs"}
    return Template(template.read_text()).substitute({k: escape(str(v)) for k, v in values.items()})


def plist_path(agents_dir: Path, label: str = LABEL) -> Path:
    return Path(agents_dir) / f"{label}.plist"


def install(*, repo: Path = REPO_ROOT, agents_dir: Path = AGENTS_DIR, runner: Runner = _run,
            uid: int | None = None) -> int:
    repo, uid = Path(repo).resolve(), os.getuid() if uid is None else uid
    if not (repo / ".venv/bin/python").exists():
        print(f"warning: {repo / '.venv/bin/python'} does not exist; the job will fail until it does", file=sys.stderr)
    (repo / "logs").mkdir(parents=True, exist_ok=True)
    when = {LABEL: "weekdays at 10:30, updates + Telegram offer (runs on wake if the Mac was asleep)",
            PICKS_LABEL: "every 5 minutes, analyses IPOs picked by Telegram reply"}
    for label in LABELS:
        path = plist_path(agents_dir, label)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Reinstalling: unload the old copy first, or bootstrap fails with "already loaded". Fails harmlessly if absent.
        runner(["launchctl", "bootout", f"gui/{uid}", str(path)])
        path.write_text(render(repo, label))
        result = runner(["launchctl", "bootstrap", f"gui/{uid}", str(path)])
        if result.returncode != 0:
            print(f"launchctl bootstrap failed for {label}: {result.stderr.strip()}", file=sys.stderr)
            return result.returncode
        print(f"installed {path}: {when[label]}")
    return 0


def uninstall(*, agents_dir: Path = AGENTS_DIR, runner: Runner = _run, uid: int | None = None) -> int:
    uid = os.getuid() if uid is None else uid
    for label in LABELS:
        path = plist_path(agents_dir, label)
        runner(["launchctl", "bootout", f"gui/{uid}", str(path)])
        path.unlink(missing_ok=True)
        print(f"uninstalled {path}")
    return 0


def status(*, agents_dir: Path = AGENTS_DIR, runner: Runner = _run, uid: int | None = None) -> int:
    uid = os.getuid() if uid is None else uid
    all_loaded = True
    for label in LABELS:
        path = plist_path(agents_dir, label)
        loaded = runner(["launchctl", "print", f"gui/{uid}/{label}"]).returncode == 0
        all_loaded &= loaded
        print(f"{label}: plist {'present' if path.exists() else 'absent'}, launchd {'loaded' if loaded else 'not loaded'}")
    return 0 if all_loaded else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "uninstall", "status"])
    command = parser.parse_args(argv).command
    return {"install": install, "uninstall": uninstall, "status": status}[command]()


if __name__ == "__main__":
    sys.exit(main())
