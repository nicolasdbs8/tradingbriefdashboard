from src.capital import compute_capital_plan


def test_capital_plan_basic():
    plan = compute_capital_plan(1000.0, active_alloc_pct=0.40, risk_per_trade_pct=0.005)
    assert plan.total_equity_usd == 1000.0
    assert plan.active_capital_usd == 400.0
    assert plan.reserve_capital_usd == 600.0
    assert plan.risk_per_trade_usd == 2.0
