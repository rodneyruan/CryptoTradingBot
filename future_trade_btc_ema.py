#!/usr/bin/env python3
"""
Futures EMA WebSocket Bot (timeframe from argv[1], default 5m)
 - Limit Buy when EMA_FAST crosses above EMA_SLOW
 - TP placed after buy fills (limit sell)
 - NO stop-loss order at entry
 - Stop-loss handled by monitoring each closed candle:
     * If close <= EMA200, close the position at market
"""

import sys
import time
import pandas as pd
import pytz
from datetime import datetime
from binance.client import Client
from binance.streams import ThreadedWebsocketManager

from key_config import apikey, secret

SYMBOL = "BTCUSDT"
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200

# ============================
#   Futures Client
# ============================
client = Client(api_key=apikey, api_secret=secret)
client.futures_change_leverage(symbol=SYMBOL, leverage=3)

# ============================
#   Load Historical Candles
# ============================
def get_klines(tf):
    raw = client.futures_klines(symbol=SYMBOL, interval=tf, limit=500)
    df = pd.DataFrame(raw, columns=[
        'open_time','open','high','low','close','volume',
        'close_time','qav','num_trades','taker_base','taker_quote','ignore'
    ])
    df['open'] = df['open'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['close'] = df['close'].astype(float)
    return df[['open','high','low','close']]

tf = sys.argv[1] if len(sys.argv) > 1 else "5m"
df = get_klines(tf)

# ============================
#   Compute EMAs
# ============================
def update_emas(df):
    df['ema_fast'] = df['close'].ewm(span=EMA_FAST).mean()
    df['ema_slow'] = df['close'].ewm(span=EMA_SLOW).mean()
    df['ema_trend'] = df['close'].ewm(span=EMA_TREND).mean()
    return df

df = update_emas(df)

# ============================
#   Globals
# ============================
position_open = False
entry_price = None
twm = ThreadedWebsocketManager()

# ============================
#   Orders
# ============================
def place_limit_buy(price):
    # ?? Modified as requested: **1-digit precision**
    price = round(price, 1)

    return client.futures_create_order(
        symbol=SYMBOL,
        side="BUY",
        type="LIMIT",
        timeInForce="GTC",
        quantity=0.001,
        price=str(price)
    )

def place_limit_sell(price):
    return client.futures_create_order(
        symbol=SYMBOL,
        side="SELL",
        type="LIMIT",
        timeInForce="GTC",
        quantity=0.001,
        price=str(price)
    )

def close_position_market():
    return client.futures_create_order(
        symbol=SYMBOL,
        side="SELL",
        type="MARKET",
        quantity=0.001
    )

# ============================
#   WebSocket Handler
# ============================
def handle_msg(msg):
    global df, position_open, entry_price

    if msg['e'] != 'kline':
        return

    k = msg['k']
    closed = k['x']
    close_price = float(k['c'])

    if closed:
        # Add new row
        df.loc[len(df)] = [
            float(k['o']),
            float(k['h']),
            float(k['l']),
            close_price
        ]
        df = update_emas(df)

        ema_fast_prev = df['ema_fast'].iloc[-2]
        ema_slow_prev = df['ema_slow'].iloc[-2]
        ema_fast_now  = df['ema_fast'].iloc[-1]
        ema_slow_now  = df['ema_slow'].iloc[-1]
        ema200_now    = df['ema_trend'].iloc[-1]

        # ============================
        #   BUY Condition
        # ============================
        cond1 = (ema_fast_prev < ema_slow_prev) and (ema_fast_now >= ema_slow_now)
        cond2 = (close_price > ema200_now)

        if not position_open and cond1 and cond2:
            print("=== BUY SIGNAL ===")
            buy_order = place_limit_buy(close_price)
            entry_price = float(buy_order["price"])
            position_open = True
            print("Buy order placed at:", entry_price)

            # place TP
            tp_price = round(entry_price * 1.005, 2)
            place_limit_sell(tp_price)
            print("TP placed at:", tp_price)
            return

        # ============================
        #   Stop-loss by candle close
        # ============================
        if position_open:
            if close_price <= ema200_now:
                print("=== STOP LOSS ===")
                close_position_market()
                position_open = False
                entry_price = None
                return

# ============================
#   Start Stream
# ============================
def main():
    twm.start()
    twm.start_kline_socket(callback=handle_msg, symbol=SYMBOL, interval=tf)

    print("Running Futures EMA bot...")
    while True:
        time.sleep(1)

main()
