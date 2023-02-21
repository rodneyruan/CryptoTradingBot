#!/home/ruanrongbin/.local/bin/python3
import time
from binance.client import Client
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt   # needs pip install
import ta
from datetime import datetime, timezone, timedelta

import key_config

fd=open("log.txt", "a")


client = Client(key_config.API_KEY, key_config.SECURITY_KEY)

total_profit = 0

symbol = 'BTCBUSD'

interval = '5m'
quantity_to_buy =  0.006
df_initialized = False
histrical_kline_list = client.get_historical_klines(symbol, interval, limit=100)
histrical_kline_list_close = [ histrical_kline_list_line[4] for histrical_kline_list_line in histrical_kline_list]


def execute_buy_and_take_profit_or_stoploss():
    global total_profit
    retry_counter = 0
    while True:
        try:
            btc_price = client.get_symbol_ticker(symbol="BTCBUSD")
            print("The current BTC price is ", float(btc_price['price']))
            price_to_buy= round(float(btc_price['price']) - 10)
            print("price_to_buy and quantity_to_buy: ",price_to_buy," ",quantity_to_buy)
            order_id_buy_limit = client.order_limit_buy(symbol='BTCBUSD', quantity=quantity_to_buy, price=price_to_buy)
            print("Placed a buy order, at  ",price_to_buy, ", order id is", order_id_buy_limit['orderId'])
            fd.write(f"Placed a buy order, at {price_to_buy} \n")
            buy_order_id = order_id_buy_limit['orderId']
            break
        except:
            retry_counter+=1
            if(retry_counter>=5):
                break
            print("execute_buy_and_take_profit_or_stoploss: except occured while running get_symbol_ticker and placing buy order.");
            time.sleep(60)

    time.sleep(60)

    # 300 seconds count down for the buy order
    time_out_buy_order = 320
    while (True):
        time.sleep(80)
        try:
            order = client.get_order(symbol='BTCBUSD',orderId=buy_order_id)
            if (order['status'] == 'FILLED'):
                print("BUY order is filled")
                fd.write("Good, BUY order is filled ")
                break
            time_out_buy_order -= 80

        except:
            print("execute_buy_and_take_profit_or_stoploss: except occured while getting buy order status")

        if(time_out_buy_order <=0 ):
            client.cancel_order(symbol='BTCBUSD', orderId=buy_order_id)
            print("Buy-Order Timed Out, waiting for next buy-signal.")
            fd.write("Buy-Order Timed Out, waiting for next buy-signal.")
            return
    time.sleep(10)
    price_to_sell = price_to_buy + 100
    quantity_to_sell = quantity_to_buy

    retry_counter = 0
    while (True):
        retry_counter +=1
        if(retry_counter >5):
            break

        try:
            order_id_sell_limit = client.order_limit_sell(symbol='BTCBUSD', quantity=quantity_to_sell, price=price_to_sell)
            sell_order_id = order_id_sell_limit ['orderId']
            print("Placed a sell order at ", price_to_sell)
            fd.write(f"Placed a sell order at { price_to_sell} .\n")
            break
        except:
            print("execute_buy_and_take_profit_or_stoploss: except occured while placing sell order")
            time.sleep(60)

    while (True):
        time.sleep(120)
        try:
            order = client.get_order(symbol='BTCBUSD', orderId=sell_order_id)
            if (order['status'] == 'FILLED'):

                print("Sell Order executed, you have earned (???) USDT: ", quantity_to_sell*100 )
                fd.write(f"Sell Order executed, you have earned (???) USDT:  {quantity_to_sell*100} \n")
                total_profit += quantity_to_sell*100
                print("Total profit???: ",  total_profit)
                fd.write(f"Total profit???:{total_profit}\n"  )
                break
        except:
            print("execute_buy_and_take_profit_or_stoploss: except occured while check sell order status")
        try:
            btc_price = client.get_symbol_ticker(symbol="BTCBUSD")
            if (float(btc_price['price']) <= (price_to_buy -400)):
                price_to_sell = round(float(btc_price['price']) )
                print("Stop Loss Triggered at ", btc_price['price'], "?? You lost ",quantity_to_sell* 400)
                total_profit -= quantity_to_sell*(price_to_buy -price_to_sell)
                print("Total profit: ",  total_profit)
                fd.write(f"Stop Loss Triggered at {btc_price['price'] } ????,  ???? You lost {quantity_to_sell* 400}\n")
                fd.write(f"Total profit???:{total_profit}\n"  )
                client.cancel_order(symbol='BTCBUSD', orderId=sell_order_id)
                order_id_sell_limit = client.order_limit_sell(symbol='BTCBUSD',quantity = quantity_to_sell, price = price_to_sell)
                break
        except:
             print("execute_buy_and_take_profit_or_stoploss: except occured while doing stop loss")

def get_data_frame():
    # valid intervals - 1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M
    # request historical candle (or klines) data using timestamp from above, interval either every min, hr, day or month
    # starttime = '30 minutes ago UTC' for last 30 mins time
    # e.g. client.get_historical_klines(symbol='ETHUSDTUSDT', '1m', starttime)
    # starttime = '1 Dec, 2017', '1 Jan, 2018'  for last month of 2017
    # e.g. client.get_historical_klines(symbol='BTCUSDT', '1h', "1 Dec, 2017", "1 Jan, 2018")
    global df_initialized
    global histrical_kline_list_close
    if(df_initialized == False):
        df = pd.DataFrame(histrical_kline_list_close, columns=[ 'close'])
        df_initialized = True
        return df
    while True:
        try:
            bars = client.get_historical_klines(symbol, interval, limit=2)
            break
        except:
            print("get_data_frame: Except occured while running client.get_historical_klines. ")
            time.sleep(60)
    #print(bars)
    close_price = [ line[4] for line in bars]

    del histrical_kline_list_close[0]
    histrical_kline_list_close[-1] = close_price[0]
    histrical_kline_list_close.append(close_price[1])
    df = pd.DataFrame(histrical_kline_list_close, columns=[ 'close'])
    return df


def macd_trade_logic():

    """
    symbol_df = get_data_frame()
    # calculate short and long EMA mostly using close values
    shortEMA = symbol_df['close'].ewm(span=12, adjust=False).mean()
    longEMA = symbol_df['close'].ewm(span=26, adjust=False).mean()

    # Calculate MACD and signal line
    MACD = shortEMA - longEMA
    signal = MACD.ewm(span=9, adjust=False).mean()
    symbol_df['MACD'] = MACD
    symbol_df['signal'] = signal


    symbol_df['Trigger'] = np.where(symbol_df['MACD'] > symbol_df['signal'], 1, 0)
    symbol_df['Position'] = symbol_df['Trigger'].diff()

    # Add buy and sell columns
    symbol_df['Buy'] = np.where(symbol_df['Position'] == 1,symbol_df['close'], np.NaN )
    symbol_df['Sell'] = np.where(symbol_df['Position'] == -1,symbol_df['close'], np.NaN )

    # To print in human-readable date and time (from timestamp)
    symbol_df.set_index('date', inplace=True)
    #symbol_df.index = pd.to_datetime(symbol_df.index, unit='ms')

    #with open('output.txt', 'w') as f:
    #    f.write(symbol_df.to_string())
    print(symbol_df)
    print(list(symbol_df['Trigger'])[-1])
    if( ( (list(symbol_df['Trigger'])[-2]) == 1)  and ( (list(symbol_df['Trigger'])[-3]) == 0) and ( (list(symbol_df['Trigger'])[-4]) == 0) ):
        print(" Buy signal triggered.")
        execute_buy_and_take_profit_or_stoploss()
    """
    # This fucntion is used to get the value of MACD, while  MACD = DIF- DEM
    # wait until 4:40, 9:40, 14:40, 19:40 to get data .....
    tz = timezone(timedelta(hours=+8))
    now  = datetime.now(tz)
    seconds_to_wait = (580 - ( ( 60* now.minute  +now.second) % 300) ) %300
    #print("current second %300 is", ( ( 60* now.minute +now.second) % 300), " next is ",(580 - ( ( 60* now.minute% +now.second) % 300) ))

    #print(now, "seconds to wait is ", seconds_to_wait)
    time.sleep(seconds_to_wait)
    symbol_df = get_data_frame()
    #print(symbol_df.head())
    #print(symbol_df.tail())
    """macd=ta.trend.macd(symbol_df.close)
    macd_signal=ta.trend.macd_signal(symbol_df.close)
    macd_diff=ta.trend.macd_diff(symbol_df.close)
    tz = timezone(timedelta(hours=+8))
    print(datetime.now(tz),  ":  The last 3 MACD values are:  " , macd.iloc[-3], "  ", macd.iloc[-2],"  " , macd.iloc[-1] )
    if ( (macd_signal.iloc[-1] < 0 ) and (macd_signal.iloc[-2] < 0) and (macd_signal.iloc[-3] < 0)  and (macd_signal.iloc[-4] < 0 ) and
       ( macd.iloc[-1] < 0 )  and ( macd.iloc[-2] < 0 ) and  ( macd.iloc[-3] < 0)  and  (macd.iloc[-4] < 0) and
       (macd_diff.iloc[-1] > macd_diff.iloc[-2])  and (macd_diff.iloc[-2] > macd_diff.iloc[-3])  and
       (macd_diff.iloc[-3] > 0) and ( macd_diff.iloc[-4]< 0 ) ) :
        print(datetime.now(tz)," Buy signal triggered.")
        execute_buy_and_take_profit_or_stoploss()
"""

    rsi= ta.momentum.RSIIndicator(pd.to_numeric(symbol_df.close), window=6).rsi()
    #print(rsi)
    print(datetime.now(tz),  ":  The last 4 RSI values are:  " , rsi.iloc[-4], " ",rsi.iloc[-3], "  ", rsi.iloc[-2],"  " , rsi.iloc[-1] )
    fd.write(f'{datetime.now(tz)} The last 4 RSI values are:  {rsi.iloc[-4]}  {rsi.iloc[-3]}  {rsi.iloc[-2]}  {rsi.iloc[-1]} \n')
    if( ( rsi.iloc[-1] >=30 ) and ( rsi.iloc[-2] <30 ) and ( rsi.iloc[-3] < 30)  and ( rsi.iloc[-4] <30 )
        and ( (rsi.iloc[-2] < 20) or (rsi.iloc[-3] < 20) or (rsi.iloc[-4] < 20) or (rsi.iloc[-5] < 20))):
        print(datetime.now(tz)," RSI crossed above 30, Buy signal triggered.")
        fd.write(f'{datetime.now(tz)} RSI crossed above 30, Buy signal triggered.\n')
        execute_buy_and_take_profit_or_stoploss()
    time.sleep(250)
    #plot_graph(symbol_df)



while True:
    macd_trade_logic()





