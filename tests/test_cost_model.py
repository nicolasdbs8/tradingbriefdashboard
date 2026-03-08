from src.execution.cost_model import (
    ExecutionCostAssumptions,
    effective_stop_distance_rate,
    net_rr,
    passes_cost_filter,
    round_trip_cost_rate,
)


def test_effective_stop_distance_rate():
    costs = ExecutionCostAssumptions(0.0026, 0.0026, 0.001)
    assert round_trip_cost_rate(costs) == 0.0062
    assert effective_stop_distance_rate(0.01, costs) == 0.0162


def test_rr_net_lower_than_gross():
    costs = ExecutionCostAssumptions(0.002, 0.002, 0.001)
    rr = net_rr(100, 98, 104, costs)
    assert rr < 2.0


def test_cost_filter():
    costs = ExecutionCostAssumptions(0.002, 0.002, 0.001)
    ok, _ = passes_cost_filter(0.02, 2.0, costs, 0.35, 1.6)
    assert ok is True
    ok, _ = passes_cost_filter(0.005, 1.0, costs, 0.35, 1.6)
    assert ok is False
