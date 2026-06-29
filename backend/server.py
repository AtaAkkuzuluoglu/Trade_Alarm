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

from strategy import EMA_LENGTH, calculate_indicators, detect_setup


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


def _symbols_from_env() -> list[str]:
    raw = os.getenv("SYMBOLS", "")
    if not raw:
        return []
    return [symbol.strip().upper() for symbol in raw.split(",") if symbol.strip()]


SYMBOLS = _symbols_from_env()
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
HISTORY_LIMIT = max(EMA_LENGTH + 100, _env_int("HISTORY_LIMIT", 600))
POLL_SECONDS = max(60, _env_int("POLL_SECONDS", 300))
STARTUP_SCAN = _env_bool("STARTUP_SCAN", False)
DEBUG_ALERTS = _env_bool("DEBUG_ALERTS", True)
MAX_ALERT_MEMORY = _env_int("MAX_ALERT_MEMORY", 200)

alerts: list[dict[str, Any]] = []
market_status: dict[str, dict[str, Any]] = {}
seen_alert_keys: set[str] = set()
last_closed_timestamp: dict[str, int] = {}
monitor_task: asyncio.Task | None = None


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale_connections: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(payload)
            except RuntimeError:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self.disconnect(websocket)


manager = ConnectionManager()


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


def _alert_payload(symbol: str, timeframe: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "type": "alert",
        "symbol": symbol,
        "timeframe": timeframe,
        "createdAt": _iso_now(),
        "sweepTime": _timestamp_to_iso(result["sweep_time"]),
        "breakdownTime": _timestamp_to_iso(result["breakdown_time"]),
        "liquidityTime": _timestamp_to_iso(result["liquidity_time"]),
        "swingLowTime": _timestamp_to_iso(result["swing_low_time"]),
        "liquidityLevel": round(float(result["liquidity_level"]), 8),
        "breakdownLevel": round(float(result["breakdown_level"]), 8),
        "sweepHigh": round(float(result["sweep_high"]), 8),
        "breakdownClose": round(float(result["breakdown_close"]), 8),
    }


async def _push_alert(payload: dict[str, Any]) -> None:
    alerts.append(payload)
    del alerts[:-MAX_ALERT_MEMORY]
    await manager.broadcast(payload)


async def _process_symbol(exchange: ccxt.Exchange, symbol: str) -> None:
    candles = await asyncio.to_thread(
        _fetch_closed_ohlcv,
        exchange,
        symbol,
        TIMEFRAME,
        HISTORY_LIMIT,
    )
    if len(candles) < EMA_LENGTH:
        market_status[symbol] = {
            "state": "warming",
            "message": f"Only {len(candles)} closed candles available.",
            "updatedAt": _iso_now(),
        }
        return

    df = calculate_indicators(_candles_to_frame(candles))
    latest_closed = int(df.index[-1].timestamp() * 1000)
    previous_closed = last_closed_timestamp.get(symbol)

    market_status[symbol] = {
        "state": "online",
        "timeframe": TIMEFRAME,
        "historyCandles": len(df),
        "lastClosedCandle": df.index[-1].isoformat(),
        "updatedAt": _iso_now(),
    }

    if previous_closed is None:
        last_closed_timestamp[symbol] = latest_closed
        if not STARTUP_SCAN:
            return
        scan_start = max(EMA_LENGTH, len(df) - 250)
    else:
        scan_start = next(
            (idx for idx, timestamp in enumerate(df.index) if int(timestamp.timestamp() * 1000) > previous_closed),
            len(df),
        )

    for current_index in range(scan_start, len(df)):
        result = detect_setup(df, current_index)
        if not result["trigger"]:
            continue

        alert_key = f"{symbol}|{result['sweep_time']}|{result['breakdown_time']}"
        if alert_key in seen_alert_keys:
            continue

        seen_alert_keys.add(alert_key)
        await _push_alert(_alert_payload(symbol, TIMEFRAME, result))

    last_closed_timestamp[symbol] = latest_closed


async def _fetch_top_usdt_pairs(exchange: ccxt.Exchange, limit: int = 50) -> list[str]:
    tickers = await asyncio.to_thread(exchange.fetch_tickers)
    pairs = []
    for sym, ticker in tickers.items():
        if sym.endswith(':USDT') and ticker.get('quoteVolume') is not None:
            pairs.append((sym, ticker['quoteVolume']))
    pairs.sort(key=lambda x: x[1], reverse=True)
    return [p[0] for p in pairs[:limit]]


async def monitor_markets() -> None:
    exchange = ccxt.kucoinfutures({"enableRateLimit": True})
    global SYMBOLS
    try:
        if not SYMBOLS:
            SYMBOLS = await _fetch_top_usdt_pairs(exchange, limit=50)
            
        while True:
            for symbol in SYMBOLS:
                try:
                    await _process_symbol(exchange, symbol)
                except Exception as exc:
                    market_status[symbol] = {
                        "state": "error",
                        "message": str(exc),
                        "updatedAt": _iso_now(),
                    }
                await asyncio.sleep(max(exchange.rateLimit / 1000, 0.2))
            await manager.broadcast({"type": "status", "status": market_status})
            await asyncio.sleep(POLL_SECONDS)
    except Exception as exc:
        market_status["SYSTEM"] = {"state": "error", "message": f"Fatal monitor error: {str(exc)}", "updatedAt": _iso_now()}
        await manager.broadcast({"type": "status", "status": market_status})
    finally:
        with suppress(Exception):
            exchange.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global monitor_task
    monitor_task = asyncio.create_task(monitor_markets())
    try:
        yield
    finally:
        if monitor_task:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task


app = FastAPI(title="Trading Alert Engine", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "symbols": SYMBOLS,
        "timeframe": TIMEFRAME,
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

    symbol = SYMBOLS[0] if SYMBOLS else "NEIRO/USDT:USDT"
    payload = {
        "id": str(uuid.uuid4()),
        "type": "alert",
        "symbol": symbol,
        "timeframe": TIMEFRAME,
        "createdAt": _iso_now(),
        "sweepTime": _iso_now(),
        "breakdownTime": _iso_now(),
        "liquidityTime": _iso_now(),
        "swingLowTime": _iso_now(),
        "liquidityLevel": 0.00008091,
        "breakdownLevel": 0.00007702,
        "sweepHigh": 0.00008159,
        "breakdownClose": 0.00007332,
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
            "symbols": SYMBOLS,
            "timeframe": TIMEFRAME,
        }
    )
    try:
        while True:
            message = await websocket.receive_text()
            if message.lower() == "ping":
                await websocket.send_json({"type": "pong", "at": _iso_now()})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
