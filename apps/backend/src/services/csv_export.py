"""Shared CSV-writing boilerplate for the batch scripts (run_analyze.py):
timestamped filename under out/, DictWriter with a header. Sorting stays
with each caller — the sort key differs per script."""

import csv
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "out"


def write_timestamped_csv(prefix: str, fieldnames: list[str], rows: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = OUT_DIR / f"{prefix}_{timestamp}.csv"

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return path
