from __future__ import annotations

import argparse
import html
import json
import math
import time
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd

from strategy import calculate_indicators, detect_setup


DEFAULT_SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "XRP/USDT:USDT"]
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
            symbol,
            timeframe=timeframe,
            since=since,
            limit=min(max_per_request, remaining),
        )
        if not batch:
            break

        if candles:
            last_timestamp = candles[-1][0]
            batch = [row for row in batch if row[0] > last_timestamp]
        candles.extend(batch)

        if not batch:
            break

        since = int(candles[-1][0] + timeframe_ms)
        if len(batch) < min(max_per_request, remaining):
            break

        time.sleep(max(exchange.rateLimit / 1000, 0.2))

    cutoff = exchange.milliseconds() - timeframe_ms
    return [row for row in candles[-target_limit:] if row[0] <= cutoff]


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
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    print(f"\nStarting backtest for {symbol} on {timeframe} timeframe...")
    candles = fetch_ohlcv_history(exchange, symbol, timeframe, limit)
    df = calculate_indicators(candles_to_frame(candles))
    print(f"Downloaded {len(df)} closed candles.")

    triggers: list[dict[str, Any]] = []
    for i in range(200, len(df)):
        result = detect_setup(df, i)
        if result["trigger"]:
            result["symbol"] = symbol
            result["timeframe"] = timeframe
            triggers.append(result)
            print(
                "[ALERT] "
                f"{symbol} sweep={result['sweep_time']} "
                f"breakdown={result['breakdown_time']} "
                f"liquidity=${result['liquidity_level']:.4f} "
                f"breakdown=${result['breakdown_level']:.4f}"
            )

    print(f"Backtest complete for {symbol}. Found {len(triggers)} setup(s).")
    return df, triggers


def _price_to_y(price: float, min_price: float, max_price: float, height: int) -> float:
    if max_price <= min_price:
        return height / 2
    return 24 + ((max_price - price) / (max_price - min_price)) * (height - 56)


def _x_for_index(index: int, candle_count: int, width: int) -> float:
    if candle_count <= 1:
        return width / 2
    return 48 + (index / (candle_count - 1)) * (width - 96)


def render_setup_svg(df: pd.DataFrame, trigger: dict[str, Any], window: int = 60) -> str:
    breakdown_pos = int(df.index.get_loc(trigger["breakdown_time"]))
    start = max(0, breakdown_pos - window)
    end = min(len(df), breakdown_pos + 12)
    view = df.iloc[start:end]

    width = 1000
    height = 420
    min_price = float(min(view["low"].min(), trigger["breakdown_level"]))
    max_price = float(max(view["high"].max(), trigger["liquidity_level"]))
    pad = (max_price - min_price) * 0.06 or max_price * 0.01
    min_price -= pad
    max_price += pad

    candle_width = max(3, min(10, int((width - 120) / max(len(view), 1) * 0.56)))
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'xmlns="http://www.w3.org/2000/svg">'
    ]
    parts.append('<rect width="100%" height="100%" fill="#111111" rx="6" />')
    parts.append('<g stroke="#2d2d2d" stroke-width="1">')
    for grid in range(5):
        y = 24 + grid * ((height - 56) / 4)
        parts.append(f'<line x1="40" x2="{width - 32}" y1="{y:.2f}" y2="{y:.2f}" />')
    parts.append("</g>")

    for local_idx, row in enumerate(view.itertuples()):
        x = _x_for_index(local_idx, len(view), width)
        high_y = _price_to_y(float(row.high), min_price, max_price, height)
        low_y = _price_to_y(float(row.low), min_price, max_price, height)
        open_y = _price_to_y(float(row.open), min_price, max_price, height)
        close_y = _price_to_y(float(row.close), min_price, max_price, height)
        color = "#2fd17c" if row.close >= row.open else "#f05d5e"
        rect_y = min(open_y, close_y)
        rect_h = max(2, abs(open_y - close_y))
        parts.append(f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{high_y:.2f}" y2="{low_y:.2f}" stroke="{color}" stroke-width="1.5" />')
        parts.append(f'<rect x="{x - candle_width / 2:.2f}" y="{rect_y:.2f}" width="{candle_width}" height="{rect_h:.2f}" fill="{color}" rx="1" />')

    levels = [
        ("", trigger["liquidity_level"], "#3b82f6"),
        ("", trigger["sweep_high"], "#d946ef"),
        ("", trigger["breakdown_level"], "#ef4444"),
    ]
    for label, price, color in levels:
        y = _price_to_y(float(price), min_price, max_price, height)
        parts.append(f'<line x1="40" x2="{width - 32}" y1="{y:.2f}" y2="{y:.2f}" stroke="{color}" stroke-width="1.5" stroke-dasharray="6 6" />')
        parts.append(f'<text x="{width - 28}" y="{y + 5:.2f}" fill="{color}" font-size="14" font-family="Arial" font-weight="bold" text-anchor="end">{label} {float(price):.4f}</text>')

    markers = [
        ("Sweep", trigger["sweep_time"], "#d946ef"),
        ("Break", trigger["breakdown_time"], "#f43f5e"),
    ]
    for label, timestamp, color in markers:
        pos = int(df.index.get_loc(timestamp)) - start
        if 0 <= pos < len(view):
            x = _x_for_index(pos, len(view), width)
            parts.append(f'<line x1="{x:.2f}" x2="{x:.2f}" y1="18" y2="{height - 26}" stroke="{color}" stroke-width="1.2" opacity="0.6" stroke-dasharray="4 4" />')
            parts.append(f'<text x="{x + 6:.2f}" y="38" fill="{color}" font-size="14" font-family="Arial" font-weight="bold">{label}</text>')

    def draw_num(num_str: str, timestamp, price: float, bg_color: str, offset_y: int):
        pos = int(df.index.get_loc(timestamp)) - start
        if 0 <= pos < len(view):
            x = _x_for_index(pos, len(view), width)
            y = _price_to_y(float(price), min_price, max_price, height) + offset_y
            parts.append(f'<rect x="{x - 12:.2f}" y="{y - 14:.2f}" width="24" height="24" fill="{bg_color}" rx="4" opacity="1.0"/>')
            parts.append(f'<text x="{x:.2f}" y="{y + 4:.2f}" fill="#ffffff" font-size="14" font-family="Arial" font-weight="bold" text-anchor="middle">{num_str}</text>')

    draw_num("1", trigger["liquidity_time"], trigger["liquidity_level"], "#3b82f6", -24)
    draw_num("2", trigger["sweep_time"], trigger["sweep_high"], "#d946ef", -24)
    draw_num("3", trigger["breakdown_time"], trigger["breakdown_close"], "#f43f5e", 24)

    parts.append("</svg>")
    return "".join(parts)


def _json_ready(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def render_report(results: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]], timeframe: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d-%H%M%S")
    output_path = REPORT_DIR / f"short-setups-{timeframe}-{timestamp}.html"

    total = sum(len(triggers) for _, triggers in results.values())
    sections: list[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Trading Alerts Backtest</title>",
        "<style>",
        "body{margin:0;background:#0b0b0c;color:#eeeeee;font-family:Inter,Segoe UI,Arial,sans-serif;}",
        "main{max-width:1180px;margin:0 auto;padding:28px 20px 48px;}",
        "h1{font-size:28px;margin:0 0 6px;} h2{font-size:18px;margin:28px 0 10px;}",
        ".muted{color:#aaa;} .setup{border:1px solid #2b2b2e;border-radius:8px;margin:16px 0;padding:14px;background:#151518;}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:10px 0 14px;}",
        ".metric{background:#101013;border:1px solid #29292c;border-radius:6px;padding:9px 10px;}",
        ".metric b{display:block;color:#fff;font-size:13px}.metric span{color:#b8b8bd;font-size:12px;}",
        "svg{width:100%;height:auto;display:block;border-radius:6px;}",
        "pre{white-space:pre-wrap;color:#cfcfcf;background:#111;border:1px solid #2b2b2e;border-radius:6px;padding:12px;}",
        "</style></head><body><main>",
        f"<h1>Short Setup Backtest</h1><div class='muted'>Timeframe: {html.escape(timeframe)} | Total setups: {total}</div>",
    ]

    if total == 0:
        sections.append(
            "<pre>No valid setups were detected with the current strict rules. "
            "Try a larger --limit or more --symbols before loosening the strategy.</pre>"
        )

    for symbol, (df, triggers) in results.items():
        sections.append(f"<h2>{html.escape(symbol)} <span class='muted'>({len(triggers)} setup(s))</span></h2>")
        for trigger in triggers:
            sections.append("<section class='setup'>")
            sections.append("<div class='grid'>")
            metrics = [
                ("Liquidity", f"{trigger['liquidity_level']:.4f}", trigger["liquidity_time"]),
                ("Sweep High", f"{trigger['sweep_high']:.4f}", trigger["sweep_time"]),
                ("Breakdown", f"{trigger['breakdown_level']:.4f}", trigger["breakdown_time"]),
                ("Close", f"{trigger['breakdown_close']:.4f}", trigger["breakdown_time"]),
            ]
            for label, value, when in metrics:
                sections.append(
                    "<div class='metric'>"
                    f"<b>{html.escape(label)}: {html.escape(value)}</b>"
                    f"<span>{html.escape(str(when))}</span>"
                    "</div>"
                )
            sections.append("</div>")
            sections.append(render_setup_svg(df, trigger))
            sections.append("</section>")

    summary = {
        symbol: [
            {key: _json_ready(value) for key, value in trigger.items()}
            for trigger in triggers
        ]
        for symbol, (_, triggers) in results.items()
    }
    sections.append(f"<script type='application/json' id='summary'>{html.escape(json.dumps(summary, indent=2))}</script>")
    sections.append("</main></body></html>")
    output_path.write_text("".join(sections), encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the 1H long setup against Binance OHLCV data.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Binance symbols such as BTC/USDT ETH/USDT.")
    parser.add_argument("--timeframe", default="1h", help="CCXT timeframe. Production logic is designed for 1h.")
    parser.add_argument("--limit", type=int, default=3000, help="Closed candles to fetch per symbol.")
    parser.add_argument("--no-report", action="store_true", help="Skip writing the HTML candlestick report.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    results: dict[str, tuple[pd.DataFrame, list[dict[str, Any]]]] = {}

    for symbol in args.symbols:
        try:
            results[symbol] = run_symbol_backtest(exchange, symbol, args.timeframe, args.limit)
        except Exception as exc:
            print(f"[ERROR] {symbol}: {exc}")

    total = sum(len(triggers) for _, triggers in results.values())
    print(f"\nBacktest sweep complete. Found {total} total setup(s).")
    if not args.no_report:
        report_path = render_report(results, args.timeframe)
        print(f"Visual report written to: {report_path}")


if __name__ == "__main__":
    main()
