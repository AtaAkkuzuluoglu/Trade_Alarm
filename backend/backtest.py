"""
Backtest for the Liquidity Sweep strategy (LONG + SHORT).
Generates an HTML report with SVG candlestick charts.
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import time
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd

from strategy import EMA_LENGTH, calculate_indicators, detect_sweeps


DEFAULT_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "BNB/USDT:USDT", "XRP/USDT:USDT", "ADA/USDT:USDT",
    "HBAR/USDT:USDT", "AVAX/USDT:USDT", "LINK/USDT:USDT",
]
REPORT_DIR = Path(__file__).resolve().parent / "backtest_reports"


def fetch_ohlcv_history(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    target_limit: int,
) -> list[list[float]]:
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    max_per_request = 1000
    since = exchange.milliseconds() - (target_limit * timeframe_ms)
    candles: list[list[float]] = []

    while len(candles) < target_limit:
        remaining = target_limit - len(candles)
        batch = exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, since=since,
            limit=min(max_per_request, remaining),
        )
        if not batch:
            break
        if candles:
            last_ts = candles[-1][0]
            batch = [r for r in batch if r[0] > last_ts]
        candles.extend(batch)
        if not batch:
            break
        since = int(candles[-1][0] + timeframe_ms)
        if len(batch) < min(max_per_request, remaining):
            break
        time.sleep(max(exchange.rateLimit / 1000, 0.2))

    cutoff = exchange.milliseconds() - timeframe_ms
    return [r for r in candles[-target_limit:] if r[0] <= cutoff]


def candles_to_frame(candles: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def run_symbol_backtest(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    limit: int,
    min_bars: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    print(f"\nStarting backtest for {symbol} on {timeframe} timeframe (min_bars={min_bars})...")
    candles = fetch_ohlcv_history(exchange, symbol, timeframe, limit)
    df = calculate_indicators(candles_to_frame(candles))
    print(f"Downloaded {len(df)} closed candles.")

    triggers: list[dict[str, Any]] = []
    consumed: set[tuple[str, int]] = set()
    for i in range(EMA_LENGTH, len(df)):
        results = detect_sweeps(df, i, min_bars=min_bars, consumed=consumed)
        for result in results:
            result["symbol"] = symbol
            result["timeframe"] = timeframe
            triggers.append(result)
            direction = result["direction"]
            level_key = "sweep_low" if direction == "LONG" else "sweep_high"
            level_val = result.get(level_key, 0)
            print(
                f"[{direction}] {symbol} "
                f"sweep={result['sweep_time']} "
                f"liquidity_time={result['liquidity_time']} "
                f"liquidity=${result['liquidity_level']:.4f} "
                f"close=${result['close_price']:.4f} "
                f"bars={result['bars_between']}"
            )

    print(f"Backtest complete for {symbol}. Found {len(triggers)} setup(s).")
    return df, triggers


# ---------------------------------------------------------------------------
# SVG rendering
# ---------------------------------------------------------------------------

def _price_to_y(price: float, mn: float, mx: float, h: int) -> float:
    if mx <= mn:
        return h / 2
    return 24 + ((mx - price) / (mx - mn)) * (h - 56)


def _x_for_index(idx: int, count: int, w: int) -> float:
    if count <= 1:
        return w / 2
    return 48 + (idx / (count - 1)) * (w - 96)


def render_setup_svg(df: pd.DataFrame, trigger: dict[str, Any], window: int = 60) -> str:
    sweep_pos = int(df.index.get_loc(trigger["sweep_time"]))
    start = max(0, sweep_pos - window)
    end = min(len(df), sweep_pos + 12)
    view = df.iloc[start:end]

    width, height = 1000, 420
    is_long = trigger["direction"] == "LONG"

    liq_level = trigger["liquidity_level"]
    mn = float(min(view["low"].min(), liq_level))
    mx = float(max(view["high"].max(), liq_level))
    pad = (mx - mn) * 0.06 or mx * 0.01
    mn -= pad
    mx += pad

    cw = max(3, min(10, int((width - 120) / max(len(view), 1) * 0.56)))
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" xmlns="http://www.w3.org/2000/svg">',
        '<rect width="100%" height="100%" fill="#111111" rx="6" />',
        '<g stroke="#2d2d2d" stroke-width="1">',
    ]
    for g in range(5):
        y = 24 + g * ((height - 56) / 4)
        parts.append(f'<line x1="40" x2="{width - 32}" y1="{y:.2f}" y2="{y:.2f}" />')
    parts.append("</g>")

    for li, row in enumerate(view.itertuples()):
        x = _x_for_index(li, len(view), width)
        hy = _price_to_y(float(row.high), mn, mx, height)
        ly = _price_to_y(float(row.low), mn, mx, height)
        oy = _price_to_y(float(row.open), mn, mx, height)
        cy = _price_to_y(float(row.close), mn, mx, height)
        color = "#2fd17c" if row.close >= row.open else "#f05d5e"
        ry = min(oy, cy)
        rh = max(2, abs(oy - cy))
        parts.append(f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{hy:.2f}" y2="{ly:.2f}" stroke="{color}" stroke-width="1.5" />')
        parts.append(f'<rect x="{x - cw / 2:.2f}" y="{ry:.2f}" width="{cw}" height="{rh:.2f}" fill="{color}" rx="1" />')

    # Liquidity level line
    liq_color = "#22c55e" if is_long else "#ef4444"
    ly = _price_to_y(float(liq_level), mn, mx, height)
    parts.append(f'<line x1="40" x2="{width - 32}" y1="{ly:.2f}" y2="{ly:.2f}" stroke="{liq_color}" stroke-width="1.5" stroke-dasharray="6 6" />')
    label = "Sellside Liquidity" if is_long else "Buyside Liquidity"
    parts.append(f'<text x="{width - 28}" y="{ly + 5:.2f}" fill="{liq_color}" font-size="13" font-family="Arial" font-weight="bold" text-anchor="end">{label} {float(liq_level):.4f}</text>')

    # Point 1 and 2 markers
    def draw_num(n: str, ts, price: float, bg: str, oy: int):
        pos = int(df.index.get_loc(ts)) - start
        if 0 <= pos < len(view):
            x = _x_for_index(pos, len(view), width)
            y = _price_to_y(float(price), mn, mx, height) + oy
            parts.append(f'<rect x="{x - 12:.2f}" y="{y - 14:.2f}" width="24" height="24" fill="{bg}" rx="4" />')
            parts.append(f'<text x="{x:.2f}" y="{y + 4:.2f}" fill="#ffffff" font-size="14" font-family="Arial" font-weight="bold" text-anchor="middle">{n}</text>')

    if is_long:
        draw_num("1", trigger["liquidity_time"], trigger["liquidity_level"], "#22c55e", 24)
        draw_num("2", trigger["sweep_time"], trigger.get("sweep_low", trigger["close_price"]), "#3b82f6", 24)
    else:
        draw_num("1", trigger["liquidity_time"], trigger["liquidity_level"], "#ef4444", -24)
        draw_num("2", trigger["sweep_time"], trigger.get("sweep_high", trigger["close_price"]), "#3b82f6", -24)

    parts.append("</svg>")
    return "".join(parts)


def _json_ready(v: Any) -> Any:
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def render_report(results: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]], timeframe: str, min_bars: int) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d-%H%M%S")
    output_path = REPORT_DIR / f"sweep-setups-{timeframe}-{ts}.html"

    total = sum(len(trigs) for _, trigs in results.values())
    long_total = sum(1 for _, trigs in results.values() for t in trigs if t["direction"] == "LONG")
    short_total = total - long_total

    sections: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Liquidity Sweep Backtest</title>",
        "<style>",
        "body{margin:0;background:#0b0b0c;color:#eeeeee;font-family:Inter,Segoe UI,Arial,sans-serif;}",
        "main{max-width:1180px;margin:0 auto;padding:28px 20px 48px;}",
        "h1{font-size:28px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 10px;}",
        ".muted{color:#aaa;} .setup{border:1px solid #2b2b2e;border-radius:8px;margin:16px 0;padding:14px;background:#151518;}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:10px 0 14px;}",
        ".metric{background:#101013;border:1px solid #29292c;border-radius:6px;padding:9px 10px;}",
        ".metric b{display:block;color:#fff;font-size:13px}.metric span{color:#b8b8bd;font-size:12px;}",
        "svg{width:100%;height:auto;display:block;border-radius:6px;}",
        ".long{border-left:3px solid #22c55e;} .short{border-left:3px solid #ef4444;}",
        ".badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;margin-left:8px;}",
        ".badge-long{background:#22c55e20;color:#22c55e;border:1px solid #22c55e40;}",
        ".badge-short{background:#ef444420;color:#ef4444;border:1px solid #ef444440;}",
        ".badge-ema{background:#a855f720;color:#a855f7;border:1px solid #a855f740;}",
        "</style></head><body><main>",
        f"<h1>Liquidity Sweep Backtest</h1>"
        f"<div class='muted'>Timeframe: {html_mod.escape(timeframe)} | Min bars: {min_bars} | "
        f"Total: {total} (🟢 LONG: {long_total}, 🔴 SHORT: {short_total})</div>",
    ]

    if total == 0:
        sections.append("<p class='muted'>No valid setups detected.</p>")

    for symbol, (df, triggers) in results.items():
        longs = sum(1 for t in triggers if t["direction"] == "LONG")
        shorts = len(triggers) - longs
        sections.append(
            f"<h2>{html_mod.escape(symbol)} "
            f"<span class='muted'>({len(triggers)} setup: 🟢{longs} 🔴{shorts})</span></h2>"
        )
        for trigger in triggers:
            d = trigger["direction"]
            css = "long" if d == "LONG" else "short"
            badge_css = "badge-long" if d == "LONG" else "badge-short"
            sections.append(f"<section class='setup {css}'>")
            sections.append(
                f"<div><span class='badge {badge_css}'>{d}</span>"
                + (f"<span class='badge badge-ema'>EMA 200</span>" if trigger.get("ema_aligned") else "")
                + f"<span class='muted' style='margin-left:8px'>Bars: {trigger['bars_between']}</span></div>"
            )
            sections.append("<div class='grid'>")
            metrics = [
                ("Liquidity Level", f"{trigger['liquidity_level']:.4f}", trigger["liquidity_time"]),
                ("Close Price", f"{trigger['close_price']:.4f}", trigger["sweep_time"]),
                ("Sweep Extreme", f"{trigger.get('sweep_low', trigger.get('sweep_high', 0)):.4f}", trigger["sweep_time"]),
            ]
            for label, val, when in metrics:
                sections.append(
                    "<div class='metric'>"
                    f"<b>{html_mod.escape(label)}: {html_mod.escape(val)}</b>"
                    f"<span>{html_mod.escape(str(when))}</span>"
                    "</div>"
                )
            sections.append("</div>")
            sections.append(render_setup_svg(df, trigger))
            sections.append("</section>")

    summary = {
        sym: [{k: _json_ready(v) for k, v in t.items()} for t in trigs]
        for sym, (_, trigs) in results.items()
    }
    sections.append(f"<script type='application/json' id='summary'>{html_mod.escape(json.dumps(summary, indent=2))}</script>")
    sections.append("</main></body></html>")
    output_path.write_text("".join(sections), encoding="utf-8")
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the Liquidity Sweep strategy.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--min-bars", type=int, default=15)
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--no-report", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    results: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]] = {}

    for symbol in args.symbols:
        try:
            results[symbol] = run_symbol_backtest(
                exchange, symbol, args.timeframe, args.limit, args.min_bars,
            )
        except Exception as exc:
            print(f"[ERROR] {symbol}: {exc}")

    total = sum(len(trigs) for _, trigs in results.values())
    print(f"\nBacktest sweep complete. Found {total} total setup(s).")
    if not args.no_report:
        report_path = render_report(results, args.timeframe, args.min_bars)
        print(f"Visual report written to: {report_path}")


if __name__ == "__main__":
    main()
