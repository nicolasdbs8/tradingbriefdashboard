from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import pandas as pd

from .capital import CapitalPlan
from .derivatives.models import DerivativesSnapshot
from .derivatives.interpret import synthese
from .execution.cost_model import (
    ExecutionCostAssumptions,
    effective_stop_distance_rate,
    net_rr,
    passes_cost_filter,
    round_trip_cost_rate,
)
from .execution.take_profit import TakeProfitLevel, compute_tp_plan
from .probability.engine import compute_directional_probability


@dataclass
class TimeframeMetrics:
    timeframe: str
    price: float
    ema_fast: float
    ema_slow: float
    ema_slope: float
    rsi: float
    atr: float
    volume: float
    volume_sma: float
    vwap: Optional[float] = None
    vwap_series: Optional[pd.Series] = None
    recent_high: Optional[float] = None
    recent_low: Optional[float] = None
    atr_series: Optional[pd.Series] = None


@dataclass
class BriefReport:
    symbol: str
    exchange: str
    metrics: Dict[str, TimeframeMetrics]
    capital: CapitalPlan
    levels: Dict[str, list[float]]
    levels_tolerance_pct: Dict[str, float]
    heatmap_name: str
    heatmap_note: str
    triggers: Dict[str, object]
    costs: ExecutionCostAssumptions
    max_cost_to_stop_ratio: float
    min_rr_net: float
    cost_gate_enabled: bool
    vwap_gate_enabled: bool
    probability_gate_enabled: bool
    probability_gate_trigger_min: float
    probability_gate_heads_up_min: float
    level_source_weight_enabled: bool
    level_source_weights: Dict[str, float]
    liquidity_gate_enabled: bool
    liquidity_gate_max_distance_pct: float
    probability_engine_enabled: bool
    probability_engine_weights: Dict[str, float]
    probability_engine_adjustments: Dict[str, float]
    setup_preset_name: str
    setup_entry_mode: str
    setup_retest_buffer_atr: float
    setup_stop_atr_mult: float
    setup_target_timeframe: str
    derivatives: Optional[DerivativesSnapshot] = None


def _select_target_levels(
    target_timeframe: str,
    range_high_1h: float,
    range_low_1h: float,
    range_high_4h: float,
    range_low_4h: float,
) -> Tuple[float, float]:
    if target_timeframe == "4h":
        return range_high_4h, range_low_4h
    return range_high_1h, range_low_1h


def _setup_entry(level: float, atr: float, side: str, entry_mode: str, retest_buffer_atr: float) -> float:
    if entry_mode == "retest":
        buffer = atr * retest_buffer_atr
        return level + buffer if side == "LONG" else level - buffer
    return level


def _setup_plan(
    level: float,
    atr: float,
    side: str,
    entry_mode: str,
    retest_buffer_atr: float,
    stop_atr_mult: float,
    target_high: float,
    target_low: float,
) -> Tuple[float, float, float]:
    entry = _setup_entry(level, atr, side, entry_mode, retest_buffer_atr)
    if side == "LONG":
        stop = entry - atr * stop_atr_mult
        target = target_high
    else:
        stop = entry + atr * stop_atr_mult
        target = target_low
    return entry, stop, target


def _apply_costs_to_levels(
    entry: float,
    stop: float,
    target: float,
    side: str,
    costs: ExecutionCostAssumptions,
) -> Tuple[float, float]:
    cost_rate = round_trip_cost_rate(costs)
    if entry == 0:
        return stop, target
    offset = entry * cost_rate
    if side == "LONG":
        return stop - offset, target + offset
    return stop + offset, target - offset


def _cap_tp_levels(levels: list[TakeProfitLevel], target: float, side: str) -> list[TakeProfitLevel]:
    capped: list[TakeProfitLevel] = []
    for lvl in levels:
        price = lvl.price
        if side == "LONG" and price > target:
            price = target
        elif side == "SHORT" and price < target:
            price = target
        capped.append(TakeProfitLevel(price=price, size_pct=lvl.size_pct, r_multiple=lvl.r_multiple))
    return capped


def _fit_tp_levels_to_target(
    entry: float,
    target: float,
    side: str,
    levels: list[TakeProfitLevel],
) -> list[TakeProfitLevel]:
    if not levels:
        return levels
    sign = 1.0 if side == "LONG" else -1.0
    target_dist = sign * (target - entry)
    if target_dist <= 0:
        return levels
    max_dist = sign * (levels[-1].price - entry)
    if max_dist <= 0 or target_dist >= max_dist:
        return levels
    scale = target_dist / max_dist
    fitted: list[TakeProfitLevel] = []
    for idx, lvl in enumerate(levels):
        base_dist = sign * (lvl.price - entry)
        new_price = entry + sign * (base_dist * scale)
        if idx == len(levels) - 1:
            new_price = target
        fitted.append(TakeProfitLevel(price=new_price, size_pct=lvl.size_pct, r_multiple=lvl.r_multiple))
    return fitted


def _liquidity_distance_for_event(
    active_event: str,
    below_pct: float,
    above_pct: float,
    critical_pct: float,
    critical_regime: str,
) -> Tuple[float, str]:
    if active_event == "sweep_reclaim":
        return below_pct, "below"
    if active_event == "break":
        return above_pct, "above"
    if critical_regime in {"bullish_breakout", "range_pullback"}:
        return critical_pct, "critical"
    return min(below_pct, above_pct), "nearest"


def _effective_liquidity_max_distance_pct(base_max_pct: float, atr: float, price: float) -> float:
    if price <= 0:
        return base_max_pct
    atr_pct = abs(atr / price) * 100
    # Adaptive threshold: widen in high-volatility regimes while capping drift.
    dynamic_pct = min(1.2, atr_pct * 0.8)
    return max(base_max_pct, dynamic_pct)


def _level_source_bonus(level_source: str, enabled: bool, weights: Dict[str, float]) -> float:
    if not enabled:
        return 0.0
    return float(weights.get(level_source, 0.0))


def _vwap_pass(active_event: str, vwap_side: str, enabled: bool) -> bool:
    if not enabled:
        return True
    if active_event == "sweep_reclaim":
        return vwap_side == "above"
    if active_event == "break":
        return vwap_side == "below"
    return True


def _probability_pass(probability: Optional[dict], min_threshold: float, enabled: bool) -> tuple[bool, float]:
    if not enabled:
        return True, 0.0
    if not probability:
        return True, 0.0
    prob_max = max(probability.get("long_probability_pct", 0.0), probability.get("short_probability_pct", 0.0))
    return prob_max >= min_threshold, prob_max


def _vol_state(volume: float, volume_sma: float) -> str:
    if volume_sma <= 0:
        return "n/a"
    if volume > volume_sma * 1.5:
        return "strong"
    if volume < volume_sma * 0.7:
        return "weak"
    return "normal"


def _ema_relation(price: float, ema_fast: float, ema_slow: float) -> str:
    if price > ema_fast > ema_slow:
        return "price > EMA50 > EMA200"
    if price < ema_fast < ema_slow:
        return "price < EMA50 < EMA200"
    return "mixed"


def _trend_direction(price: float, ema_fast: float, ema_slow: float) -> str:
    if price > ema_fast > ema_slow:
        return "up"
    if price < ema_fast < ema_slow:
        return "down"
    return "neutral"


def _conclusion(price: float, ema_fast: float, ema_slow: float, rsi: float) -> str:
    if price > ema_fast > ema_slow and rsi >= 55:
        return "bullish"
    if price < ema_fast < ema_slow and rsi <= 45:
        return "bearish"
    return "neutral"


def _structure(recent_high: float, recent_low: float, atr: float) -> str:
    if atr <= 0:
        return "n/a"
    rng = recent_high - recent_low
    if rng <= atr * 2.0:
        return "range"
    return "trend"


def _compression(atr_series: pd.Series) -> str:
    if len(atr_series) < 20:
        return "n/a"
    recent = atr_series.tail(10).mean()
    prev = atr_series.tail(20).head(10).mean()
    if prev == 0:
        return "n/a"
    return "yes" if recent < prev * 0.9 else "no"


def _atr_trend(atr_series: pd.Series) -> str:
    if len(atr_series) < 20:
        return "n/a"
    recent = atr_series.tail(10).mean()
    prev = atr_series.tail(20).head(10).mean()
    if prev == 0:
        return "n/a"
    if recent > prev * 1.1:
        return "up"
    if recent < prev * 0.9:
        return "down"
    return "flat"


def _bias(daily: TimeframeMetrics, h4: TimeframeMetrics) -> str:
    d = _conclusion(daily.price, daily.ema_fast, daily.ema_slow, daily.rsi)
    h = _conclusion(h4.price, h4.ema_fast, h4.ema_slow, h4.rsi)
    if d == "bullish" and h == "bullish":
        return "LONG"
    if d == "bearish" and h == "bearish":
        return "SHORT"
    return "RANGE"


def _scenario_levels(price: float, atr_val: float) -> Tuple[float, float]:
    if atr_val <= 0:
        return price, price
    return price + atr_val * 0.8, price - atr_val * 0.8


def _sizing(risk_amount: float, entry: float, stop: float) -> Tuple[float, float]:
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0, risk_amount
    position_size = risk_amount / stop_dist
    return position_size, risk_amount


def _nearest_levels(levels: list[float], price: float) -> Tuple[Optional[float], Optional[float]]:
    if not levels:
        return None, None
    levels_sorted = sorted(levels)
    support = None
    resistance = None
    for lvl in levels_sorted:
        if lvl <= price:
            support = lvl
        if lvl > price and resistance is None:
            resistance = lvl
    return support, resistance


def _major_levels(levels: list[float], price: float) -> Tuple[Optional[float], Optional[float]]:
    if not levels:
        return None, None
    levels_sorted = sorted(levels)
    below = [lvl for lvl in levels_sorted if lvl < price]
    above = [lvl for lvl in levels_sorted if lvl > price]
    major_support = below[-2] if len(below) >= 2 else (below[-1] if below else None)
    major_resistance = above[1] if len(above) >= 2 else (above[0] if above else None)
    return major_support, major_resistance


def _range_location(price: float, low: float, high: float) -> str:
    if low >= high:
        return "n/a"
    if price < low:
        return "below range"
    if price > high:
        return "above range"
    pct = (price - low) / (high - low)
    if pct < 0.33:
        return "lower third"
    if pct < 0.66:
        return "middle third"
    return "upper third"


def _distance_pct(a: float, b: Optional[float]) -> float:
    if not b or b == 0:
        return 0.0
    return (a - b) / b * 100


def _distance_pct_abs(a: float, b: Optional[float]) -> float:
    return abs(_distance_pct(a, b))


def _trend_strength(ema_slope: float, atr: float) -> str:
    if atr <= 0:
        return "n/a"
    ratio = abs(ema_slope) / atr
    if ratio >= 0.12:
        return "strong"
    if ratio >= 0.06:
        return "moderate"
    return "weak"


def _vwap_trend(vwap_series: Optional[pd.Series]) -> str:
    if vwap_series is None or len(vwap_series) < 6:
        return "n/a"
    recent = vwap_series.iloc[-1]
    prev = vwap_series.iloc[-6]
    if prev == 0:
        return "n/a"
    change = (recent - prev) / prev
    if change > 0.002:
        return "rising"
    if change < -0.002:
        return "falling"
    return "flat"


def build_metrics(
    timeframe: str,
    df: pd.DataFrame,
    recent_window: int = 20,
) -> TimeframeMetrics:
    last = df.iloc[-1]
    recent = df.tail(recent_window)
    return TimeframeMetrics(
        timeframe=timeframe,
        price=float(last["close"]),
        ema_fast=float(last["ema_fast"]),
        ema_slow=float(last["ema_slow"]),
        ema_slope=float(last["ema_slope"]),
        rsi=float(last["rsi"]),
        atr=float(last["atr"]),
        volume=float(last["volume"]),
        volume_sma=float(last["volume_sma"]),
        vwap=float(last["vwap"]) if "vwap" in df.columns else None,
        vwap_series=df["vwap"] if "vwap" in df.columns else None,
        recent_high=float(recent["high"].max()),
        recent_low=float(recent["low"].min()),
        atr_series=df["atr"],
    )


def format_brief(report: BriefReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    daily = report.metrics["1d"]
    h4 = report.metrics["4h"]
    h1 = report.metrics["1h"]
    m15 = report.metrics["15m"]

    h4_structure = _structure(h4.recent_high or 0, h4.recent_low or 0, h4.atr)
    h1_structure = _structure(h1.recent_high or 0, h1.recent_low or 0, h1.atr)
    h1_compression = _compression(h1.atr_series) if h1.atr_series is not None else "n/a"
    atr_trend = _atr_trend(h1.atr_series) if h1.atr_series is not None else "n/a"
    bias = _bias(daily, h4)
    vol_state = _vol_state(h1.volume, h1.volume_sma)

    vwap_side = "above" if m15.vwap and m15.price > m15.vwap else "below"
    vwap_trend = _vwap_trend(m15.vwap_series)
    volume_breakout = report.triggers.get("volume_breakout", False)
    breakout_level = report.triggers.get("breakout_level", m15.recent_high or m15.price)
    sweep_level = report.triggers.get("sweep_level", m15.recent_low or m15.price)
    breakout_now = report.triggers.get("breakout_now", False)
    retest_now = report.triggers.get("retest_now", False)
    sweep_reclaim_now = report.triggers.get("sweep_reclaim_now", False)

    probability = None
    if report.probability_engine_enabled:
        probability = compute_directional_probability(
            daily=daily,
            h4=h4,
            h1=h1,
            m15=m15,
            volume_breakout=volume_breakout,
            derivatives=report.derivatives,
            weights=report.probability_engine_weights,
            event_flags={
                "sweep_detected": report.triggers.get("sweep_detected", False),
                "break_confirmed": report.triggers.get("break_confirmed", False),
            },
            adjustments=report.probability_engine_adjustments,
        )

    daily_levels = report.levels.get("1d", [])
    daily_support, daily_resistance = _nearest_levels(daily_levels, daily.price)
    major_support, major_resistance = _major_levels(daily_levels, daily.price)

    range_high_4h = h4.recent_high or h4.price
    range_low_4h = h4.recent_low or h4.price
    range_high_1h = h1.recent_high or h1.price
    range_low_1h = h1.recent_low or h1.price
    setup_level = report.triggers.get("critical_level", range_low_1h)
    setup_level_long = report.triggers.get("critical_level_long", setup_level)
    setup_level_short = report.triggers.get("critical_level_short", setup_level)
    location_1h = _range_location(h1.price, range_low_1h, range_high_1h)
    vwap_dist_pct = _distance_pct(m15.price, m15.vwap)
    target_high, target_low = _select_target_levels(
        report.setup_target_timeframe,
        range_high_1h,
        range_low_1h,
        range_high_4h,
        range_low_4h,
    )
    long_entry, long_stop, long_target = _setup_plan(
        setup_level_long,
        h1.atr,
        "LONG",
        report.setup_entry_mode,
        report.setup_retest_buffer_atr,
        report.setup_stop_atr_mult,
        target_high,
        target_low,
    )
    long_stop, long_target = _apply_costs_to_levels(
        long_entry,
        long_stop,
        long_target,
        "LONG",
        report.costs,
    )
    short_entry, short_stop, short_target = _setup_plan(
        setup_level_short,
        h1.atr,
        "SHORT",
        report.setup_entry_mode,
        report.setup_retest_buffer_atr,
        report.setup_stop_atr_mult,
        target_high,
        target_low,
    )
    short_stop, short_target = _apply_costs_to_levels(
        short_entry,
        short_stop,
        short_target,
        "SHORT",
        report.costs,
    )

    daily_trend = _conclusion(daily.price, daily.ema_fast, daily.ema_slow, daily.rsi)
    h4_trend = _conclusion(h4.price, h4.ema_fast, h4.ema_slow, h4.rsi)
    daily_direction = _trend_direction(daily.price, daily.ema_fast, daily.ema_slow)
    market_type = "RANGE" if h4_structure == "range" else "TREND"
    if daily_direction in {"up", "down"}:
        daily_structure = "trend"
    else:
        daily_structure = "corrective"
    h4_direction = _trend_direction(h4.price, h4.ema_fast, h4.ema_slow)
    if h4_structure == "trend" and h4_direction == "neutral":
        if h4.ema_slope > 0:
            h4_direction = "up"
        elif h4.ema_slope < 0:
            h4_direction = "down"
        else:
            h4_structure = "range"
    trend_strength = _trend_strength(h4.ema_slope, h4.atr)

    trade_side = "LONG"
    if bias == "LONG":
        entry, stop, target = long_entry, long_stop, long_target
    elif bias == "SHORT":
        trade_side = "SHORT"
        entry, stop, target = short_entry, short_stop, short_target
    else:
        trade_side = "NONE"
        entry = h1.price
        stop = h1.price - h1.atr * 1.0
        target = h1.price + h1.atr * 2.0

    stop_distance_rate = abs(entry - stop) / entry if entry != 0 else 0.0
    cost_rate = round_trip_cost_rate(report.costs)
    effective_stop_rate = effective_stop_distance_rate(stop_distance_rate, report.costs)
    size_usd = report.capital.risk_per_trade_usd / effective_stop_rate if effective_stop_rate > 0 else 0.0
    position_size = size_usd / entry if entry != 0 else 0.0
    rr_gross = abs((target - entry) / (entry - stop)) if (entry - stop) != 0 else 0.0
    rr_net = net_rr(entry, stop, target, report.costs)
    stop_distance = abs(entry - stop)
    exposure_usd = size_usd
    exposure_pct = (exposure_usd / report.capital.active_capital_usd * 100) if report.capital.active_capital_usd > 0 else 0.0

    trend_score = 2 if daily_trend == h4_trend and daily_trend != "neutral" else 1 if daily_trend == h4_trend else 0
    location_score = 2 if location_1h in {"lower third", "upper third"} else 1 if location_1h == "middle third" else 0
    liquidity_score = 1
    tol_pct = report.levels_tolerance_pct.get("1h", 0.003) * 100
    if daily_support and abs(_distance_pct(h1.price, daily_support)) <= tol_pct:
        liquidity_score = 2
    momentum_score = 2 if (h1.rsi > 60 or h1.rsi < 40) and vol_state == "strong" else 1 if (h1.rsi > 55 or h1.rsi < 45) else 0
    volatility_score = 2 if atr_trend == "up" else 1 if atr_trend == "flat" else 0
    level_source = str(report.triggers.get("critical_level_source", "1h"))
    level_source_bonus = _level_source_bonus(level_source, report.level_source_weight_enabled, report.level_source_weights)
    total_score = trend_score + location_score + liquidity_score + momentum_score + volatility_score
    final_score = float(total_score)
    active_event = report.triggers.get("active_event", "none")
    if location_score == 0:
        final_score = min(final_score, 6)
    if liquidity_score == 0:
        final_score = min(final_score, 6)
    if trend_score == 0 and active_event != "sweep_reclaim":
        final_score = min(final_score, 5)
    final_score = min(final_score + level_source_bonus, 10.0)

    if final_score <= 5:
        setup_class = "IGNORE"
    elif final_score == 6:
        setup_class = "WATCHLIST"
    elif final_score <= 8:
        setup_class = "VALID"
    else:
        setup_class = "PRIORITY"

    liquidity_below_pct = _distance_pct_abs(h1.price, range_low_1h)
    liquidity_above_pct = _distance_pct_abs(range_high_1h, h1.price)
    liquidity_asymmetry = "bearish" if liquidity_below_pct < liquidity_above_pct else "bullish" if liquidity_above_pct < liquidity_below_pct else "balanced"
    critical_regime = report.triggers.get("critical_regime", "range_pullback")
    critical_dist_pct = _distance_pct_abs(h1.price, setup_level)
    liquidity_event_dist_pct, liquidity_event_side = _liquidity_distance_for_event(
        active_event,
        liquidity_below_pct,
        liquidity_above_pct,
        critical_dist_pct,
        critical_regime,
    )
    liquidity_max_distance_pct_effective = _effective_liquidity_max_distance_pct(
        report.liquidity_gate_max_distance_pct,
        h1.atr,
        h1.price,
    )
    cost_pass, cost_reason = passes_cost_filter(
        stop_distance_rate,
        rr_net,
        report.costs,
        report.max_cost_to_stop_ratio,
        report.min_rr_net,
    )
    probability_pass, prob_max = _probability_pass(
        probability,
        report.probability_gate_trigger_min,
        report.probability_gate_enabled,
    )
    probability_heads_up_pass = (
        (not report.probability_gate_enabled)
        or prob_max == 0.0
        or prob_max >= report.probability_gate_heads_up_min
    )
    vwap_pass = _vwap_pass(active_event, vwap_side, report.vwap_gate_enabled)
    liquidity_gate_pass = (
        (not report.liquidity_gate_enabled)
        or liquidity_event_dist_pct <= liquidity_max_distance_pct_effective
    )
    long_inversion_confirmed = bool(report.triggers.get("long_inversion_confirmed", True))
    short_inversion_confirmed = bool(report.triggers.get("short_inversion_confirmed", True))
    inversion_pass = (
        (active_event != "sweep_reclaim" or long_inversion_confirmed)
        and (active_event != "break" or short_inversion_confirmed)
    )
    cost_gate_pass = (not report.cost_gate_enabled) or cost_pass
    trade_gate = False
    trade_gate_reason = "setup_score below threshold"
    trade_gate_failures: list[str] = []
    trade_gate_warnings: list[str] = []
    if final_score >= 6:
        if location_score >= 1 and liquidity_score >= 1 and (trend_score >= 1 or prob_max >= 55):
            trade_gate = True
            trade_gate_reason = "passed location/liquidity and trend/probability checks"
        else:
            trade_gate_reason = "failed location/liquidity or trend/probability checks"
    if trade_gate and not cost_gate_pass:
        trade_gate_warnings.append(f"cost_warn:{cost_reason}")
    if trade_gate and not vwap_pass:
        trade_gate = False
        trade_gate_failures.append("vwap_mismatch")
    if trade_gate and not probability_pass:
        trade_gate = False
        trade_gate_failures.append("probability_below_threshold")
    if trade_gate and not liquidity_gate_pass:
        trade_gate = False
        trade_gate_failures.append("liquidity_too_far")
    if trade_gate and not inversion_pass:
        trade_gate = False
        trade_gate_failures.append("inversion_not_confirmed_2bars")
    if trade_gate_failures:
        trade_gate_reason = "; ".join(trade_gate_failures)
    elif trade_gate_warnings:
        trade_gate_reason = "passed with warnings"
    elif trade_gate and level_source_bonus > 0:
        trade_gate_reason = f"{trade_gate_reason}; level_source_bonus={level_source_bonus:,.1f} ({level_source})"

    active_setup = "NONE"
    if active_event == "sweep_reclaim":
        active_setup = "LONG"
    elif active_event == "break":
        active_setup = "SHORT"
    if not trade_gate:
        active_setup = "NONE"

    exec_entry = None
    exec_stop = None
    if active_setup == "LONG":
        exec_entry = long_entry
        exec_stop = long_stop
    elif active_setup == "SHORT":
        exec_entry = short_entry
        exec_stop = short_stop

    exec_stop_distance_rate = None
    if exec_entry is not None and exec_stop is not None and exec_entry != 0:
        exec_stop_distance_rate = abs(exec_entry - exec_stop) / exec_entry

    dist_to_low_pct = _distance_pct(h1.price, range_low_1h)
    dist_to_high_pct = _distance_pct(h1.price, range_high_1h)
    support_dist_pct = _distance_pct(daily_support, h1.price) if daily_support else 0.0
    resistance_dist_pct = _distance_pct(daily_resistance, h1.price) if daily_resistance else 0.0

    risk_move_pct = _distance_pct(entry, stop)

    trigger_breakout_dist = _distance_pct_abs(breakout_level, m15.price)
    trigger_sweep_dist = _distance_pct_abs(sweep_level, m15.price)

    key_level = setup_level

    bias_reason = f"{'bearish' if daily_direction == 'down' else 'bullish' if daily_direction == 'up' else 'neutral'} pullback zone"

    tp_plan = compute_tp_plan(entry, stop, trade_side)
    if tp_plan.levels:
        tp_plan.levels = _fit_tp_levels_to_target(entry, target, trade_side, tp_plan.levels)
        tp_plan.levels = _cap_tp_levels(tp_plan.levels, target, trade_side)
    tp_plan_long = compute_tp_plan(long_entry, long_stop, "LONG")
    if tp_plan_long.levels:
        tp_plan_long.levels = _fit_tp_levels_to_target(long_entry, long_target, "LONG", tp_plan_long.levels)
        tp_plan_long.levels = _cap_tp_levels(tp_plan_long.levels, long_target, "LONG")
    tp_plan_short = compute_tp_plan(short_entry, short_stop, "SHORT")
    if tp_plan_short.levels:
        tp_plan_short.levels = _fit_tp_levels_to_target(short_entry, short_target, "SHORT", tp_plan_short.levels)
        tp_plan_short.levels = _cap_tp_levels(tp_plan_short.levels, short_target, "SHORT")
    tp_lines: list[str] = []
    if tp_plan.levels:
        tp1, tp2, tp3 = tp_plan.levels
        tp_lines = [
            "TAKE PROFIT PLAN",
            "TP1:",
            f"price: {tp1.price:,.2f}",
            f"size: {tp1.size_pct*100:.0f}%",
            "R multiple: 1R",
            "TP2:",
            f"price: {tp2.price:,.2f}",
            f"size: {tp2.size_pct*100:.0f}%",
            "R multiple: 2R",
            "TP3:",
            f"price: {tp3.price:,.2f}",
            f"size: {tp3.size_pct*100:.0f}%",
            "R multiple: 3R",
            "After TP1 hit: stop_loss = entry_price (breakeven)",
        ]

        cost_rate = round_trip_cost_rate(report.costs)
        tp1_profit = size_usd * tp1.size_pct * (abs(tp1.price - entry) / entry - cost_rate)
        tp2_profit = size_usd * (tp1.size_pct + tp2.size_pct) * (abs(tp2.price - entry) / entry - cost_rate)
        tp3_profit = size_usd * (tp1.size_pct + tp2.size_pct + tp3.size_pct) * (abs(tp3.price - entry) / entry - cost_rate)
        tp_lines += [
            "Expected profit (approx net)",
            f"TP1 only: {tp1_profit:,.2f} USDC",
            f"TP2 reached: {tp2_profit:,.2f} USDC",
            f"TP3 reached: {tp3_profit:,.2f} USDC",
        ]

    probability_section: list[str] = []
    if probability:
        probability_section = [
            "DIRECTIONAL PROBABILITY",
            f"LONG probability: {probability['long_probability_pct']:,.1f}%",
            f"SHORT probability: {probability['short_probability_pct']:,.1f}%",
            f"Edge: {probability['edge']}",
            f"Confidence: {probability['confidence']}",
            f"Note: {probability['note']}",
            *(
                ["Conditional adjustments: " + "; ".join(probability["conditional_adjustments"])]
                if probability.get("conditional_adjustments")
                else []
            ),
            "",
            "FACTOR BREAKDOWN",
            *[
                f"- {factor['label']}: {factor['signed_score']:+.0f} ({factor['reason']})"
                for factor in probability["factors"]
            ],
            "",
        ]

    return "\n".join(
        [
            f"TRADING BRIEF - {report.symbol}",
            f"Date: {now}",
            f"Price: {m15.price:,.2f}",
            f"Exchange: {report.exchange}",
            "",
            "DATA",
            f"Symbol: {report.symbol}",
            f"Exchange: {report.exchange}",
            "TF loaded: 1D / 4H / 1H / 15m",
            "",
            "DAILY",
            f"Trend: {daily_direction}",
            f"Price: {daily.price:,.2f}",
            "Nearest levels",
            f"Support: {daily_support:,.2f} ({support_dist_pct:,.2f}%)" if daily_support else "Support: n/a",
            f"Resistance: {daily_resistance:,.2f} ({resistance_dist_pct:+,.2f}%)" if daily_resistance else "Resistance: n/a",
            "Major levels",
            f"Support: {major_support:,.2f}" if major_support else "Support: n/a",
            f"Resistance: {major_resistance:,.2f}" if major_resistance else "Resistance: n/a",
            "EMA",
            f"{_ema_relation(daily.price, daily.ema_fast, daily.ema_slow)}",
            f"EMA50 slope: {daily.ema_slope:,.2f}",
            "Momentum",
            f"RSI: {daily.rsi:,.2f}",
            f"ATR: {daily.atr:,.2f}",
            "Conclusion",
            f"Structure: {daily_structure} | Trend direction: {daily_direction}",
            "",
            "4H STRUCTURE",
            f"Structure: {h4_structure}",
            f"Direction: {h4_direction}" if h4_structure == "trend" else "Direction: n/a",
            f"Range: {range_low_4h:,.2f} -> {range_high_4h:,.2f}" if h4_structure == "range" else "Range: n/a",
            f"Pullback structure: 1H {h1_structure}",
            "EMA",
            f"Structure: {_ema_relation(h4.price, h4.ema_fast, h4.ema_slow)}",
            "Momentum",
            f"RSI: {h4.rsi:,.2f}",
            f"ATR: {h4.atr:,.2f}",
            "Conclusion",
            "Market inside range" if h4_structure == "range" else "Market trending",
            "",
            "1H STRUCTURE",
            f"Range high: {range_high_1h:,.2f}",
            f"Range low: {range_low_1h:,.2f}",
            "Current position",
            f"Price: {h1.price:,.2f}",
            f"Location: {location_1h}",
            f"Distance to range low: {abs(dist_to_low_pct):,.2f}%",
            f"Distance to range high: {abs(dist_to_high_pct):,.2f}%",
            "Momentum",
            f"RSI: {h1.rsi:,.2f}",
            f"Volume: {vol_state}",
            f"Compression: {h1_compression}",
            f"ATR trend: {atr_trend}",
            "Conclusion",
            "Possible liquidity sweep below range" if location_1h == "lower third" else "Watch range edges",
            "",
            "15m EXECUTION",
            f"Price: {m15.price:,.2f}",
            f"VWAP: {m15.vwap:,.2f}" if m15.vwap else "VWAP: n/a",
            f"Distance: {vwap_dist_pct:,.2f}%",
            f"VWAP trend: {vwap_trend}",
            f"ATR: {m15.atr:,.2f}",
            f"Volume breakout: {'yes' if volume_breakout else 'no'}",
            "Conclusion",
            f"Intraday bias: {vwap_side} VWAP",
            "",
            "MARKET BIAS",
            f"Daily: {daily_structure} ({daily_direction})",
            f"4H: {h4_structure}",
            f"1H: {location_1h}",
            f"Bias: {market_type} ({bias_reason})",
            "",
            "LIQUIDITY MAP",
            f"Above price: {range_high_1h:,.2f}",
            f"Below price: {range_low_1h:,.2f}",
            "",
            "SETUPS",
            "LONG",
            f"Condition: sweep < {setup_level_long:,.2f}",
            "Reclaim range",
            "Probability: conditional",
            f"Invalidation: close < {long_stop:,.2f}",
            f"Target: {long_target:,.2f}",
            "SHORT",
            f"Condition: breakout < {setup_level_short:,.2f}",
            "Continuation",
            "Probability: conditional",
            f"Invalidation: close > {short_stop:,.2f}",
            f"Target: {short_target:,.2f}",
            "",
            "TRADE PLAN (candidate)",
            f"Active setup: {active_setup}",
            f"Preset: {report.setup_preset_name} (entry={report.setup_entry_mode}, stop={report.setup_stop_atr_mult} ATR, target={report.setup_target_timeframe})",
            "Candidate example",
            f"Side: {active_setup}",
            f"Entry: {exec_entry:,.2f}" if exec_entry is not None else "Entry: n/a",
            f"Stop: {exec_stop:,.2f}" if exec_stop is not None else "Stop: n/a",
            f"Stop distance: {stop_distance:,.2f}",
            f"Risk % move: {risk_move_pct:,.2f}%",
            f"Target: {target:,.2f}",
            f"RR gross: {rr_gross:,.2f}",
            f"RR net: {rr_net:,.2f}",
            f"Round-trip costs (fees+slippage): {cost_rate*100:,.3f}%",
            f"Effective stop distance: {effective_stop_rate*100:,.3f}%",
            f"Risk if stop hit (net): ~{report.capital.risk_per_trade_usd:,.2f}",
            f"Cost filter: {'PASS' if cost_pass else 'FAIL'} ({cost_reason})",
            f"VWAP filter: {'PASS' if vwap_pass else 'FAIL'} (side={vwap_side})",
            f"Probability filter: {'PASS' if probability_pass else 'FAIL'} (max={prob_max:,.1f}%, min={report.probability_gate_trigger_min:,.1f}%)",
            f"Liquidity gate: {'PASS' if liquidity_gate_pass else 'FAIL'} ({liquidity_event_dist_pct:,.2f}% on {liquidity_event_side}, max={liquidity_max_distance_pct_effective:,.2f}%)",
            "",
            *tp_lines,
            "",
            "POSITION SIZE",
            f"Active capital: {report.capital.active_capital_usd:,.2f}",
            f"Risk/trade: {report.capital.risk_per_trade_usd:,.2f}",
            f"Position size: {exposure_usd:,.2f} USDC (~{position_size:,.6f} BTC)",
            f"Exposure: {exposure_pct:,.2f}% of active",
            f"Exposure: {(exposure_usd / report.capital.total_equity_usd * 100) if report.capital.total_equity_usd > 0 else 0.0:,.2f}% of total",
            "",
            "CAPITAL",
            f"Kraken USD stable equity: {report.capital.total_equity_usd:,.2f}",
            f"Active (40%): {report.capital.active_capital_usd:,.2f}",
            f"Reserve (60%): {report.capital.reserve_capital_usd:,.2f}",
            f"Risk/trade (0.5% of active): {report.capital.risk_per_trade_usd:,.2f}",
            "",
            f"DERIVATIVES ({report.derivatives.provider.upper()})"
            if report.derivatives
            else "DERIVATIVES",
            *(
                _format_derivatives(report.derivatives, report.heatmap_name)
                if report.derivatives
                else ["- Unavailable (API)"]
            ),
            "",
            "MARKET STATE",
            f"Trend strength: {trend_strength}",
            f"Volatility: {atr_trend}",
            f"Liquidity: {'balanced' if location_1h == 'middle third' else 'skewed'}",
            f"Positioning: {('neutral' if report.derivatives is None else ('short bias' if report.derivatives.funding_current_pct < -0.03 else 'long bias' if report.derivatives.funding_current_pct > 0.03 else 'neutral'))}",
            f"Market type: {market_type.lower()}",
            "",
            "LIQUIDITY DISTANCE",
            f"Below: {liquidity_below_pct:,.2f}%",
            f"Above: {liquidity_above_pct:,.2f}%",
            f"Liquidity asymmetry: {liquidity_asymmetry}",
            "",
            "LEVEL EVENT",
            f"Level: {report.triggers.get('critical_level', key_level):,.2f}",
            f"Sweep detected: {'YES' if report.triggers.get('sweep_detected') else 'NO'}",
            f"Reclaim confirmed: {'YES' if report.triggers.get('reclaim_confirmed') else 'NO'}",
            f"Break confirmed: {'YES' if report.triggers.get('break_confirmed') else 'NO'}",
            f"Inversion confirm ({report.triggers.get('inversion_confirmation_bars', 2)}x15m): LONG={'YES' if report.triggers.get('long_inversion_confirmed') else 'NO'} | SHORT={'YES' if report.triggers.get('short_inversion_confirmed') else 'NO'}",
            f"Active event: {report.triggers.get('active_event', 'none')}",
            "",
            "CRITICAL LEVEL",
            f"{key_level:,.2f}",
            "Break below -> continuation",
            "Reclaim -> range",
            "",
            "LIQUIDATION HEATMAP",
            f"Manual check: {report.heatmap_name}",
            "Expected liquidity sweep zones",
            f"Below: {range_low_1h:,.0f}",
            f"Above: {range_high_1h:,.0f}",
            "",
            "TRIGGERS",
            f"Breakout > {breakout_level:,.2f} -> {'YES' if breakout_now else 'NO'} ({trigger_breakout_dist:,.2f}% away)",
            f"Retest breakout -> {'YES' if retest_now else 'NO'}",
            f"Sweep < {sweep_level:,.2f} -> {'YES' if sweep_reclaim_now else 'NO'} ({trigger_sweep_dist:,.2f}% away)",
            f"Reclaim -> {'YES' if sweep_reclaim_now else 'NO'}",
            "",
            *probability_section,
            "SUMMARY",
            f"Market type: {market_type}",
            f"Location: {location_1h}",
            f"Key level: {key_level:,.2f}",
            f"Break below -> continuation | Reclaim -> long setup",
            f"Best trade: Sweep below {range_low_1h:,.2f} then reclaim" if location_1h == "lower third" else f"Best trade: Short on breakdown below {range_low_1h:,.2f}",
            "Avoid: Trading middle of range" if market_type == "RANGE" else "Avoid: Fading strong trend",
            "",
            "SETUP SCORE",
            f"Trend alignment: {trend_score}/2",
            f"Location: {location_score}/2",
            f"Liquidity: {liquidity_score}/2",
            f"Momentum: {momentum_score}/2",
            f"Volatility: {volatility_score}/2",
            f"Level source bonus: {level_source_bonus:,.1f} ({level_source})",
            f"Raw score: {total_score}/10",
            f"Final score: {final_score}/10",
            f"Setup class: {setup_class}",
            f"Trade gate: {'YES' if trade_gate else 'NO'} ({trade_gate_reason})",
        ]
    )


def build_brief_data(report: BriefReport, dfs: Optional[Dict[str, pd.DataFrame]] = None) -> dict:
    daily = report.metrics["1d"]
    h4 = report.metrics["4h"]
    h1 = report.metrics["1h"]
    m15 = report.metrics["15m"]

    h4_structure = _structure(h4.recent_high or 0, h4.recent_low or 0, h4.atr)
    h4_direction = _trend_direction(h4.price, h4.ema_fast, h4.ema_slow)
    if h4_structure == "trend" and h4_direction == "neutral":
        if h4.ema_slope > 0:
            h4_direction = "up"
        elif h4.ema_slope < 0:
            h4_direction = "down"
        else:
            h4_structure = "range"

    range_high_1h = h1.recent_high or h1.price
    range_low_1h = h1.recent_low or h1.price
    setup_level = report.triggers.get("critical_level", range_low_1h)
    setup_level_long = report.triggers.get("critical_level_long", setup_level)
    setup_level_short = report.triggers.get("critical_level_short", setup_level)
    liquidity_below_pct = _distance_pct_abs(h1.price, range_low_1h)
    liquidity_above_pct = _distance_pct_abs(range_high_1h, h1.price)
    liquidity_asymmetry = "bearish" if liquidity_below_pct < liquidity_above_pct else "bullish" if liquidity_above_pct < liquidity_below_pct else "balanced"
    critical_regime = report.triggers.get("critical_regime", "range_pullback")
    critical_dist_pct = _distance_pct_abs(h1.price, setup_level)
    target_high, target_low = _select_target_levels(
        report.setup_target_timeframe,
        range_high_1h,
        range_low_1h,
        h4.recent_high or h4.price,
        h4.recent_low or h4.price,
    )
    long_entry, long_stop, long_target = _setup_plan(
        setup_level_long,
        h1.atr,
        "LONG",
        report.setup_entry_mode,
        report.setup_retest_buffer_atr,
        report.setup_stop_atr_mult,
        target_high,
        target_low,
    )
    long_stop, long_target = _apply_costs_to_levels(
        long_entry,
        long_stop,
        long_target,
        "LONG",
        report.costs,
    )
    short_entry, short_stop, short_target = _setup_plan(
        setup_level_short,
        h1.atr,
        "SHORT",
        report.setup_entry_mode,
        report.setup_retest_buffer_atr,
        report.setup_stop_atr_mult,
        target_high,
        target_low,
    )
    short_stop, short_target = _apply_costs_to_levels(
        short_entry,
        short_stop,
        short_target,
        "SHORT",
        report.costs,
    )

    daily_direction = _trend_direction(daily.price, daily.ema_fast, daily.ema_slow)
    market_type = "RANGE" if h4_structure == "range" else "TREND"
    bias_reason = f"{'bearish' if daily_direction == 'down' else 'bullish' if daily_direction == 'up' else 'neutral'} pullback zone"

    trade_side = "LONG"
    bias = _bias(daily, h4)
    if bias == "LONG":
        entry, stop, target = long_entry, long_stop, long_target
    elif bias == "SHORT":
        trade_side = "SHORT"
        entry, stop, target = short_entry, short_stop, short_target
    else:
        trade_side = "NONE"
        entry = h1.price
        stop = h1.price - h1.atr * 1.0
        target = h1.price + h1.atr * 2.0

    stop_distance_rate = abs(entry - stop) / entry if entry != 0 else 0.0
    long_stop_distance_rate = abs(long_entry - long_stop) / long_entry if long_entry != 0 else 0.0
    short_stop_distance_rate = abs(short_entry - short_stop) / short_entry if short_entry != 0 else 0.0
    effective_stop_rate = effective_stop_distance_rate(stop_distance_rate, report.costs)
    size_usd = report.capital.risk_per_trade_usd / effective_stop_rate if effective_stop_rate > 0 else 0.0
    position_size = size_usd / entry if entry != 0 else 0.0
    rr_net = net_rr(entry, stop, target, report.costs)
    vwap_side = "above" if m15.vwap and m15.price > m15.vwap else "below"
    cost_pass, cost_reason = passes_cost_filter(
        stop_distance_rate,
        rr_net,
        report.costs,
        report.max_cost_to_stop_ratio,
        report.min_rr_net,
    )

    daily_trend = _conclusion(daily.price, daily.ema_fast, daily.ema_slow, daily.rsi)
    h4_trend = _conclusion(h4.price, h4.ema_fast, h4.ema_slow, h4.rsi)
    vol_state = _vol_state(h1.volume, h1.volume_sma)
    atr_trend = _atr_trend(h1.atr_series) if h1.atr_series is not None else "n/a"
    location_1h = _range_location(h1.price, range_low_1h, range_high_1h)
    trend_score = 2 if daily_trend == h4_trend and daily_trend != "neutral" else 1 if daily_trend == h4_trend else 0
    location_score = 2 if location_1h in {"lower third", "upper third"} else 1 if location_1h == "middle third" else 0
    liquidity_score = 1
    daily_levels = report.levels.get("1d", [])
    daily_support, _ = _nearest_levels(daily_levels, daily.price)
    tol_pct = report.levels_tolerance_pct.get("1h", 0.003) * 100
    if daily_support and abs(_distance_pct(h1.price, daily_support)) <= tol_pct:
        liquidity_score = 2
    momentum_score = 2 if (h1.rsi > 60 or h1.rsi < 40) and vol_state == "strong" else 1 if (h1.rsi > 55 or h1.rsi < 45) else 0
    volatility_score = 2 if atr_trend == "up" else 1 if atr_trend == "flat" else 0
    level_source = str(report.triggers.get("critical_level_source", "1h"))
    level_source_bonus = _level_source_bonus(level_source, report.level_source_weight_enabled, report.level_source_weights)
    total_score = trend_score + location_score + liquidity_score + momentum_score + volatility_score
    final_score = float(total_score)
    active_event = report.triggers.get("active_event", "none")
    liquidity_event_dist_pct, liquidity_event_side = _liquidity_distance_for_event(
        active_event,
        liquidity_below_pct,
        liquidity_above_pct,
        critical_dist_pct,
        critical_regime,
    )
    liquidity_max_distance_pct_effective = _effective_liquidity_max_distance_pct(
        report.liquidity_gate_max_distance_pct,
        h1.atr,
        h1.price,
    )
    if location_score == 0:
        final_score = min(final_score, 6)
    if liquidity_score == 0:
        final_score = min(final_score, 6)
    if trend_score == 0 and active_event != "sweep_reclaim":
        final_score = min(final_score, 5)
    final_score = min(final_score + level_source_bonus, 10.0)

    if final_score <= 5:
        setup_class = "IGNORE"
    elif final_score == 6:
        setup_class = "WATCHLIST"
    elif final_score <= 8:
        setup_class = "VALID"
    else:
        setup_class = "PRIORITY"

    tp_plan = compute_tp_plan(entry, stop, trade_side)
    if tp_plan.levels:
        tp_plan.levels = _fit_tp_levels_to_target(entry, target, trade_side, tp_plan.levels)
        tp_plan.levels = _cap_tp_levels(tp_plan.levels, target, trade_side)
    tp_plan_long = compute_tp_plan(long_entry, long_stop, "LONG")
    if tp_plan_long.levels:
        tp_plan_long.levels = _fit_tp_levels_to_target(long_entry, long_target, "LONG", tp_plan_long.levels)
        tp_plan_long.levels = _cap_tp_levels(tp_plan_long.levels, long_target, "LONG")
    tp_plan_short = compute_tp_plan(short_entry, short_stop, "SHORT")
    if tp_plan_short.levels:
        tp_plan_short.levels = _fit_tp_levels_to_target(short_entry, short_target, "SHORT", tp_plan_short.levels)
        tp_plan_short.levels = _cap_tp_levels(tp_plan_short.levels, short_target, "SHORT")

    probability = None
    if report.probability_engine_enabled:
        probability = compute_directional_probability(
            daily=daily,
            h4=h4,
            h1=h1,
            m15=m15,
            volume_breakout=report.triggers.get("volume_breakout", False),
            derivatives=report.derivatives,
            weights=report.probability_engine_weights,
            event_flags={
                "sweep_detected": report.triggers.get("sweep_detected", False),
                "break_confirmed": report.triggers.get("break_confirmed", False),
            },
            adjustments=report.probability_engine_adjustments,
        )

    probability_pass, prob_max = _probability_pass(
        probability,
        report.probability_gate_trigger_min,
        report.probability_gate_enabled,
    )
    probability_heads_up_pass = (
        (not report.probability_gate_enabled)
        or prob_max == 0.0
        or prob_max >= report.probability_gate_heads_up_min
    )
    vwap_pass = _vwap_pass(active_event, vwap_side, report.vwap_gate_enabled)
    liquidity_gate_pass = (
        (not report.liquidity_gate_enabled)
        or liquidity_event_dist_pct <= liquidity_max_distance_pct_effective
    )
    long_inversion_confirmed = bool(report.triggers.get("long_inversion_confirmed", True))
    short_inversion_confirmed = bool(report.triggers.get("short_inversion_confirmed", True))
    inversion_pass = (
        (active_event != "sweep_reclaim" or long_inversion_confirmed)
        and (active_event != "break" or short_inversion_confirmed)
    )
    cost_gate_pass = (not report.cost_gate_enabled) or cost_pass
    round_trip_cost_pct = round_trip_cost_rate(report.costs) * 100
    cost_ratio_long = (
        round_trip_cost_rate(report.costs) / long_stop_distance_rate
        if long_stop_distance_rate > 0
        else None
    )
    cost_ratio_short = (
        round_trip_cost_rate(report.costs) / short_stop_distance_rate
        if short_stop_distance_rate > 0
        else None
    )
    trade_gate = False
    trade_gate_reason = "setup_score below threshold"
    trade_gate_failures: list[str] = []
    trade_gate_warnings: list[str] = []
    if final_score >= 6:
        if location_score >= 1 and liquidity_score >= 1 and (trend_score >= 1 or prob_max >= 55):
            trade_gate = True
            trade_gate_reason = "passed location/liquidity and trend/probability checks"
        else:
            trade_gate_reason = "failed location/liquidity or trend/probability checks"
    if trade_gate and not cost_gate_pass:
        trade_gate_warnings.append(f"cost_warn:{cost_reason}")
    if trade_gate and not vwap_pass:
        trade_gate = False
        trade_gate_failures.append("vwap_mismatch")
    if trade_gate and not probability_pass:
        trade_gate = False
        trade_gate_failures.append("probability_below_threshold")
    if trade_gate and not liquidity_gate_pass:
        trade_gate = False
        trade_gate_failures.append("liquidity_too_far")
    if trade_gate and not inversion_pass:
        trade_gate = False
        trade_gate_failures.append("inversion_not_confirmed_2bars")
    if trade_gate_failures:
        trade_gate_reason = "; ".join(trade_gate_failures)
    elif trade_gate_warnings:
        trade_gate_reason = "passed with warnings"
    elif trade_gate and level_source_bonus > 0:
        trade_gate_reason = f"{trade_gate_reason}; level_source_bonus={level_source_bonus:,.1f} ({level_source})"
    active_setup = "NONE"
    if active_event == "sweep_reclaim":
        active_setup = "LONG"
    elif active_event == "break":
        active_setup = "SHORT"
    if not trade_gate:
        active_setup = "NONE"

    exec_entry = None
    exec_stop = None
    if active_setup == "LONG":
        exec_entry = long_entry
        exec_stop = long_stop
    elif active_setup == "SHORT":
        exec_entry = short_entry
        exec_stop = short_stop

    exec_stop_distance_rate = None
    if exec_entry is not None and exec_stop is not None and exec_entry != 0:
        exec_stop_distance_rate = abs(exec_entry - exec_stop) / exec_entry

    daily_levels = report.levels.get("1d", [])
    major_support, major_resistance = _major_levels(daily_levels, daily.price)

    mini_chart = None
    if dfs and "15m" in dfs:
        df15 = dfs["15m"].tail(120).reset_index()
        candles = [
            {
                "time": int(row["timestamp"].timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for _, row in df15.iterrows()
        ]
        mini_chart = {
            "candles": candles,
            "levels": {
                "critical": report.triggers.get("critical_level", range_low_1h),
                "critical_long": report.triggers.get("critical_level_long", report.triggers.get("critical_level", range_low_1h)),
                "critical_short": report.triggers.get("critical_level_short", report.triggers.get("critical_level", range_low_1h)),
                "support": major_support,
                "resistance": major_resistance,
                "range_low": range_low_1h,
                "range_high": range_high_1h,
            },
        }

    return {
        "symbol": report.symbol,
        "exchange": report.exchange,
        "sr_levels_source": report.triggers.get("levels_mode", "config"),
        "price": m15.price,
        "setup_profile": f"{report.setup_preset_name} (entry={report.setup_entry_mode}, stop={report.setup_stop_atr_mult} ATR, target={report.setup_target_timeframe})",
        "market_bias": {
            "daily": daily_direction,
            "h4_structure": h4_structure,
            "h4_direction": h4_direction,
            "bias": market_type,
            "reason": bias_reason,
        },
        "market_state": {
            "volatility": atr_trend,
        },
        "setup_score": {
            "total": total_score,
            "final": final_score,
            "class": setup_class,
            "trade_gate": trade_gate,
            "reason": trade_gate_reason,
            "components": {
                "trend": trend_score,
                "location": location_score,
                "liquidity": liquidity_score,
                "momentum": momentum_score,
                "volatility": volatility_score,
                "level_source_bonus": level_source_bonus,
            },
        },
        "critical_level": report.triggers.get("critical_level", range_low_1h),
        "critical_level_source": report.triggers.get("critical_level_source", "1h"),
        "critical_level_long": report.triggers.get("critical_level_long", report.triggers.get("critical_level", range_low_1h)),
        "critical_level_long_source": report.triggers.get("critical_level_long_source", report.triggers.get("critical_level_source", "1h")),
        "critical_level_short": report.triggers.get("critical_level_short", report.triggers.get("critical_level", range_low_1h)),
        "critical_level_short_source": report.triggers.get("critical_level_short_source", report.triggers.get("critical_level_source", "1h")),
        "critical_regime": report.triggers.get("critical_regime", "range_pullback"),
        "critical_level_quality": report.triggers.get("critical_level_quality"),
        "critical_level_long_quality": report.triggers.get("critical_level_long_quality"),
        "critical_level_short_quality": report.triggers.get("critical_level_short_quality"),
        "critical_level_distance_pct": _distance_pct(
            report.triggers.get("critical_level", range_low_1h), m15.price
        ),
        "liquidity_distance": {
            "below_pct": liquidity_below_pct,
            "above_pct": liquidity_above_pct,
            "asymmetry": liquidity_asymmetry,
            "min_pct": min(liquidity_below_pct, liquidity_above_pct),
            "event_side": liquidity_event_side,
            "event_side_pct": liquidity_event_dist_pct,
        },
        "position_size": {
            "usdc": size_usd,
            "btc": position_size,
            "risk_per_trade": report.capital.risk_per_trade_usd,
            "exposure_active_pct": (size_usd / report.capital.active_capital_usd * 100) if report.capital.active_capital_usd > 0 else 0.0,
            "exposure_total_pct": (size_usd / report.capital.total_equity_usd * 100) if report.capital.total_equity_usd > 0 else 0.0,
        },
        "capital": {
            "total": report.capital.total_equity_usd,
            "active": report.capital.active_capital_usd,
            "reserve": report.capital.reserve_capital_usd,
        },
        "derivatives": None if report.derivatives is None else {
            "provider": report.derivatives.provider,
            "mark_price": report.derivatives.mark_price,
            "oi_change_1h_pct": report.derivatives.oi_change_1h_pct,
            "oi_change_4h_pct": report.derivatives.oi_change_4h_pct,
            "oi_change_24h_pct": report.derivatives.oi_change_24h_pct,
            "funding_current_pct": report.derivatives.funding_current_pct,
            "funding_1d_pct": report.derivatives.funding_1d_pct,
        },
        "trade": {
            "side": active_setup,
            "entry": exec_entry,
            "stop": exec_stop,
            "target": target,
            "rr_net": rr_net,
            "stop_distance_pct": (exec_stop_distance_rate * 100) if exec_stop_distance_rate is not None else None,
            "active_setup": active_setup,
            "vwap_side": vwap_side,
            "filters": {
                "cost_pass": cost_gate_pass,
                "cost_reason": cost_reason,
                "cost_ratio_threshold": report.max_cost_to_stop_ratio,
                "cost_ratio_long": cost_ratio_long,
                "cost_ratio_short": cost_ratio_short,
                "cost_round_trip_pct": round_trip_cost_pct,
                "stop_distance_long_pct": (long_stop_distance_rate * 100) if long_stop_distance_rate > 0 else None,
                "stop_distance_short_pct": (short_stop_distance_rate * 100) if short_stop_distance_rate > 0 else None,
                "vwap_pass": vwap_pass,
                "probability_pass": probability_pass,
                "probability_max": prob_max,
                "probability_min": report.probability_gate_trigger_min,
                "probability_heads_up_pass": probability_heads_up_pass,
                "probability_heads_up_min": report.probability_gate_heads_up_min,
                "level_source_weight": level_source_bonus,
                "level_source": level_source,
                "liquidity_gate_pass": liquidity_gate_pass,
                "liquidity_event_side": liquidity_event_side,
                "liquidity_event_pct": liquidity_event_dist_pct,
                "liquidity_max_distance_pct_effective": liquidity_max_distance_pct_effective,
                "inversion_pass": inversion_pass,
                "long_inversion_confirmed": long_inversion_confirmed,
                "short_inversion_confirmed": short_inversion_confirmed,
                "trade_gate_failures": trade_gate_failures,
                "trade_gate_warnings": trade_gate_warnings,
            },
        },
        "level_event": {
            "level": report.triggers.get("critical_level", range_low_1h),
            "level_source": report.triggers.get("critical_level_source", "1h"),
            "level_long": report.triggers.get("critical_level_long", report.triggers.get("critical_level", range_low_1h)),
            "level_long_source": report.triggers.get("critical_level_long_source", report.triggers.get("critical_level_source", "1h")),
            "level_short": report.triggers.get("critical_level_short", report.triggers.get("critical_level", range_low_1h)),
            "level_short_source": report.triggers.get("critical_level_short_source", report.triggers.get("critical_level_source", "1h")),
            "critical_regime": report.triggers.get("critical_regime", "range_pullback"),
            "level_quality": report.triggers.get("critical_level_quality"),
            "level_long_quality": report.triggers.get("critical_level_long_quality"),
            "level_short_quality": report.triggers.get("critical_level_short_quality"),
            "sweep_detected": report.triggers.get("sweep_detected", False),
            "reclaim_confirmed": report.triggers.get("reclaim_confirmed", False),
            "break_confirmed": report.triggers.get("break_confirmed", False),
            "inversion_confirmation_bars": report.triggers.get("inversion_confirmation_bars", 2),
            "long_inversion_confirmed": long_inversion_confirmed,
            "short_inversion_confirmed": short_inversion_confirmed,
            "active_event": active_event,
        },
        "mini_chart": mini_chart,
        "setups": {
            "long": {
                "condition": f"sweep < {setup_level_long:,.2f}",
                "target": long_target,
                "entry": long_entry,
                "stop": long_stop,
            },
            "short": {
                "condition": f"breakout < {setup_level_short:,.2f}",
                "target": short_target,
                "entry": short_entry,
                "stop": short_stop,
            },
        },
        "tp_plan": [
            {"price": lvl.price, "size_pct": lvl.size_pct, "r_multiple": lvl.r_multiple}
            for lvl in tp_plan.levels
        ],
        "tp_plan_long": [
            {"price": lvl.price, "size_pct": lvl.size_pct, "r_multiple": lvl.r_multiple}
            for lvl in tp_plan_long.levels
        ],
        "tp_plan_short": [
            {"price": lvl.price, "size_pct": lvl.size_pct, "r_multiple": lvl.r_multiple}
            for lvl in tp_plan_short.levels
        ],
        "directional_probability": probability,
    }

def _format_derivatives(snapshot: DerivativesSnapshot, heatmap_name: str) -> list[str]:
    oi_change_str = "n/a"
    if snapshot.oi_change_1h_pct is not None:
        oi_change_str = (
            f"1h {snapshot.oi_change_1h_pct:,.2f}% | "
            f"4h {snapshot.oi_change_4h_pct:,.2f}% | "
            f"24h {snapshot.oi_change_24h_pct:,.2f}%"
        )
    interpretation = "Market positioning: neutral"
    if snapshot.funding_current_pct > 0.03:
        interpretation = "Market positioning: slightly long"
    if snapshot.funding_current_pct < -0.03:
        interpretation = "Market positioning: slightly short"
    if snapshot.oi_change_24h_pct is not None and snapshot.oi_change_24h_pct < 0:
        interpretation = f"{interpretation} | Market deleveraging"
    return [
        f"- Mark price: {snapshot.mark_price:,.2f}",
        f"- Open interest: {snapshot.oi_contracts:,.2f} contracts (~{snapshot.oi_usd:,.2f} USD)",
        f"- OI change: {oi_change_str}",
        f"- Funding current: {snapshot.funding_current_pct:,.4f}%",
        f"- Funding 1D (approx): {snapshot.funding_1d_pct:,.4f}%",
        f"- Interpretation: {interpretation}",
        f"- Notes: {synthese(snapshot)}",
        f"- Heatmap: manual check ({heatmap_name})",
    ]
