import ccxt
import asyncio

async def test():
    exchange = ccxt.kucoinfutures()
    tickers = await asyncio.to_thread(exchange.fetch_tickers)
    print("Fetched", len(tickers), "tickers")

asyncio.run(test())
