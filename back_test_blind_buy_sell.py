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
BUY_DISCOUNT = 0.9985    # Buy 0.15% below price
TP_MULTIPLIER = 1.0015   # Sell 0.15% above buy price
SL_MULTIPLIER = 0.99     # Stop loss -1%


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

    total_profit = 0.0
    successful_trades = 0
    total_trades = 0

    i = 3  # small buffer for safety

    while i < len(df) - 1:

        close_price = df["close"].iloc[i]
        open_price  = df["open"].iloc[i]
        ts = df["timestamp"].iloc[i].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

        print(f"{ts} | Close={close_price:.2f}")

        # ------------------------------------------------------------
        # BUY CONDITION (ONLY ONE NOW)
        # ------------------------------------------------------------
        buy_signal = (close_price > open_price)   # green candle

        if buy_signal:

            buy_price = close_price * BUY_DISCOUNT
            tp_price  = buy_price * TP_MULTIPLIER
            sl_price  = buy_price * SL_MULTIPLIER

            total_trades += 1

            print(
                f"\nBUY SIGNAL 🔔 | {ts}\n"
                f"  Limit Buy @ {buy_price:.2f} (-0.15%)\n"
                f"  TP = {tp_price:.2f} (+0.15%)\n"
                f"  SL = {sl_price:.2f} (-1%)\n"
            )

            trade_closed = False

            # simulate future candles
            for j in range(i + 1, len(df)):

                high_ = df["high"].iloc[j]
                low_  = df["low"].iloc[j]

                ts_j = df["timestamp"].iloc[j].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

                # Take profit hit
                if high_ >= tp_price:
                    profit = (tp_price - buy_price) * quantity
                    total_profit += profit
                    successful_trades += 1

                    print(f"TP HIT ✔ | {ts_j} | Profit: {profit:.4f} USDC\n")
                    i = j
                    trade_closed = True
                    break

                # Stop loss hit
                if low_ <= sl_price:
                    profit = (sl_price - buy_price) * quantity
                    total_profit += profit

                    print(f"STOP LOSS ❌ | {ts_j} | Loss: {profit:.4f} USDC\n")
                    i = j
                    trade_closed = True
                    break

            if not trade_closed:
                print("Trade left open. End of data.\n")
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
