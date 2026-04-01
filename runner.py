#!/usr/bin/env python3
"""Master runner for all production admission scrapers."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Ordered production scraper entry points.
SCRAPER_SCRIPTS = [
    Path("FAST University/fast-scraper-standalone.py"),
    Path("GIKI/giki_scraper_standalone.py"),
    Path("IBA Karachi/ibakarachi-scraper-standalone.py"),
    Path("IBASukkur/iba-scraper-standalone.py"),
    Path("Muhammd  Ali Jinnah/muhammadalijinnah-scraper-standalone.py"),
    Path("NUTECH/nutech-scraper-standalone.py"),
]


def run_scraper(script_path: Path, python_executable: str) -> int:
    """Run a single scraper script and return its exit code."""
    print("=" * 80)
    print(f"[START] Running scraper: {script_path}")
    print("=" * 80)

    result = subprocess.run(
        [python_executable, str(script_path)],
        check=False,
    )

    print("-" * 80)
    if result.returncode == 0:
        print(f"[SUCCESS] {script_path} completed with exit code 0")
    else:
        print(f"[FAILED] {script_path} failed with exit code {result.returncode}")
    print("-" * 80)

    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all production scrapers sequentially.")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=3.0,
        help="Delay between scraper runs in seconds (default: 3).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List scraper scripts and exit.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent

    if args.list:
        print("Production scraper execution order:")
        for i, script in enumerate(SCRAPER_SCRIPTS, start=1):
            print(f"{i}. {script}")
        return 0

    failed: list[tuple[Path, int]] = []

    print(f"Using Python interpreter: {sys.executable}")
    print(f"Repository root: {repo_root}")

    for idx, relative_script in enumerate(SCRAPER_SCRIPTS, start=1):
        script_path = repo_root / relative_script

        if not script_path.exists():
            print(f"[FAILED] Missing scraper script: {relative_script}")
            failed.append((relative_script, 127))
            continue

        exit_code = run_scraper(script_path, sys.executable)
        if exit_code != 0:
            failed.append((relative_script, exit_code))

        if idx < len(SCRAPER_SCRIPTS):
            time.sleep(args.sleep_seconds)

    print("=" * 80)
    print("RUN SUMMARY")
    print("=" * 80)
    print(f"Total scrapers: {len(SCRAPER_SCRIPTS)}")
    print(f"Succeeded: {len(SCRAPER_SCRIPTS) - len(failed)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print("Failed scripts:")
        for script, code in failed:
            print(f"- {script} (exit code: {code})")
        return 1

    print("All scrapers completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
