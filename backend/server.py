"""
Trading Alert Engine — Dual-loop scanner (1D + 1H).

Loop 1 (daily):   Top-200 USDT pairs on KuCoin Futures, min_bars=10
Loop 2 (hourly):  A fixed watchlist from HOURLY_SYMBOLS env / hardcoded, min_bars=15
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any

import ccxt
import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from strategy import EMA_LENGTH, calculate_indicators, detect_sweeps


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


# belge.txt listesinden dönüştürülmüş KuCoin Futures formatındaki saatlik coinler
_DEFAULT_HOURLY_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "XRP/USDT:USDT", "SOL/USDT:USDT",
    "TRX/USDT:USDT", "HYPE/USDT:USDT", "DOGE/USDT:USDT", "ZEC/USDT:USDT",
    "XLM/USDT:USDT", "ADA/USDT:USDT", "XMR/USDT:USDT", "LINK/USDT:USDT",
    "BCH/USDT:USDT", "LTC/USDT:USDT",
    "HBAR/USDT:USDT", "SUI/USDT:USDT", "AVAX/USDT:USDT", "NEAR/USDT:USDT",
    "TAO/USDT:USDT", "UNI/USDT:USDT", "ONDO/USDT:USDT",
    "WLD/USDT:USDT", "DOT/USDT:USDT", "AAVE/USDT:USDT", "ICP/USDT:USDT",
    "PEPE/USDT:USDT", "JUP/USDT:USDT", "ENA/USDT:USDT",
    "FIL/USDT:USDT", "APT/USDT:USDT", "ARB/USDT:USDT",
    "INJ/USDT:USDT", "DASH/USDT:USDT", "CAKE/USDT:USDT", "PENGU/USDT:USDT",
    "RENDER/USDT:USDT", "COMP/USDT:USDT",
    "PNUT/USDT:USDT", "POPCAT/USDT:USDT", "TRB/USDT:USDT",
    "ALGO/USDT:USDT", "BNB/USDT:USDT",
]


def _symbols_from_env(env_name: str, default: list[str]) -> list[str]:
    raw = os.getenv(env_name, "")
    if not raw:
        return default
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


HOURLY_SYMBOLS = _symbols_from_env("HOURLY_SYMBOLS", _DEFAULT_HOURLY_SYMBOLS)
DAILY_TOP_LIMIT = _env_int("DAILY_TOP_LIMIT", 200)
HISTORY_LIMIT_1H = _env_int("HISTORY_LIMIT_1H", 250)
HISTORY_LIMIT_1D = _env_int("HISTORY_LIMIT_1D", 250)
POLL_SECONDS = max(60, _env_int("POLL_SECONDS", 300))
STARTUP_SCAN = _env_bool("STARTUP_SCAN", False)
DEBUG_ALERTS = _env_bool("DEBUG_ALERTS", True)
MAX_ALERT_MEMORY = _env_int("MAX_ALERT_MEMORY", 200)

MIN_BARS_1H = 15
MIN_BARS_1D = 10

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
alerts: list[dict[str, Any]] = []
market_status: dict[str, dict[str, Any]] = {}
seen_alert_keys: set[str] = set()
last_closed_timestamp: dict[str, int] = {}
consumed_points: dict[str, set[tuple[str, int]]] = {}  # per symbol|timeframe
daily_symbols: list[str] = []


# ---------------------------------------------------------------------------
# Connection Manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(payload)
            except RuntimeError:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_to_iso(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _candles_to_frame(candles: list[list[float]]) -> pd.DataFrame:
    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def _fetch_closed_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    limit: int,
) -> list[list[float]]:
    timeframe_ms = exchange.parse_timeframe(timeframe) * 1000
    now_ms = exchange.milliseconds()
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return [row for row in candles if row[0] + timeframe_ms <= now_ms]


# ---------------------------------------------------------------------------
# Alert payload
# ---------------------------------------------------------------------------

def _alert_payload(symbol: str, timeframe: str, result: dict[str, Any]) -> dict[str, Any]:
    direction = result["direction"]  # "LONG" or "SHORT"
    return {
        "id": str(uuid.uuid4()),
        "type": "alert",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "createdAt": _iso_now(),
        "sweepTime": _timestamp_to_iso(result["sweep_time"]),
        "liquidityTime": _timestamp_to_iso(result["liquidity_time"]),
        "liquidityLevel": round(float(result["liquidity_level"]), 8),
        "closePrice": round(float(result["close_price"]), 8),
        "sweepExtreme": round(float(result.get("sweep_low", result.get("sweep_high", 0))), 8),
        "emaAligned": result.get("ema_aligned", False),
        "barsBetween": result.get("bars_between", 0),
    }


async def _push_alert(payload: dict[str, Any]) -> None:
    alerts.append(payload)
    del alerts[:-MAX_ALERT_MEMORY]
    await manager.broadcast(payload)


# ---------------------------------------------------------------------------
# Process a single symbol
# ---------------------------------------------------------------------------

async def _process_symbol(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    history_limit: int,
    min_bars: int,
) -> None:
    candles = await asyncio.to_thread(
        _fetch_closed_ohlcv, exchange, symbol, timeframe, history_limit,
    )
    if len(candles) < EMA_LENGTH:
        market_status[f"{symbol}|{timeframe}"] = {
            "state": "warming",
            "message": f"Only {len(candles)} closed candles available.",
            "updatedAt": _iso_now(),
        }
        return

    df = calculate_indicators(_candles_to_frame(candles))
    latest_closed = int(df.index[-1].timestamp() * 1000)
    cache_key = f"{symbol}|{timeframe}"
    previous_closed = last_closed_timestamp.get(cache_key)

    market_status[cache_key] = {
        "state": "online",
        "timeframe": timeframe,
        "historyCandles": len(df),
        "lastClosedCandle": df.index[-1].isoformat(),
        "updatedAt": _iso_now(),
    }

    if previous_closed is None:
        last_closed_timestamp[cache_key] = latest_closed
        if not STARTUP_SCAN:
            return
        scan_start = max(EMA_LENGTH, len(df) - 250)
    else:
        scan_start = next(
            (idx for idx, ts in enumerate(df.index) if int(ts.timestamp() * 1000) > previous_closed),
            len(df),
        )

    cache_key = f"{symbol}|{timeframe}"
    if cache_key not in consumed_points:
        consumed_points[cache_key] = set()
    consumed = consumed_points[cache_key]

    for current_index in range(scan_start, len(df)):
        results = detect_sweeps(df, current_index, min_bars=min_bars, consumed=consumed)
        for result in results:
            alert_key = f"{symbol}|{timeframe}|{result['direction']}|{result['sweep_time']}|{result['liquidity_time']}"
            if alert_key in seen_alert_keys:
                continue
            seen_alert_keys.add(alert_key)
            await _push_alert(_alert_payload(symbol, timeframe, result))

    last_closed_timestamp[cache_key] = latest_closed


# ---------------------------------------------------------------------------
# Fetch top USDT pairs by volume
# ---------------------------------------------------------------------------

async def _fetch_top_usdt_pairs(exchange: ccxt.Exchange, limit: int = 200) -> list[str]:
    tickers = await asyncio.to_thread(exchange.fetch_tickers)
    pairs = []
    for sym, ticker in tickers.items():
        if sym.endswith(":USDT") and ticker.get("quoteVolume") is not None:
            pairs.append((sym, ticker["quoteVolume"]))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:limit]]


# ---------------------------------------------------------------------------
# Monitor loops
# ---------------------------------------------------------------------------

async def _loop_daily(exchange: ccxt.Exchange) -> None:
    """Scan top-200 USDT pairs on 1D timeframe, min 10 bars."""
    global daily_symbols
    try:
        daily_symbols = await _fetch_top_usdt_pairs(exchange, limit=DAILY_TOP_LIMIT)
    except Exception as exc:
        market_status["DAILY_SYSTEM"] = {"state": "error", "message": f"Failed to fetch daily pairs: {exc}", "updatedAt": _iso_now()}
        return

    while True:
        for symbol in daily_symbols:
            try:
                await _process_symbol(exchange, symbol, "1d", HISTORY_LIMIT_1D, MIN_BARS_1D)
            except Exception as exc:
                market_status[f"{symbol}|1d"] = {
                    "state": "error", "message": str(exc), "updatedAt": _iso_now(),
                }
            await asyncio.sleep(max(exchange.rateLimit / 1000, 0.3))
        await manager.broadcast({"type": "status", "status": market_status})
        await asyncio.sleep(POLL_SECONDS)


async def _loop_hourly(exchange: ccxt.Exchange) -> None:
    """Scan belge.txt watchlist on 1H timeframe, min 15 bars."""
    while True:
        for symbol in HOURLY_SYMBOLS:
            try:
                await _process_symbol(exchange, symbol, "1h", HISTORY_LIMIT_1H, MIN_BARS_1H)
            except Exception as exc:
                market_status[f"{symbol}|1h"] = {
                    "state": "error", "message": str(exc), "updatedAt": _iso_now(),
                }
            await asyncio.sleep(max(exchange.rateLimit / 1000, 0.3))
        await manager.broadcast({"type": "status", "status": market_status})
        await asyncio.sleep(POLL_SECONDS)


async def monitor_markets() -> None:
    exchange = ccxt.kucoinfutures({"enableRateLimit": True})
    try:
        await asyncio.gather(
            _loop_daily(exchange),
            _loop_hourly(exchange),
        )
    except Exception as exc:
        market_status["SYSTEM"] = {
            "state": "error",
            "message": f"Fatal monitor error: {str(exc)}",
            "updatedAt": _iso_now(),
        }
        await manager.broadcast({"type": "status", "status": market_status})
    finally:
        with suppress(Exception):
            exchange.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(monitor_markets())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Trading Alert Engine", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "hourlySymbols": HOURLY_SYMBOLS,
        "dailySymbolCount": len(daily_symbols),
        "pollSeconds": POLL_SECONDS,
    }


@app.get("/status")
async def status() -> dict[str, Any]:
    return {"status": market_status, "alerts": len(alerts)}


@app.get("/alerts")
async def recent_alerts() -> dict[str, Any]:
    return {"alerts": alerts}


@app.post("/debug/test-alert")
async def debug_test_alert() -> dict[str, Any]:
    if not DEBUG_ALERTS:
        return {"sent": False, "reason": "DEBUG_ALERTS is disabled."}

    symbol = HOURLY_SYMBOLS[0] if HOURLY_SYMBOLS else "BTC/USDT:USDT"
    payload = {
        "id": str(uuid.uuid4()),
        "type": "alert",
        "symbol": symbol,
        "timeframe": "1h",
        "direction": "LONG",
        "createdAt": _iso_now(),
        "sweepTime": _iso_now(),
        "liquidityTime": _iso_now(),
        "liquidityLevel": 60000.0,
        "closePrice": 61234.56,
        "sweepExtreme": 59800.0,
        "emaAligned": True,
        "barsBetween": 22,
        "debug": True,
    }
    await _push_alert(payload)
    return {"sent": True, "alert": payload}


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    await websocket.send_json(
        {
            "type": "snapshot",
            "alerts": alerts[-50:],
            "status": market_status,
            "hourlySymbols": HOURLY_SYMBOLS,
            "dailySymbolCount": len(daily_symbols),
        }
    )
    try:
        while True:
            message = await websocket.receive_text()
            if message.lower() == "ping":
                await websocket.send_json({"type": "pong", "at": _iso_now()})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
