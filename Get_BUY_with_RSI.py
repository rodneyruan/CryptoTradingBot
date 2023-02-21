import time
from binance.client import Client
import pandas as pd
import ta
import sys
import os

if( len(sys.argv) >= 2):
    interval = sys.argv [1]
else
    interval = '4h'

print("interval is ", interval)

API_key =os.environ.get("binance_api_key")
Security_key = os.environ.get("binance_security_key")
client = Client(API_key, Security_key)

def get_data_frame(symbol, interval):
    bars = client.get_historical_klines(symbol, interval, limit=100)
    for line in bars:
        # Keep only first 5 columns, "date" "open" "high" "low" "close"
        del line[5:]
    df = pd.DataFrame(bars, columns=['date', 'open', 'high', 'low', 'close']) #  2 dimensional tabular data
    return df

info = client.get_exchange_info()
for c in info['symbols']:
    if c['quoteAsset']=='USDT' and c['status']=="TRADING":
        symbol_df = get_data_frame(c['symbol'],interval)
        rsi= ta.momentum.RSIIndicator(pd.to_numeric(symbol_df.close), window=6).rsi()
        if( ( rsi.iloc[-1] >=30 ) and ( rsi.iloc[-2] >30 ) and ( rsi.iloc[-3] < 30)  and ( rsi.iloc[-4] <30 )):
            print(c['symbol'], " RSI crossed above 30, Buy signal triggered.")
        elif( ( rsi.iloc[-1] >=30 ) and ( rsi.iloc[-2] >30 ) and ( rsi.iloc[-3] > 30)  and ( rsi.iloc[-4] <30 ) and ( rsi.iloc[-5] <30 )):
            print(c['symbol'], " RSI crossed above 30, Buy signal triggered.")
        elif( ( rsi.iloc[-1] >=30 ) and ( rsi.iloc[-2] >30 ) and ( rsi.iloc[-3] > 30)  and ( rsi.iloc[-4] >30 ) and ( rsi.iloc[-5] <30 ) and ( rsi.iloc[-6] <30 )):
            print(c['symbol'], " RSI crossed above 30, Buy signal triggered.")
        #time.sleep(1)