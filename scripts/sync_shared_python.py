#!/usr/bin/env python3
"""Propagate canonical modules under shared/python/ into each service's app/ dir.

Each backend microservice is built from its own directory as an isolated
Docker context, so a Python module can't be imported across service
boundaries at runtime. Instead, shared/python/ holds the single source of
truth for small cross-cutting modules; this script copies them into every
service that uses them so the build stays self-contained.

Usage: python scripts/sync_shared_python.py [--check]
  --check   exit 1 if any target copy is out of date, without writing
"""
import filecmp
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = REPO_ROOT / "shared" / "python"

# module_name -> list of service directories (relative to repo root) whose
# app/<module_name> should mirror shared/python/<module_name>
TARGETS = {
    "elk_logger.py": [
        "technical-agent",
        "pattern-agent",
        "sentiment-agent",
        "macro-agent",
        "rl-agent",
        "ensemble-engine",
        "feedback-service",
        "market-data-service",
        "stock-scanner",
        "model-trainer",
        "sentiment-service",
        "autopilot-service",
    ],
    "agent_bootstrap.py": [
        "technical-agent",
        "pattern-agent",
        "sentiment-agent",
        "macro-agent",
        "rl-agent",
        "ensemble-engine",
    ],
}


def main() -> int:
    check_only = "--check" in sys.argv
    stale = []
    for module_name, services in TARGETS.items():
        source = SHARED_DIR / module_name
        if not source.exists():
            print(f"missing canonical source: {source}", file=sys.stderr)
            return 1
        for service in services:
            dest = REPO_ROOT / service / "app" / module_name
            if dest.exists() and filecmp.cmp(source, dest, shallow=False):
                continue
            stale.append(dest)
            if not check_only:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                print(f"synced {dest.relative_to(REPO_ROOT)}")
    if check_only and stale:
        for dest in stale:
            print(f"out of date: {dest.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1
    if not stale:
        print("all shared python modules already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
