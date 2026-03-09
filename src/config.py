from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Dict, List

import yaml


@dataclass
class Config:
    exchange: str
    fallback_exchange: str
    symbol: str
    timeframes: List[str]
    lookback: Dict[str, int]
    ema_fast: int
    ema_slow: int
    rsi: int
    atr: int
    vwap_session: str
    volume_sma: int
    ema_slope_bars: int
    log_level: str
    levels_tolerance_pct: Dict[str, float]
    critical_level_daily_threshold_pct: float
    levels: Dict[str, list[float]]
    heatmap_name: str
    heatmap_note: str
    futures_oi_symbol: str
    futures_funding_symbol: str
    derivatives_fallback_provider: str
    bybit_base_url: str
    bybit_category: str
    bybit_symbol: str
    fee_mode: str
    slippage_rate: float
    kraken_pair: str
    fallback_fee_maker: float
    fallback_fee_taker: float
    max_cost_to_stop_ratio: float
    min_rr_net: float
    probability_engine_enabled: bool
    probability_engine_weights: Dict[str, float]
    probability_engine_adjustments: Dict[str, float]
    sweep_min_sweep_pct: float
    sweep_atr_multiplier: float
    sweep_reclaim_confirmation_bars: int
    sweep_breakout_volume_multiplier: float
    setup_preset_name: str
    setup_entry_mode: str
    setup_retest_buffer_atr: float
    setup_stop_atr_mult: float
    setup_target_timeframe: str
    alerts_enabled: bool
    alerts_min_setup_score: int
    alerts_require_trade_gate: bool
    alerts_require_active_setup: bool
    alerts_require_active_event: bool
    alerts_allowed_active_events: list[str]
    alerts_cooldown_minutes: int
    alerts_heads_up_enabled: bool
    alerts_heads_up_min_setup_score: float
    alerts_heads_up_require_trade_gate: bool
    alerts_heads_up_require_no_active_setup: bool
    alerts_heads_up_require_signal_hint: bool
    alerts_heads_up_max_distance_pct: float
    alerts_heads_up_cooldown_minutes: int


def _load_setup_preset(data: dict) -> tuple[str, dict]:
    setup_cfg = data.get("setup", {})
    presets = setup_cfg.get("presets", {})
    active = setup_cfg.get("active_preset", "intraday")
    preset = presets.get(active) or presets.get("intraday") or {}
    return active, preset


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    prob_cfg = data.get("probability_engine", {})
    prob_weights = prob_cfg.get("weights", {})
    prob_adjustments = prob_cfg.get("conditional_adjustments", {})
    sweep_cfg = data.get("sweep_detection", {})
    preset_name, preset_cfg = _load_setup_preset(data)
    alerts_cfg = data.get("alerts", {})
    heads_up_cfg = alerts_cfg.get("heads_up", {})

    exchange = os.getenv("DATA_EXCHANGE", data["data"]["exchange"])
    fallback_exchange = os.getenv("DATA_FALLBACK_EXCHANGE", data["data"]["fallback_exchange"])
    symbol = os.getenv("DATA_SYMBOL", data["data"]["symbol"])

    return Config(
        exchange=exchange,
        fallback_exchange=fallback_exchange,
        symbol=symbol,
        timeframes=data["data"]["timeframes"],
        lookback=data["data"]["lookback"],
        ema_fast=data["indicators"]["ema_fast"],
        ema_slow=data["indicators"]["ema_slow"],
        rsi=data["indicators"]["rsi"],
        atr=data["indicators"]["atr"],
        vwap_session=data["indicators"]["vwap_session"],
        volume_sma=data["indicators"]["volume_sma"],
        ema_slope_bars=data["indicators"]["ema_slope_bars"],
        log_level=data["logging"]["level"],
        levels_tolerance_pct=data["report"]["levels_tolerance_pct"],
        critical_level_daily_threshold_pct=float(
            data["report"].get("critical_level_daily_threshold_pct", 0.008)
        ),
        levels=data["report"]["levels"],
        heatmap_name=data["report"]["heatmap"]["name"],
        heatmap_note=data["report"]["heatmap"]["note"],
        futures_oi_symbol=data["derivatives"]["binance_futures"]["oi_symbol"],
        futures_funding_symbol=data["derivatives"]["binance_futures"]["funding_symbol"],
        derivatives_fallback_provider=data["derivatives"]["fallback"]["provider"],
        bybit_base_url=data["derivatives"]["fallback"]["bybit"]["base_url"],
        bybit_category=data["derivatives"]["fallback"]["bybit"]["category"],
        bybit_symbol=data["derivatives"]["fallback"]["bybit"]["symbol"],
        fee_mode=data["execution"]["fee_mode"],
        slippage_rate=float(data["execution"]["slippage_rate"]),
        kraken_pair=data["execution"]["kraken_pair"],
        fallback_fee_maker=float(data["execution"]["fallback_fees"]["maker"]),
        fallback_fee_taker=float(data["execution"]["fallback_fees"]["taker"]),
        max_cost_to_stop_ratio=float(data["filters"]["max_cost_to_stop_ratio"]),
        min_rr_net=float(data["filters"]["min_rr_net"]),
        probability_engine_enabled=bool(prob_cfg.get("enabled", True)),
        probability_engine_weights={
            "htf_trend": float(prob_weights.get("htf_trend", 0)),
            "location": float(prob_weights.get("location", 0)),
            "liquidity": float(prob_weights.get("liquidity", 0)),
            "momentum": float(prob_weights.get("momentum", 0)),
            "derivatives": float(prob_weights.get("derivatives", 0)),
        },
        probability_engine_adjustments={
            "sweep_detected_long_pct": float(prob_adjustments.get("sweep_detected_long_pct", 0)),
            "break_confirmed_short_pct": float(prob_adjustments.get("break_confirmed_short_pct", 0)),
        },
        sweep_min_sweep_pct=float(sweep_cfg.get("min_sweep_pct", 0.001)),
        sweep_atr_multiplier=float(sweep_cfg.get("atr_multiplier", 0.2)),
        sweep_reclaim_confirmation_bars=int(sweep_cfg.get("reclaim_confirmation_bars", 1)),
        sweep_breakout_volume_multiplier=float(sweep_cfg.get("breakout_volume_multiplier", 1.2)),
        setup_preset_name=str(preset_name),
        setup_entry_mode=str(preset_cfg.get("entry_mode", "retest")),
        setup_retest_buffer_atr=float(preset_cfg.get("retest_buffer_atr", 0.1)),
        setup_stop_atr_mult=float(preset_cfg.get("stop_atr_mult", 0.5)),
        setup_target_timeframe=str(preset_cfg.get("target_timeframe", "4h")),
        alerts_enabled=bool(alerts_cfg.get("enabled", True)),
        alerts_min_setup_score=int(alerts_cfg.get("min_setup_score", 7)),
        alerts_require_trade_gate=bool(alerts_cfg.get("require_trade_gate", True)),
        alerts_require_active_setup=bool(alerts_cfg.get("require_active_setup", True)),
        alerts_require_active_event=bool(alerts_cfg.get("require_active_event", True)),
        alerts_allowed_active_events=[
            str(item) for item in alerts_cfg.get("allowed_active_events", ["sweep_reclaim", "break"])
        ],
        alerts_cooldown_minutes=int(alerts_cfg.get("cooldown_minutes", 90)),
        alerts_heads_up_enabled=bool(heads_up_cfg.get("enabled", True)),
        alerts_heads_up_min_setup_score=float(heads_up_cfg.get("min_setup_score", 6.5)),
        alerts_heads_up_require_trade_gate=bool(heads_up_cfg.get("require_trade_gate", True)),
        alerts_heads_up_require_no_active_setup=bool(heads_up_cfg.get("require_no_active_setup", True)),
        alerts_heads_up_require_signal_hint=bool(heads_up_cfg.get("require_signal_hint", True)),
        alerts_heads_up_max_distance_pct=float(heads_up_cfg.get("max_distance_pct", 0.35)),
        alerts_heads_up_cooldown_minutes=int(heads_up_cfg.get("cooldown_minutes", 60)),
    )
