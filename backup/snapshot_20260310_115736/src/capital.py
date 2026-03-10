from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapitalPlan:
    total_equity_usd: float
    active_capital_usd: float
    reserve_capital_usd: float
    risk_per_trade_usd: float


def compute_capital_plan(
    usdc_equity: float,
    active_alloc_pct: float = 0.40,
    risk_per_trade_pct: float = 0.005,
) -> CapitalPlan:
    total = max(usdc_equity, 0.0)
    active = total * active_alloc_pct
    reserve = total - active
    risk = active * risk_per_trade_pct
    return CapitalPlan(
        total_equity_usd=total,
        active_capital_usd=active,
        reserve_capital_usd=reserve,
        risk_per_trade_usd=risk,
    )
