"""
Liquidity Sweep Strategy — Detects LONG and SHORT setups.

LONG (Sellside Liquidity Sweep):
    Point 1 = a confirmed swing LOW
    Point 2 = a candle whose wick goes BELOW Point 1's low, but CLOSES ABOVE it

SHORT (Buyside Liquidity Sweep):
    Point 1 = a confirmed swing HIGH
    Point 2 = a candle whose wick goes ABOVE Point 1's high, but CLOSES BELOW it

Distance rule: min_bars between Point 1 and Point 2 (configurable per timeframe).
EMA 200 is computed and reported as a flag but does NOT filter signals.

Each swing point can only be swept ONCE — after that it is consumed and
will not generate further alerts.
"""

from __future__ import annotations

import pandas as pd

EMA_LENGTH = 200
SWING_STRENGTH = 5            # A swing is confirmed with N bars on each side (11-bar window)
LOOKBACK_MAX = 500            # Max bars to look back for a swing point
LOOKBACK_MIN = 5              # Min bars to look back (avoid immediate swings)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA and confirmed swing high/low flags to the dataframe."""
    result = df.copy()
    if result.empty:
        return result

    result["ema"] = result["close"].ewm(span=EMA_LENGTH, adjust=False).mean()

    window = (SWING_STRENGTH * 2) + 1
    result["is_swing_low"] = result["low"].eq(
        result["low"].rolling(window=window, center=True).min()
    )
    result["is_swing_high"] = result["high"].eq(
        result["high"].rolling(window=window, center=True).max()
    )
    result[["is_swing_low", "is_swing_high"]] = result[
        ["is_swing_low", "is_swing_high"]
    ].fillna(False)

    return result


def detect_sweeps(
    df: pd.DataFrame,
    current_index: int,
    min_bars: int = 10,
    consumed: set[tuple[str, int]] | None = None,
) -> list[dict]:
    """
    At the candle at *current_index*, check whether it sweeps any past
    swing high (SHORT) or swing low (LONG) that is at least *min_bars* away
    and hasn't been consumed yet.

    *consumed* is a set of (direction, swing_position) tuples that have
    already been triggered.  When a new trigger fires, the caller should
    add it to the set so the same swing point is not re-triggered.

    Returns a list of trigger dicts (0, 1 or 2 — at most one per direction).
    """
    if current_index < max(EMA_LENGTH, min_bars + LOOKBACK_MIN):
        return []

    if consumed is None:
        consumed = set()

    candle = df.iloc[current_index]
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    swing_highs = df["is_swing_high"].to_numpy()
    swing_lows = df["is_swing_low"].to_numpy()

    start = max(0, current_index - LOOKBACK_MAX)
    end = current_index - min_bars  # must be at least min_bars before

    if end < start:
        return []

    triggers: list[dict] = []

    # --- LONG: sellside liquidity sweep ---
    best_long_pos = None
    for pos in range(end, start - 1, -1):
        if not swing_lows[pos]:
            continue
        if ("LONG", pos) in consumed:
            continue
        swing_low_price = lows[pos]
        if candle["low"] < swing_low_price and candle["close"] > swing_low_price:
            best_long_pos = pos
            break

    if best_long_pos is not None:
        swing_low_price = float(lows[best_long_pos])
        ema_val = candle["ema"] if not pd.isna(candle["ema"]) else None
        ema_aligned = bool(ema_val is not None and candle["close"] > ema_val)
        consumed.add(("LONG", best_long_pos))
        triggers.append({
            "trigger": True,
            "direction": "LONG",
            "sweep_time": df.index[current_index] if isinstance(df.index, pd.DatetimeIndex) else current_index,
            "liquidity_time": df.index[best_long_pos] if isinstance(df.index, pd.DatetimeIndex) else best_long_pos,
            "liquidity_level": swing_low_price,
            "sweep_low": float(candle["low"]),
            "close_price": float(candle["close"]),
            "ema_aligned": ema_aligned,
            "bars_between": current_index - best_long_pos,
        })

    # --- SHORT: buyside liquidity sweep ---
    best_short_pos = None
    for pos in range(end, start - 1, -1):
        if not swing_highs[pos]:
            continue
        if ("SHORT", pos) in consumed:
            continue
        swing_high_price = highs[pos]
        if candle["high"] > swing_high_price and candle["close"] < swing_high_price:
            best_short_pos = pos
            break

    if best_short_pos is not None:
        swing_high_price = float(highs[best_short_pos])
        ema_val = candle["ema"] if not pd.isna(candle["ema"]) else None
        ema_aligned = bool(ema_val is not None and candle["close"] < ema_val)
        consumed.add(("SHORT", best_short_pos))
        triggers.append({
            "trigger": True,
            "direction": "SHORT",
            "sweep_time": df.index[current_index] if isinstance(df.index, pd.DatetimeIndex) else current_index,
            "liquidity_time": df.index[best_short_pos] if isinstance(df.index, pd.DatetimeIndex) else best_short_pos,
            "liquidity_level": swing_high_price,
            "sweep_high": float(candle["high"]),
            "close_price": float(candle["close"]),
            "ema_aligned": ema_aligned,
            "bars_between": current_index - best_short_pos,
        })

    return triggers
