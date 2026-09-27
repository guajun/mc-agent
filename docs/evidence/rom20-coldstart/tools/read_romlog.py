#!/usr/bin/env python3
"""Read the agent logger's captured carts and print uuid + ordered items.

Usage:
    python read_romlog.py --file <romlog.jsonl> [--via left_stack_region]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--via", default="left_stack_region")
    args = parser.parse_args()
    order = 0
    for line in Path(args.file).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") != "cart_observed":
            continue
        if args.via and record.get("via") != args.via:
            continue
        order += 1
        items = ", ".join(f"{item['id']} x{item['count']} (slot {item['slot']})" for item in record["items"])
        print(
            f"pop {order}: uuid {record['uuid']} tick {record.get('tick')} "
            f"via {record.get('via')} items: {items}"
        )
    print(f"total captures: {order}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
