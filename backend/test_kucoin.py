import ccxt

ex = ccxt.kucoinfutures()
tickers = ex.fetch_tickers()
print(f"Number of tickers: {len(tickers)}")
ticker = tickers.get('BTC/USDT:USDT')
if ticker:
    print(f"BTC/USDT:USDT quoteVolume: {ticker.get('quoteVolume')}")
else:
    print("BTC/USDT:USDT not found")
