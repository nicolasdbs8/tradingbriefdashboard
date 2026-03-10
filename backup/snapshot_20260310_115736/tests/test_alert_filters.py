from dataclasses import dataclass

from src.alerts.check import _evaluate_heads_up, _evaluate_trigger


@dataclass
class DummyCfg:
    alerts_enabled: bool = True
    alerts_min_setup_score: int = 7
    alerts_require_trade_gate: bool = True
    alerts_require_active_setup: bool = True
    alerts_require_active_event: bool = True
    alerts_allowed_active_events: list[str] = None
    alerts_heads_up_enabled: bool = True
    alerts_heads_up_min_setup_score: float = 6.5
    alerts_heads_up_require_trade_gate: bool = True
    alerts_heads_up_require_no_active_setup: bool = True
    alerts_heads_up_require_signal_hint: bool = True
    alerts_heads_up_max_distance_pct: float = 0.35

    def __post_init__(self) -> None:
        if self.alerts_allowed_active_events is None:
            self.alerts_allowed_active_events = ["sweep_reclaim", "break"]


def _base_data() -> dict:
    return {
        "setup_score": {"final": 7.5, "trade_gate": True},
        "trade": {
            "active_setup": "SHORT",
            "filters": {
                "cost_pass": True,
                "vwap_pass": True,
                "probability_pass": True,
                "probability_heads_up_pass": True,
            },
        },
        "level_event": {"active_event": "break", "sweep_detected": False, "reclaim_confirmed": False},
        "liquidity_distance": {"min_pct": 0.2},
    }


def test_trigger_requires_all_filters() -> None:
    cfg = DummyCfg()
    data = _base_data()
    data["trade"]["filters"]["cost_pass"] = False

    decision = _evaluate_trigger(data, cfg)

    assert not decision.favorable
    assert decision.reason == "cost_fail"


def test_heads_up_keeps_signal_and_reports_blockers() -> None:
    cfg = DummyCfg()
    data = _base_data()
    data["trade"]["active_setup"] = "NONE"
    data["level_event"]["active_event"] = "none"
    data["level_event"]["sweep_detected"] = True
    data["trade"]["filters"]["vwap_pass"] = False
    data["trade"]["filters"]["probability_heads_up_pass"] = False

    decision = _evaluate_heads_up(data, cfg)

    assert decision.favorable
    assert "vwap_mismatch" in decision.why_blocked
    assert "probability_below_heads_up_threshold" in decision.why_blocked
