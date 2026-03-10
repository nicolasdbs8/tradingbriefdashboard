from src.report import _level_source_bonus


def test_level_source_bonus_weighting_order() -> None:
    weights = {"1d": 1.0, "4h": 0.5, "1h": 0.0}
    assert _level_source_bonus("1d", True, weights) > _level_source_bonus("4h", True, weights)
    assert _level_source_bonus("4h", True, weights) > _level_source_bonus("1h", True, weights)
