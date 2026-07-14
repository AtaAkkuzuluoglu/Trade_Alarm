import ccxt
import asyncio

async def test():
    exchange = ccxt.kucoinfutures()
    tickers = await asyncio.to_thread(exchange.fetch_tickers)
    print("Fetched", len(tickers), "tickers")
    for sym, ticker in list(tickers.items())[:5]:
        print(sym, ticker.get("quoteVolume"))

asyncio.run(test())
