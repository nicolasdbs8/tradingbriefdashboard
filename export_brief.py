from __future__ import annotations

import json
from pathlib import Path

from brief_engine import generate_trading_brief


def main() -> int:
    data = generate_trading_brief()
    Path("brief.json").write_text(json.dumps(data["data"], indent=2), encoding="utf-8")
    print("Wrote brief.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
