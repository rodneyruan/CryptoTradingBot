from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime

from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_3MINUTE
quantity = 0.01
default_limit = 1500
tz = pytz.timezone("America/Los_Angeles")

# Profit/SL logic
BUY_DISCOUNT = 0.9980      # Buy 0.20% below current close price
TP_MULTIPLIER = 1.0015     # +0.15%
SL_MULTIPLIER = 0.99       # -1%

# Order valid for 10 candles = 30 minutes
ORDER_EXPIRATION = 10


def fetch_historical_ohlcv(symbol, timeframe, limit=default_limit):
    """Fetch OHLCV from Binance Futures."""
    try:
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=timeframe,
            limit=limit
        )
        df = pd.DataFrame(klines, columns=[
            "timestamp","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_base_volume","taker_quote_volume","ignored"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms").dt.tz_localize("UTC")
        df[["open","high","low","close"]] = df[["open","high","low","close"]].astype(float)
        return df
    except Exception as e:
        print(f"{datetime.now(tz)} | Error fetching klines: {e}")
        return pd.DataFrame()


def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe)
    if df.empty:
        print("No data. Exiting.")
        return

    total_profit = 0
    successful_trades = 0
    total_trades = 0

    i = 3  # buffer

    while i < len(df) - ORDER_EXPIRATION - 1:

        # Start by placing a fresh limit buy
        attempt_index = i
        close_price = df["close"].iloc[attempt_index]
        ts = df["timestamp"].iloc[attempt_index].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

        limit_buy_price = close_price * BUY_DISCOUNT
        tp_price = limit_buy_price * TP_MULTIPLIER
        sl_price = limit_buy_price * SL_MULTIPLIER

        print(
            f"\nPLACED LIMIT BUY 🔔 | {ts}\n"
            f"  Limit Buy @ {limit_buy_price:.2f} (-0.20%)\n"
            f"  Expires in 10 candles\n"
            f"  TP = {tp_price:.2f} (+0.15%)\n"
            f"  SL = {sl_price:.2f} (-1%)\n"
        )

        buy_filled = False

        # ------------------------------------------------------------
        # Check the next 10 candles for a buy fill
        # ------------------------------------------------------------
        expiration_index = attempt_index + ORDER_EXPIRATION

        for j in range(attempt_index + 1, expiration_index + 1):

            low_ = df["low"].iloc[j]
            ts_j = df["timestamp"].iloc[j].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

            if low_ <= limit_buy_price:
                buy_filled = True
                fill_index = j

                print(f"BUY FILLED ✔ | {ts_j} | Fill Price: {limit_buy_price:.2f}")
                total_trades += 1
                break

        # ------------------------------------------------------------
        # If NOT filled after 10 candles → expire the order
        # ------------------------------------------------------------
        if not buy_filled:
            print("❌ Buy order EXPIRED after 10 candles. Placing a NEW limit buy at new price.\n")
            i = expiration_index   # Move pointer to expiration candle
            continue  # Restart with a new buy order at the expiration candle price

        # ------------------------------------------------------------
        # Step 2: After fill, check TP or SL
        # ------------------------------------------------------------
        trade_closed = False

        for k in range(fill_index + 1, len(df)):
            high2 = df["high"].iloc[k]
            low2  = df["low"].iloc[k]
            ts_k  = df["timestamp"].iloc[k].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

            # TP FIRST
            if high2 >= tp_price:
                profit = (tp_price - limit_buy_price) * quantity
                total_profit += profit
                successful_trades += 1

                print(f"TP HIT ✔ | {ts_k} | Profit: {profit:.4f} USDC\n")
                i = k
                trade_closed = True
                break

            # SL SECOND
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

    # --------------------
    # Summary
    # --------------------
    win_rate = (successful_trades / total_trades) if total_trades else 0

    print("\n========= BACKTEST RESULTS =========")
    print(f"Total Trades:       {total_trades}")
    print(f"Successful Trades:  {successful_trades}")
    print(f"Win Rate:           {win_rate:.2%}")
    print(f"Total Profit:       {total_profit:.4f} USDC")
    print("====================================")


if __name__ == "__main__":
    backtest()
