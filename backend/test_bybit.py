import ccxt
exchange = ccxt.bybit({'options': {'defaultType': 'swap'}})
exchange.load_markets()
print('BTC/USDT:USDT' in exchange.markets)
