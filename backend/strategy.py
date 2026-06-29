from __future__ import annotations

import math
import pandas as pd


EMA_LENGTH = 150
LIQUIDITY_LOOKBACK_MIN = 5
LIQUIDITY_LOOKBACK_MAX = 500
SWING_STRENGTH = 2
MAX_BARS_AFTER_SWEEP = 72
MIN_REJECTION_WICK_RATIO = 0.40
MAX_CLOSE_POSITION = 0.55
MAX_SWEEP_DEVIATION = 0.015  # Max 1.5% sweep deviation for equal highs


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates the 200 EMA and identifies confirmed swing highs/lows.
    Requires a pandas DataFrame with 'open', 'high', 'low', 'close', 'volume' columns.
    """
    result = df.copy()
    if result.empty:
        return result

    result["ema"] = result["close"].ewm(span=EMA_LENGTH, adjust=False).mean()

    swing_window = (SWING_STRENGTH * 2) + 1
    result["is_swing_low"] = result["low"].eq(
        result["low"].rolling(window=swing_window, center=True).min()
    )
    result["is_swing_high"] = result["high"].eq(
        result["high"].rolling(window=swing_window, center=True).max()
    )
    result[["is_swing_low", "is_swing_high"]] = result[
        ["is_swing_low", "is_swing_high"]
    ].fillna(False)

    lows = result["low"].to_numpy()
    highs = result["high"].to_numpy()
    closes = result["close"].to_numpy()
    ema = result["ema"].to_numpy()
    swing_lows = result["is_swing_low"].to_numpy()
    swing_highs = result["is_swing_high"].to_numpy()

    liquidity_highs: list[float] = []
    liquidity_positions: list[float] = []
    swept_liquidity_highs: list[float] = []
    swept_liquidity_positions: list[float] = []
    breakdown_levels: list[float] = []
    breakdown_positions: list[float] = []

    for pos in range(len(result)):
        start = max(0, pos - LIQUIDITY_LOOKBACK_MAX)
        end = max(0, pos - LIQUIDITY_LOOKBACK_MIN + 1)
        swing_high_positions = [
            candidate_pos for candidate_pos in range(start, end) if swing_highs[candidate_pos]
        ]

        if not swing_high_positions:
            liquidity_highs.append(math.nan)
            liquidity_positions.append(math.nan)
            swept_liquidity_highs.append(math.nan)
            swept_liquidity_positions.append(math.nan)
            breakdown_levels.append(math.nan)
            breakdown_positions.append(math.nan)
            continue

        latest_liquidity_pos = swing_high_positions[-1]
        liquidity_highs.append(float(highs[latest_liquidity_pos]))
        liquidity_positions.append(float(latest_liquidity_pos))

        # Sweep: high goes above liquidity high, but close is below it. Must be at least 20 bars apart.
        swept_positions = []
        for candidate_pos in swing_high_positions:
            if pos - candidate_pos < 20:
                continue
            if highs[pos] > highs[candidate_pos] > closes[pos]:
                # ZigZag Rule: No candle between Point 1 and Point 2 can close above Point 1's high
                intermediate_closes = closes[candidate_pos:pos]
                if len(intermediate_closes) == 0 or intermediate_closes.max() <= highs[candidate_pos]:
                    swept_positions.append(candidate_pos)
        
        if not swept_positions:
            swept_liquidity_highs.append(math.nan)
            swept_liquidity_positions.append(math.nan)
            breakdown_levels.append(math.nan)
            breakdown_positions.append(math.nan)
            continue

        swept_pos = swept_positions[-1]
        swept_liquidity_highs.append(float(highs[swept_pos]))
        swept_liquidity_positions.append(float(swept_pos))

        breakdown_ceiling = min(highs[swept_pos], closes[pos])
        prior_low_positions = [
            candidate_pos
            for candidate_pos in range(swept_pos, pos)
            if swing_lows[candidate_pos] and lows[candidate_pos] < breakdown_ceiling
        ]
        if prior_low_positions:
            low_pos = prior_low_positions[-1]
        else:
            fallback_start = max(0, pos - LIQUIDITY_LOOKBACK_MIN)
            fallback_positions = [
                candidate_pos
                for candidate_pos in range(fallback_start, pos)
                if lows[candidate_pos] < breakdown_ceiling
            ]
            low_pos = min(fallback_positions, key=lambda candidate_pos: lows[candidate_pos]) if fallback_positions else None

        if low_pos is None:
            breakdown_levels.append(math.nan)
            breakdown_positions.append(math.nan)
        else:
            breakdown_levels.append(float(lows[low_pos]))
            breakdown_positions.append(float(low_pos))

    result["liquidity_high"] = liquidity_highs
    result["liquidity_high_pos"] = liquidity_positions
    result["swept_liquidity_high"] = swept_liquidity_highs
    result["swept_liquidity_high_pos"] = swept_liquidity_positions
    result["swing_low_to_break"] = breakdown_levels
    result["swing_low_to_break_pos"] = breakdown_positions

    return result


def detect_setup(df: pd.DataFrame, current_index: int) -> dict:
    """
    Evaluates the Short setup on the candle at current_index.
    """
    if current_index < EMA_LENGTH or pd.isna(df["ema"].iloc[current_index]):
        return {"trigger": False, "reason": "Not enough data"}

    current_candle = df.iloc[current_index]

    earliest_sweep = max(EMA_LENGTH, current_index - MAX_BARS_AFTER_SWEEP)

    for sweep_idx in range(current_index - 1, earliest_sweep - 1, -1):
        sweep_candle = df.iloc[sweep_idx]
        liquidity_high = sweep_candle["swept_liquidity_high"]
        liquidity_pos_value = sweep_candle["swept_liquidity_high_pos"]
        swing_low_to_break = sweep_candle["swing_low_to_break"]
        swing_low_pos_value = sweep_candle["swing_low_to_break_pos"]
        
        if (
            pd.isna(liquidity_high)
            or pd.isna(liquidity_pos_value)
            or pd.isna(swing_low_to_break)
            or pd.isna(swing_low_pos_value)
        ):
            continue
            
        liquidity_pos = int(liquidity_pos_value)
        swing_low_pos = int(swing_low_pos_value)

        ema_aligned = bool(sweep_candle["close"] < sweep_candle["ema"] and current_candle["close"] < current_candle["ema"])

        if sweep_candle["high"] > liquidity_high * (1 + MAX_SWEEP_DEVIATION):
            continue

        candle_range = sweep_candle["high"] - sweep_candle["low"]
        if candle_range <= 0:
            continue

        # Top wick rejection logic
        upper_wick = sweep_candle["high"] - max(sweep_candle["open"], sweep_candle["close"])
        close_position = (sweep_candle["close"] - sweep_candle["low"]) / candle_range
        has_heavy_rejection = (
            upper_wick / candle_range >= MIN_REJECTION_WICK_RATIO
            and close_position <= MAX_CLOSE_POSITION
        )

        if not has_heavy_rejection:
            continue

        previous_breaks = df.iloc[sweep_idx + 1 : current_index]["close"] < swing_low_to_break
        if previous_breaks.any():
            continue

        # ENFORCEMENT: Point 2 must be the absolute highest peak. No candle after the sweep can go higher.
        intermediate_highs = df.iloc[sweep_idx + 1 : current_index]["high"]
        if (intermediate_highs > sweep_candle["high"]).any():
            continue

        # Point 3: The Breakdown confirmed by a strong red close below the structure
        if current_candle["close"] < swing_low_to_break:
            return {
                "trigger": True,
                "sweep_time": df.index[sweep_idx] if isinstance(df.index, pd.DatetimeIndex) else sweep_idx,
                "breakdown_time": df.index[current_index] if isinstance(df.index, pd.DatetimeIndex) else current_index,
                "liquidity_time": df.index[liquidity_pos] if isinstance(df.index, pd.DatetimeIndex) else liquidity_pos,
                "swing_low_time": df.index[swing_low_pos] if swing_low_pos is not None and isinstance(df.index, pd.DatetimeIndex) else swing_low_pos,
                "liquidity_level": liquidity_high,
                "breakdown_level": swing_low_to_break,
                "sweep_high": float(sweep_candle["high"]), # Point 5 Stop Loss
                "breakdown_close": float(current_candle["close"]),
                "ema_aligned": ema_aligned,
            }

    return {"trigger": False, "reason": "No setup found"}
