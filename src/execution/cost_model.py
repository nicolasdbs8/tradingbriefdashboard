from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecutionCostAssumptions:
    entry_fee_rate: float
    exit_fee_rate: float
    slippage_rate: float
    mode: str = "taker"


def round_trip_cost_rate(costs: ExecutionCostAssumptions) -> float:
    return costs.entry_fee_rate + costs.exit_fee_rate + costs.slippage_rate


def effective_stop_distance_rate(stop_distance_rate: float, costs: ExecutionCostAssumptions) -> float:
    return stop_distance_rate + round_trip_cost_rate(costs)


def net_rr(entry: float, stop: float, target: float, costs: ExecutionCostAssumptions) -> float:
    gross_reward = abs(target - entry) / entry
    gross_risk = abs(entry - stop) / entry
    cost = round_trip_cost_rate(costs)
    net_reward = max(gross_reward - cost, 0.0)
    net_risk = gross_risk + cost
    if net_risk == 0:
        return 0.0
    return net_reward / net_risk


def passes_cost_filter(
    stop_distance_rate: float,
    rr_net: float,
    costs: ExecutionCostAssumptions,
    max_cost_to_stop_ratio: float,
    min_rr_net: float,
) -> tuple[bool, str]:
    cost = round_trip_cost_rate(costs)
    ratio = cost / stop_distance_rate if stop_distance_rate > 0 else 999.0
    if ratio > max_cost_to_stop_ratio:
        return False, f"cost/stop {ratio:,.2f} > {max_cost_to_stop_ratio:,.2f}"
    if rr_net < min_rr_net:
        return False, f"rr_net {rr_net:,.2f} < {min_rr_net:,.2f}"
    return True, "pass"
