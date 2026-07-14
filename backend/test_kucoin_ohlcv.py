import ccxt
from datetime import datetime

ex = ccxt.kucoinfutures({'enableRateLimit': True})
ohlcv = ex.fetch_ohlcv('BTC/USDT:USDT', '1h', limit=5)
for candle in ohlcv:
    ts = candle[0]
    print(f"Candle time: {datetime.fromtimestamp(ts/1000).isoformat()} - Close: {candle[4]}")
