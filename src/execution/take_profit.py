from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class TakeProfitLevel:
    price: float
    size_pct: float
    r_multiple: float


@dataclass
class TakeProfitPlan:
    r_value: float
    levels: List[TakeProfitLevel]


def compute_tp_plan(entry: float, stop: float, side: str) -> TakeProfitPlan:
    r_value = abs(entry - stop)
    if r_value == 0:
        return TakeProfitPlan(r_value=0.0, levels=[])

    if side.upper() == "SHORT":
        tp1 = entry - 1 * r_value
        tp2 = entry - 2 * r_value
        tp3 = entry - 3 * r_value
    else:
        tp1 = entry + 1 * r_value
        tp2 = entry + 2 * r_value
        tp3 = entry + 3 * r_value

    levels = [
        TakeProfitLevel(price=tp1, size_pct=0.30, r_multiple=1.0),
        TakeProfitLevel(price=tp2, size_pct=0.40, r_multiple=2.0),
        TakeProfitLevel(price=tp3, size_pct=0.30, r_multiple=3.0),
    ]
    return TakeProfitPlan(r_value=r_value, levels=levels)
