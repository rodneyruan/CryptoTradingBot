from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime, timedelta
import time

from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_15MINUTE
quantity = 0.01
BUY_DISCOUNT = 0.9980
TP_MULTIPLIER = 1.0015
SL_MULTIPLIER = 0.99
ORDER_EXPIRATION = 10
RSI_PERIOD = 14
RSI_OVERSOLD = 30
tz = pytz.timezone("America/Los_Angeles")
MAX_LIMIT = 1000


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


def compute_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe, days_back=30)
    if df.empty:
        print("No data. Exiting.")
        return

    df['rsi'] = compute_rsi(df, RSI_PERIOD)
    total_profit = 0
    successful_trades = 0
    total_trades = 0
    i = RSI_PERIOD  # start after RSI warmup

    while i < len(df) - ORDER_EXPIRATION - 1:
        close_price = df["close"].iloc[i]
        ts = df["timestamp"].iloc[i].strftime("%Y-%m-%d %H:%M:%S")

        # RSI buy condition: cross below 30
        rsi_now = df['rsi'].iloc[i]
        rsi_prev = df['rsi'].iloc[i - 1]
        buy_signal = (rsi_prev > RSI_OVERSOLD) and (rsi_now <= RSI_OVERSOLD)

        if not buy_signal:
            i += 1
            continue

        # Place limit buy
        limit_buy_price = close_price * BUY_DISCOUNT
        tp_price = limit_buy_price * TP_MULTIPLIER
        sl_price = limit_buy_price * SL_MULTIPLIER

        print(
            f"\nRSI BUY SIGNAL 🔔 | {ts}\n"
            f"  Limit Buy @ {limit_buy_price:.2f}\n"
            f"  TP = {tp_price:.2f}\n"
            f"  SL = {sl_price:.2f}\n"
            f"  Order expires in {ORDER_EXPIRATION} candles"
        )

        # Step 1: check if limit buy is filled in next ORDER_EXPIRATION candles
        buy_filled = False
        expiration_index = i + ORDER_EXPIRATION
        for j in range(i + 1, min(expiration_index + 1, len(df))):
            low_ = df['low'].iloc[j]
            ts_j = df['timestamp'].iloc[j].strftime("%Y-%m-%d %H:%M:%S")
            if low_ <= limit_buy_price:
                buy_filled = True
                fill_index = j
                print(f"BUY FILLED ✔ | {ts_j} | Price: {limit_buy_price:.2f}")
                total_trades += 1
                break

        if not buy_filled:
            print("❌ Buy order expired. Restarting at new price.\n")
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
                print(f"TP HIT ✔ | {ts_k} | Profit: {profit:.4f} USDC\n")
                i = k
                trade_closed = True
                break

            if low2 <= sl_price:
                profit = (sl_price - limit_buy_price) * quantity
                total_profit += profit
                print(f"STOP LOSS ❌ | {ts_k} | Loss: {profit:.4f} USDC\n")
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
