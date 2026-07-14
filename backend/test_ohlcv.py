import ccxt
import asyncio

async def test():
    exchange = ccxt.kucoinfutures({"enableRateLimit": True})
    candles = await asyncio.to_thread(exchange.fetch_ohlcv, "BTC/USDT:USDT", "1d", limit=200)
    print("Fetched", len(candles), "1D candles")

asyncio.run(test())
