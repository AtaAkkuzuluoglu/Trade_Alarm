import ccxt
import time

ex = ccxt.kucoinfutures()
timeframe_ms = ex.parse_timeframe('1h') * 1000
now_ms = ex.milliseconds()
since = now_ms - (600 * timeframe_ms)

res = ex.fetch_ohlcv('BTC/USDT:USDT', '1h', limit=600, since=since)
print(f"Len: {len(res)}")
print(f"First: {res[0][0]}")
print(f"Last: {res[-1][0]}")
