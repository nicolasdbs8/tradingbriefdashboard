from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from brief_engine import generate_trading_brief
from src.config import load_config
from src.notifications.telegram import send_telegram_message
try:
    from dotenv import load_dotenv
except ImportError:  # optional dependency for local .env usage
    load_dotenv = None


@dataclass
class AlertDecision:
    alert_type: str
    favorable: bool
    reason: str
    signature: str
    payload: Dict[str, Any]


def _fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def _build_signature(data: Dict[str, Any]) -> str:
    active_setup = data.get("trade", {}).get("active_setup", "NONE")
    active_event = data.get("level_event", {}).get("active_event", "none")
    score = data.get("setup_score", {}).get("final", 0)
    return f"{active_setup}:{active_event}:{score}"


def _decision(alert_type: str, favorable: bool, reason: str, data: Dict[str, Any]) -> AlertDecision:
    return AlertDecision(alert_type=alert_type, favorable=favorable, reason=reason, signature=_build_signature(data), payload=data)


def _evaluate_trigger(data: Dict[str, Any], cfg) -> AlertDecision:
    setup_score = data.get("setup_score", {})
    trade = data.get("trade", {})
    level_event = data.get("level_event", {})

    if not cfg.alerts_enabled:
        return _decision("trigger", False, "alerts disabled", data)

    final_score = float(setup_score.get("final", 0))
    final_score_rounded = round(final_score, 1)
    if final_score_rounded < cfg.alerts_min_setup_score:
        return _decision("trigger", False, "setup_score below threshold", data)

    if cfg.alerts_require_trade_gate and not setup_score.get("trade_gate", False):
        return _decision("trigger", False, "trade_gate false", data)

    if cfg.alerts_require_active_setup and trade.get("active_setup") in {None, "NONE"}:
        return _decision("trigger", False, "no active_setup", data)

    active_event = level_event.get("active_event", "none")
    if cfg.alerts_require_active_event and active_event not in cfg.alerts_allowed_active_events:
        return _decision("trigger", False, "active_event not allowed", data)

    return _decision("trigger", True, "favorable", data)


def _evaluate_heads_up(data: Dict[str, Any], cfg) -> AlertDecision:
    setup_score = data.get("setup_score", {})
    trade = data.get("trade", {})
    level_event = data.get("level_event", {})
    liquidity_dist = abs(float(data.get("liquidity_distance", {}).get("min_pct", 9999)))

    if not cfg.alerts_heads_up_enabled:
        return _decision("heads_up", False, "heads-up disabled", data)

    final_score = round(float(setup_score.get("final", 0)), 1)
    if final_score < cfg.alerts_heads_up_min_setup_score:
        return _decision("heads_up", False, "heads-up score below threshold", data)

    if cfg.alerts_heads_up_require_trade_gate and not setup_score.get("trade_gate", False):
        return _decision("heads_up", False, "heads-up trade_gate false", data)

    if cfg.alerts_heads_up_require_no_active_setup and trade.get("active_setup") not in {None, "NONE"}:
        return _decision("heads_up", False, "heads-up already active setup", data)

    if cfg.alerts_heads_up_require_signal_hint:
        has_level_hint = liquidity_dist <= cfg.alerts_heads_up_max_distance_pct
        has_event_hint = bool(level_event.get("sweep_detected") or level_event.get("reclaim_confirmed"))
        if not (has_level_hint or has_event_hint):
            return _decision("heads_up", False, "heads-up no confirmation hint", data)

    return _decision("heads_up", True, "heads-up favorable", data)


def _evaluate_alert(data: Dict[str, Any], cfg) -> AlertDecision:
    trigger = _evaluate_trigger(data, cfg)
    if trigger.favorable:
        return trigger
    heads_up = _evaluate_heads_up(data, cfg)
    if heads_up.favorable:
        return heads_up
    return trigger


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _cooldown_passed(state: Dict[str, Any], cooldown_minutes: int, alert_type: str) -> bool:
    type_key = f"last_alert_ts_{alert_type}"
    last_ts = float(state.get(type_key, 0))
    # Backward compatibility for legacy single-key trigger state.
    if last_ts <= 0 and alert_type == "trigger":
        last_ts = float(state.get("last_alert_ts", 0))
    if last_ts <= 0:
        return True
    return (time.time() - last_ts) >= cooldown_minutes * 60


def _build_message(data: Dict[str, Any], alert_type: str) -> str:
    setup_score = data.get("setup_score", {})
    trade = data.get("trade", {})
    level_event = data.get("level_event", {})
    market_bias = data.get("market_bias", {})
    symbol = data.get("symbol", "UNKNOWN")
    exchange = data.get("exchange", "n/a")
    price = _fmt_price(data.get("price"))
    active_setup = trade.get("active_setup", "NONE")
    active_event = level_event.get("active_event", "none")
    entry = _fmt_price(trade.get("entry"))
    stop = _fmt_price(trade.get("stop"))
    target = _fmt_price(trade.get("target"))
    rr_net = trade.get("rr_net", 0.0)
    score = setup_score.get("final", 0)
    setup_class = setup_score.get("class", "n/a")
    gate_reason = setup_score.get("reason", "n/a")
    preset = data.get("setup_profile", "n/a")
    bias = market_bias.get("bias", "n/a")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if alert_type == "trigger":
        title = "TRADING SETUP TRIGGER"
        alert_type_label = "TRIGGER"
    elif alert_type == "heads_up":
        title = "TRADING SETUP HEADS-UP"
        alert_type_label = "HEADS-UP"
    else:
        title = "TRADING SETUP TEST"
        alert_type_label = "FORCED-TEST"

    return "\n".join(
        [
            title,
            f"Alert type: {alert_type_label}",
            f"Time: {now}",
            f"Symbol: {symbol} ({exchange})",
            f"Price: {price}",
            f"Setup: {active_setup} | Event: {active_event}",
            f"Score: {score}/10 ({setup_class})",
            f"Gate: {gate_reason}",
            f"Preset: {preset}",
            f"Bias: {bias}",
            f"Entry: {entry}",
            f"Stop: {stop}",
            f"Target: {target}",
            f"RR net: {rr_net:,.2f}",
        ]
    )


def run_check(config_path: str, state_path: str, dry_run: bool, force: bool) -> int:
    if load_dotenv:
        load_dotenv()
    cfg = load_config(config_path)
    brief = generate_trading_brief(config_path=config_path)
    data = brief.get("data", {})
    decision = _evaluate_alert(data, cfg)

    if not decision.favorable and not force:
        print(f"[alert] not favorable: {decision.reason}")
        return 0

    state_file = Path(state_path)
    state = _load_state(state_file)
    signature = decision.signature

    cooldown_minutes = cfg.alerts_cooldown_minutes if decision.alert_type == "trigger" else cfg.alerts_heads_up_cooldown_minutes
    if not _cooldown_passed(state, cooldown_minutes, decision.alert_type) and not force:
        print("[alert] cooldown active, skipping")
        return 0

    signature_key = f"last_signature_{decision.alert_type}"
    previous_signature = state.get(signature_key)
    if previous_signature is None and decision.alert_type == "trigger":
        previous_signature = state.get("last_signature")
    if previous_signature == signature and not force:
        print("[alert] same signature already alerted, skipping")
        return 0

    message_alert_type = decision.alert_type
    if force and not decision.favorable:
        message_alert_type = "force_test"
    message = _build_message(data, message_alert_type)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if dry_run:
        print("[alert] dry-run enabled, not sending telegram")
        print(message)
        return 0
    else:
        if not token or not chat_id:
            print("[alert] telegram credentials missing, not sending")
            return 0
        send_telegram_message(token, chat_id, message)
        print(f"[alert] telegram message sent ({decision.alert_type})")

    state.update(
        {
            f"last_alert_ts_{decision.alert_type}": time.time(),
            f"last_signature_{decision.alert_type}": signature,
        }
    )
    _save_state(state_file, state)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Check favorable setup and notify via Telegram.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--state", default=".alerts_state.json", help="State file path")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Telegram message")
    parser.add_argument("--force", action="store_true", help="Force alert even if not favorable")
    args = parser.parse_args()
    return run_check(args.config, args.state, args.dry_run, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
