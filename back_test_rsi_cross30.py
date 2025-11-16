from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime

from ta.momentum import RSIIndicator
from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_5MINUTE
rsi_period = 14
profit_target = 0.005        # +0.5%
stop_loss_threshold = 0.01   # -1%
quantity = 0.01
default_limit = 1500
tz = pytz.timezone("America/Los_Angeles")


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

    # RSI calculation
    df["rsi"] = RSIIndicator(close=df["close"], window=rsi_period).rsi()

    total_profit = 0.0
    successful_trades = 0
    total_trades = 0

    i = rsi_period + 3  # Need at least 3 prior RSI values

    while i < len(df) - 1:

        close_price = df["close"].iloc[i]
        open_price = df["open"].iloc[i]
        ts = df["timestamp"].iloc[i].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

        rsi_now = df["rsi"].iloc[i]
        rsi_prev1 = df["rsi"].iloc[i - 1]
        rsi_prev2 = df["rsi"].iloc[i - 2]

        print(f"{ts} | Price={close_price:.2f}, RSI={rsi_now:.2f}")

        # ------------------------------------------------------------
        # BUY CONDITIONS (ALL MUST MATCH)
        # ------------------------------------------------------------
        cond1 = (rsi_now < 35)
        cond2 = (close_price > open_price)              # Green candle
        cond3 = (rsi_prev2 < 30 and rsi_prev1 < 30)     # RSI deeply oversold for 2 candles
        cond4 = (rsi_prev1 < 30 and rsi_now >= 30)      # RSI crosses above 30 now

        buy_signal = cond1 and cond2 and cond3 and cond4

        if buy_signal:
            buy_price = close_price
            tp_price = buy_price * (1 + profit_target)
            sl_price = buy_price * (1 - stop_loss_threshold)

            total_trades += 1

            print(
                f"BUY SIGNAL 🔔 | {ts}\n"
                f"  RSI_prev2={rsi_prev2:.2f}, RSI_prev1={rsi_prev1:.2f}, RSI_now={rsi_now:.2f}\n"
                f"  Green candle: {open_price:.2f} → {close_price:.2f}\n"
                f"  Buy Price = {buy_price:.2f}, TP={tp_price:.2f}, SL={sl_price:.2f}"
            )

            trade_closed = False

            # simulate forward price movement
            for j in range(i + 1, len(df)):
                high_ = df["high"].iloc[j]
                low_ = df["low"].iloc[j]
                ts_j = df["timestamp"].iloc[j].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

                # Check Take Profit
                if high_ >= tp_price:
                    profit = (tp_price - buy_price) * quantity
                    total_profit += profit
                    successful_trades += 1

                    print(f"TP HIT ✔ | {ts_j} | Profit: {profit:.4f} USDC")
                    trade_closed = True
                    i = j
                    break

                # Check Stop Loss
                if low_ <= sl_price:
                    profit = (sl_price - buy_price) * quantity
                    total_profit += profit

                    print(f"STOP LOSS ❌ | {ts_j} | Loss: {profit:.4f} USDC")
                    trade_closed = True
                    i = j
                    break

            if not trade_closed:
                print("Trade still open at end of data.")
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
