import ccxt
ex = ccxt.kucoinfutures()
res = ex.fetch_ohlcv('BTC/USDT:USDT', '1h', limit=201)
print(len(res))
