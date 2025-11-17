from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime, timedelta
import time

from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_5MINUTE
quantity = 0.01
BUY_DISCOUNT = 0.9980
TP_MULTIPLIER = 1.0015
SL_MULTIPLIER = 0.99
ORDER_EXPIRATION = 10
tz = pytz.timezone("America/Los_Angeles")
MAX_LIMIT = 1000

EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
EMA_TREND_PERIOD = 200

DAYS_BACK=30

def fetch_historical_ohlcv(symbol, timeframe, days_back=30):
    all_klines = []
    end_time = int(time.time() * 1000)
    start_time = end_time - days_back * 24 * 60 * 60 * 1000

    while start_time < end_time:
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=timeframe,
            start_str=start_time,
            end_str=end_time,
            limit=MAX_LIMIT
        )

        if not klines:
            break

        all_klines += klines
        last_open_time = klines[-1][0]
        start_time = last_open_time + 1
        if len(klines) < MAX_LIMIT:
            break
        time.sleep(0.2)

    df = pd.DataFrame(all_klines, columns=[
        "timestamp","open","high","low","close","volume",
        "close_time","quote_volume","trades",
        "taker_base_volume","taker_quote_volume","ignored"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms").dt.tz_localize("UTC").dt.tz_convert(tz)
    df[["open","high","low","close"]] = df[["open","high","low","close"]].astype(float)
    return df


def compute_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()


def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe, days_back=DAYS_BACK)
    if df.empty:
        print("No data. Exiting.")
        return

    # Compute EMAs
    df['ema_fast'] = compute_ema(df, EMA_FAST_PERIOD)
    df['ema_slow'] = compute_ema(df, EMA_SLOW_PERIOD)
    df['ema_trend'] = compute_ema(df, EMA_TREND_PERIOD)

    total_profit = 0
    successful_trades = 0
    total_trades = 0

    i = EMA_TREND_PERIOD  # start after EMA200 warmup

    while i < len(df) - ORDER_EXPIRATION - 1:
        close_price = df["close"].iloc[i]
        open_price = df["open"].iloc[i]
        ts = df["timestamp"].iloc[i].strftime("%Y-%m-%d %H:%M:%S")

        # EMA crossover buy condition
        ema_fast_now = df['ema_fast'].iloc[i]
        ema_slow_now = df['ema_slow'].iloc[i]
        ema_fast_prev = df['ema_fast'].iloc[i - 1]
        ema_slow_prev = df['ema_slow'].iloc[i - 1]
        ema_trend_now = df['ema_trend'].iloc[i]

        cond1 = (ema_fast_prev < ema_slow_prev) and (ema_fast_now >= ema_slow_now)
        cond2 = (close_price > ema_trend_now)
        cond3 = (close_price > open_price)

        buy_signal = cond1 and cond2

        if not buy_signal:
            i += 1
            continue

        # Place limit buy
        limit_buy_price = close_price * BUY_DISCOUNT
        tp_price = limit_buy_price * TP_MULTIPLIER
        sl_price = limit_buy_price * SL_MULTIPLIER

        print(
            f"\nEMA BUY SIGNAL ?? | {ts}\n"
            f"  Limit Buy @ {limit_buy_price:.2f}\n"
            f"  TP = {tp_price:.2f}\n"
            f"  SL = {sl_price:.2f}\n"
            f"  Order expires in {ORDER_EXPIRATION} candles"
        )

        # Step 1: check next ORDER_EXPIRATION candles for buy fill
        buy_filled = False
        expiration_index = i + ORDER_EXPIRATION
        for j in range(i + 1, min(expiration_index + 1, len(df))):
            low_ = df['low'].iloc[j]
            ts_j = df['timestamp'].iloc[j].strftime("%Y-%m-%d %H:%M:%S")
            if low_ <= limit_buy_price:
                buy_filled = True
                fill_index = j
                print(f"BUY FILLED ? | {ts_j} | Price: {limit_buy_price:.2f}")
                total_trades += 1
                break

        if not buy_filled:
            print("? Buy order expired. Restarting at new price.\n")
            i = expiration_index
            continue

        # Step 2: check TP/SL after fill
        trade_closed = False
        for k in range(fill_index + 1, len(df)):
            high2 = df['high'].iloc[k]
            low2 = df['low'].iloc[k]
            ts_k = df['timestamp'].iloc[k].strftime("%Y-%m-%d %H:%M:%S")

            if high2 >= tp_price:
                profit = (tp_price - limit_buy_price) * quantity
                total_profit += profit
                successful_trades += 1
                print(f"TP HIT ? | {ts_k} | Profit: {profit:.4f} USDC\n")
                i = k
                trade_closed = True
                break

            if low2 <= sl_price:
                profit = (sl_price - limit_buy_price) * quantity
                total_profit += profit
                print(f"STOP LOSS ? | {ts_k} | Loss: {profit:.4f} USDC\n")
                i = k
                trade_closed = True
                break

        if not trade_closed:
            print("Trade open at end of data. Stopping.\n")
            break

        i += 1

    # Summary
    start_time = df["timestamp"].iloc[0].strftime("%Y-%m-%d %H:%M:%S")
    end_time = df["timestamp"].iloc[-1].strftime("%Y-%m-%d %H:%M:%S")
    start_price = df["close"].iloc[0]
    end_price = df["close"].iloc[-1]
    win_rate = (successful_trades / total_trades) if total_trades else 0

    print("\n========= BACKTEST RESULTS =========")
    print(f"Start Time:        {start_time} | Price: {start_price:.2f}")
    print(f"End Time:          {end_time} | Price: {end_price:.2f}")
    print(f"Total Trades:      {total_trades}")
    print(f"Successful Trades: {successful_trades}")
    print(f"Win Rate:          {win_rate:.2%}")
    print(f"Total Profit:      {total_profit:.4f} USDC")
    print("====================================")


if __name__ == "__main__":
    backtest()
