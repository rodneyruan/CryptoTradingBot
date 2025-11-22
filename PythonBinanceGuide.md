### Get KLine Data
```
Use client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=KL_HISTORY_LIMIT)
It returns a list of list. 
Each inner list has 12 elements, they are all strings, except timestamps. list[list[str]]
Example.
[
  1698765600000,      # 0:  Open time
  "52251.31",         # 1:  Open
  "52281.00",         # 2:  High
  "52220.00",         # 3:  Low
  "52266.45",         # 4:  Close    ← you usually need this
  "412.553",          # 5:  Volume
  1698769199999,      # 6:  Close time
  "21567891.123",     # 7:  Quote volume
  "28412",            # 8:  Number of trades
  "198.221",          # 9:  Taker buy base volume
  "10356789.12",      # 10: Taker buy quote volume
  "0"                 # 11: Ignore
]

In most case, you just need to get the closed prices.
closed_prices = [ float(k[4]) for k in klines]   # Convert to float immediately

df = pd.DataFrame(
    client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=KL_HISTORY_LIMIT),
    columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'num_trades',
        'taker_buy_base', 'taker_buy_quote', 'ignore'
    ]
df['close'] = df['close'].astype(float)
df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
df['close_time'] = pd.to_datetime(df['close_time'], unit='ms')
)
```
