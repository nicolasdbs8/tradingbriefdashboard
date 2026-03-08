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
from .execution.take_profit import compute_tp_plan
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
        range_low_1h,
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
        range_low_1h,
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
    total_score = trend_score + location_score + liquidity_score + momentum_score + volatility_score
    final_score = total_score
    active_event = report.triggers.get("active_event", "none")
    if location_score == 0:
        final_score = min(final_score, 6)
    if liquidity_score == 0:
        final_score = min(final_score, 6)
    if trend_score == 0 and active_event != "sweep_reclaim":
        final_score = min(final_score, 5)

    if final_score <= 5:
        setup_class = "IGNORE"
    elif final_score == 6:
        setup_class = "WATCHLIST"
    elif final_score <= 8:
        setup_class = "VALID"
    else:
        setup_class = "PRIORITY"

    prob_max = 0.0
    if probability:
        prob_max = max(probability["long_probability_pct"], probability["short_probability_pct"])
    trade_gate = False
    trade_gate_reason = "setup_score below threshold"
    if final_score >= 6:
        if location_score >= 1 and liquidity_score >= 1 and (trend_score >= 1 or prob_max >= 55):
            trade_gate = True
            trade_gate_reason = "passed location/liquidity and trend/probability checks"
        else:
            trade_gate_reason = "failed location/liquidity or trend/probability checks"

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

    key_level = range_low_1h

    liquidity_below_pct = _distance_pct_abs(h1.price, range_low_1h)
    liquidity_above_pct = _distance_pct_abs(range_high_1h, h1.price)
    liquidity_asymmetry = "bearish" if liquidity_below_pct < liquidity_above_pct else "bullish" if liquidity_above_pct < liquidity_below_pct else "balanced"

    bias_reason = f"{'bearish' if daily_direction == 'down' else 'bullish' if daily_direction == 'up' else 'neutral'} pullback zone"

    cost_pass, cost_reason = passes_cost_filter(
        stop_distance_rate,
        rr_net,
        report.costs,
        report.max_cost_to_stop_ratio,
        report.min_rr_net,
    )

    tp_plan = compute_tp_plan(entry, stop, trade_side)
    tp_plan_long = compute_tp_plan(long_entry, long_stop, "LONG")
    tp_plan_short = compute_tp_plan(short_entry, short_stop, "SHORT")
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
            f"Condition: sweep < {range_low_1h:,.2f}",
            "Reclaim range",
            "Probability: conditional",
            f"Invalidation: close < {long_stop:,.2f}",
            f"Target: {long_target:,.2f}",
            "SHORT",
            f"Condition: breakout < {range_low_1h:,.2f}",
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
    liquidity_below_pct = _distance_pct_abs(h1.price, range_low_1h)
    liquidity_above_pct = _distance_pct_abs(range_high_1h, h1.price)
    liquidity_asymmetry = "bearish" if liquidity_below_pct < liquidity_above_pct else "bullish" if liquidity_above_pct < liquidity_below_pct else "balanced"
    target_high, target_low = _select_target_levels(
        report.setup_target_timeframe,
        range_high_1h,
        range_low_1h,
        h4.recent_high or h4.price,
        h4.recent_low or h4.price,
    )
    long_entry, long_stop, long_target = _setup_plan(
        range_low_1h,
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
        range_low_1h,
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
    effective_stop_rate = effective_stop_distance_rate(stop_distance_rate, report.costs)
    size_usd = report.capital.risk_per_trade_usd / effective_stop_rate if effective_stop_rate > 0 else 0.0
    position_size = size_usd / entry if entry != 0 else 0.0
    rr_net = net_rr(entry, stop, target, report.costs)
    vwap_side = "above" if m15.vwap and m15.price > m15.vwap else "below"

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
    total_score = trend_score + location_score + liquidity_score + momentum_score + volatility_score
    final_score = total_score
    active_event = report.triggers.get("active_event", "none")
    if location_score == 0:
        final_score = min(final_score, 6)
    if liquidity_score == 0:
        final_score = min(final_score, 6)
    if trend_score == 0 and active_event != "sweep_reclaim":
        final_score = min(final_score, 5)

    if final_score <= 5:
        setup_class = "IGNORE"
    elif final_score == 6:
        setup_class = "WATCHLIST"
    elif final_score <= 8:
        setup_class = "VALID"
    else:
        setup_class = "PRIORITY"

    tp_plan = compute_tp_plan(entry, stop, trade_side)
    tp_plan_long = compute_tp_plan(long_entry, long_stop, "LONG")
    tp_plan_short = compute_tp_plan(short_entry, short_stop, "SHORT")

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

    prob_max = 0.0
    if probability:
        prob_max = max(probability["long_probability_pct"], probability["short_probability_pct"])
    trade_gate = False
    trade_gate_reason = "setup_score below threshold"
    if final_score >= 6:
        if location_score >= 1 and liquidity_score >= 1 and (trend_score >= 1 or prob_max >= 55):
            trade_gate = True
            trade_gate_reason = "passed location/liquidity and trend/probability checks"
        else:
            trade_gate_reason = "failed location/liquidity or trend/probability checks"

    active_event = report.triggers.get("active_event", "none")
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
                "support": major_support,
                "resistance": major_resistance,
                "range_low": range_low_1h,
                "range_high": range_high_1h,
            },
        }

    return {
        "symbol": report.symbol,
        "exchange": report.exchange,
        "price": m15.price,
        "setup_profile": f"{report.setup_preset_name} (entry={report.setup_entry_mode}, stop={report.setup_stop_atr_mult} ATR, target={report.setup_target_timeframe})",
        "market_bias": {
            "daily": daily_direction,
            "h4_structure": h4_structure,
            "h4_direction": h4_direction,
            "bias": market_type,
            "reason": bias_reason,
        },
        "setup_score": {
            "total": total_score,
            "final": final_score,
            "class": setup_class,
            "trade_gate": trade_gate,
            "reason": trade_gate_reason,
        },
        "critical_level": range_low_1h,
        "critical_level_distance_pct": _distance_pct(range_low_1h, m15.price),
        "liquidity_distance": {
            "below_pct": liquidity_below_pct,
            "above_pct": liquidity_above_pct,
            "asymmetry": liquidity_asymmetry,
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
            "oi_change_24h_pct": report.derivatives.oi_change_24h_pct,
            "funding_current_pct": report.derivatives.funding_current_pct,
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
        },
        "level_event": {
            "level": report.triggers.get("critical_level", range_low_1h),
            "sweep_detected": report.triggers.get("sweep_detected", False),
            "reclaim_confirmed": report.triggers.get("reclaim_confirmed", False),
            "break_confirmed": report.triggers.get("break_confirmed", False),
            "active_event": active_event,
        },
        "mini_chart": mini_chart,
        "setups": {
            "long": {
                "condition": f"sweep < {range_low_1h:,.2f}",
                "target": long_target,
                "entry": long_entry,
                "stop": long_stop,
            },
            "short": {
                "condition": f"breakout < {range_low_1h:,.2f}",
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
