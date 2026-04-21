from __future__ import annotations

import argparse
import json
import sys

from app.config import load_config
from state.store import HistoryStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20, help="Nombre maximum de lignes retournees.")
    parser.add_argument("--status", help="Filtre par statut.")
    parser.add_argument("--slot-id", help="Filtre par slot.")
    parser.add_argument("--scenario-id", help="Filtre par scenario.")
    parser.add_argument("--execution-id", help="Filtre par execution_id.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config()
    store = HistoryStore(config.runtime.history_file)
    rows = store.read(
        limit=args.limit,
        status=args.status,
        slot_id=args.slot_id,
        scenario_id=args.scenario_id,
        execution_id=args.execution_id,
    )
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
