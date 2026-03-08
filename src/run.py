from __future__ import annotations

import argparse
import logging

from brief_engine import generate_trading_brief


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trading Brief Engine (MVP)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--symbol", help="Override symbol (e.g., BTC/USDC)")
    parser.add_argument("--exchange", help="Override exchange (e.g., binance)")
    return parser.parse_args()


def _compute_indicators(
    df: pd.DataFrame,
    ema_fast_period: int,
    ema_slow_period: int,
    rsi_period: int,
    atr_period: int,
    volume_sma_period: int,
    ema_slope_bars: int,
    include_vwap: bool,
) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast_period)
    df["ema_slow"] = ema(df["close"], ema_slow_period)
    df["ema_slope"] = ema_slope(df["ema_fast"], ema_slope_bars)
    df["rsi"] = rsi(df["close"], rsi_period)
    df["atr"] = atr(df, atr_period)
    if include_vwap:
        df["vwap"] = vwap_intraday(df)
    df["volume_sma"] = df["volume"].rolling(volume_sma_period).mean()
    return df


def main() -> int:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    data = generate_trading_brief(
        config_path=args.config,
        symbol=args.symbol,
        exchange=args.exchange,
    )
    print(data["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
