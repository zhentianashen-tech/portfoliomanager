#!/usr/bin/env python3
"""Create a neutral five-slot working sequence from an analysis catalog."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "analysis" / "catalog.json")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis" / "sequence.json")
    parser.add_argument("--title", default="Portfolio working sequence")
    parser.add_argument("--limit", type=int, help="Maximum number of photographs, rounded down to a multiple of five")
    args = parser.parse_args()

    rows = json.loads(args.catalog.read_text(encoding="utf-8"))
    eligible = [row for row in rows if (row.get("selection") or {}).get("status") not in {"reject", "exclude"}]
    if args.limit is not None:
        eligible = eligible[:max(args.limit, 0)]
    usable_count = len(eligible) - (len(eligible) % 5)
    eligible = eligible[:usable_count]
    if not eligible:
        raise SystemExit("The catalog needs at least five eligible photographs.")

    movements = []
    for start in range(0, len(eligible), 5):
        number = len(movements) + 1
        movements.append({
            "id": f"mock-block-{number:02d}",
            "title": f"Mock block {number:02d}",
            "note": "Replace this mock-up comment while arranging the draft.",
            "members": [row["relative_path"] for row in eligible[start:start + 5]],
        })

    sequence = {
        "title": args.title,
        "subtitle": "Editable mock-up",
        "status": "working",
        "photographs": len(eligible),
        "role_schema": [
            {"key": "anchor", "label": "Anchor", "purpose": "Starts the visual relationship"},
            {"key": "echo-1", "label": "Echo I", "purpose": "Repeats or transforms the anchor"},
            {"key": "echo-2", "label": "Echo II", "purpose": "Extends the relationship"},
            {"key": "bridge", "label": "Bridge", "purpose": "Moves toward the next block"},
            {"key": "pause", "label": "Pause", "purpose": "Breaks the expected rhythm"},
        ],
        "movements": movements,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(sequence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(eligible)} photographs in {len(movements)} mock blocks to {args.output}")


if __name__ == "__main__":
    main()
