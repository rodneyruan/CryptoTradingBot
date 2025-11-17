from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime, timedelta
import time

from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_15MINUTE   # 15-min candles
quantity = 0.01
BUY_DISCOUNT = 0.9980      # Buy 0.20% below current close price
TP_MULTIPLIER = 1.0015     # +0.15%
SL_MULTIPLIER = 0.99       # -1%
ORDER_EXPIRATION = 10      # 10 candles (150 min)
tz = pytz.timezone("America/Los_Angeles")
MAX_LIMIT = 1500           # Binance max candles per request


def fetch_historical_ohlcv(symbol, timeframe, days_back=30):
    """Fetch OHLCV for the past X days, handling pagination due to Binance limit."""
    all_klines = []
    end_time = int(time.time() * 1000)  # now in ms
    start_time = end_time - days_back * 24 * 60 * 60 * 1000  # days_back in ms

    while True:
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
        # Move start_time forward to fetch next batch, add 1 ms to avoid overlap
        start_time = last_open_time + 1

        # Stop if fetched fewer than MAX_LIMIT candles (end reached)
        if len(klines) < MAX_LIMIT:
            break

        # Sleep to avoid rate limits
        time.sleep(0.2)

    df = pd.DataFrame(all_klines, columns=[
        "timestamp","open","high","low","close","volume",
        "close_time","quote_volume","trades",
        "taker_base_volume","taker_quote_volume","ignored"
    ])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms").dt.tz_localize("UTC").dt.tz_convert(tz)
    df[["open","high","low","close"]] = df[["open","high","low","close"]].astype(float)
    return df


def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe, days_back=30)
    if df.empty:
        print("No data. Exiting.")
        return

    total_profit = 0
    successful_trades = 0
    total_trades = 0

    i = 3  # buffer

    while i < len(df) - ORDER_EXPIRATION - 1:

        attempt_index = i
        close_price = df["close"].iloc[attempt_index]
        ts = df["timestamp"].iloc[attempt_index].strftime("%Y-%m-%d %H:%M:%S")

        limit_buy_price = close_price * BUY_DISCOUNT
        tp_price = limit_buy_price * TP_MULTIPLIER
        sl_price = limit_buy_price * SL_MULTIPLIER

        print(
            f"\nPLACED LIMIT BUY 🔔 | {ts}\n"
            f"  Limit Buy @ {limit_buy_price:.2f} (-0.20%)\n"
            f"  Expires in {ORDER_EXPIRATION} candles\n"
            f"  TP = {tp_price:.2f} (+0.15%)\n"
            f"  SL = {sl_price:.2f} (-1%)\n"
        )

        # Step 1: Check if LIMIT BUY gets filled in next ORDER_EXPIRATION candles
        buy_filled = False
        expiration_index = attempt_index + ORDER_EXPIRATION

        for j in range(attempt_index + 1, min(expiration_index + 1, len(df))):
            low_ = df["low"].iloc[j]
            ts_j = df["timestamp"].iloc[j].strftime("%Y-%m-%d %H:%M:%S")

            if low_ <= limit_buy_price:
                buy_filled = True
                fill_index = j
                print(f"BUY FILLED ✔ | {ts_j} | Fill Price: {limit_buy_price:.2f}")
                total_trades += 1
                break

        # If not filled, move to next expiration candle
        if not buy_filled:
            print("❌ Buy order EXPIRED after 10 candles. Restarting with new price.\n")
            i = expiration_index
            continue

        # Step 2: After fill, check TP or SL
        trade_closed = False
        for k in range(fill_index + 1, len(df)):
            high2 = df["high"].iloc[k]
            low2  = df["low"].iloc[k]
            ts_k  = df["timestamp"].iloc[k].strftime("%Y-%m-%d %H:%M:%S")

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

    # Summary with start and end timestamps
    start_time = df["timestamp"].iloc[0].strftime("%Y-%m-%d %H:%M:%S")
    end_time   = df["timestamp"].iloc[-1].strftime("%Y-%m-%d %H:%M:%S")
    win_rate = (successful_trades / total_trades) if total_trades else 0

    print("\n========= BACKTEST RESULTS =========")
    print(f"Start Time:        {start_time}")
    print(f"End Time:          {end_time}")
    print(f"Total Trades:      {total_trades}")
    print(f"Successful Trades: {successful_trades}")
    print(f"Win Rate:          {win_rate:.2%}")
    print(f"Total Profit:      {total_profit:.4f} USDC")
    print("====================================")


if __name__ == "__main__":
    backtest()
