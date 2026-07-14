import ccxt

ex = ccxt.kucoinfutures()
res = ex.fetch_ohlcv('BTC/USDT:USDT', '1h', limit=600)
print(f"Len: {len(res)}")
print(f"Last candle: {res[-1][0]}")
