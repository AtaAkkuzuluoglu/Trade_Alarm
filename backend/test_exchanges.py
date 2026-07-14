import ccxt
import time

exchanges = [
    ccxt.mexc({'options': {'defaultType': 'swap'}}),
    ccxt.kucoinfutures(),
    ccxt.gate({'options': {'defaultType': 'swap'}}),
    ccxt.okx({'options': {'defaultType': 'swap'}})
]

for ex in exchanges:
    try:
        ex.load_markets()
        keys = list(ex.markets.keys())
        has_btc = any('BTC/USDT' in k for k in keys)
        print(f"{ex.id}: success, has_btc_usdt: {has_btc}, sample: {keys[:3]}")
    except Exception as e:
        print(f"{ex.id}: failed - {type(e).__name__}")
