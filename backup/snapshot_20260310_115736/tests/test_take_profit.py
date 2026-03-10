from src.execution.take_profit import compute_tp_plan


def test_tp_long():
    plan = compute_tp_plan(100, 95, "LONG")
    assert plan.r_value == 5
    prices = [lvl.price for lvl in plan.levels]
    assert prices == [105, 110, 115]
    sizes = sum(lvl.size_pct for lvl in plan.levels)
    assert abs(sizes - 1.0) < 1e-9


def test_tp_short():
    plan = compute_tp_plan(100, 105, "SHORT")
    assert plan.r_value == 5
    prices = [lvl.price for lvl in plan.levels]
    assert prices == [95, 90, 85]
    sizes = sum(lvl.size_pct for lvl in plan.levels)
    assert abs(sizes - 1.0) < 1e-9
