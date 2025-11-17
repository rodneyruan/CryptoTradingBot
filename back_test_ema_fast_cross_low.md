from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime

from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_3MINUTE     # <-- changed to 3m
profit_target = 0.005        # +0.5%
stop_loss_threshold = 0.01   # -1%
quantity = 0.01
default_limit = 1500
tz = pytz.timezone("America/Los_Angeles")

# EMA parameters
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200


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

    # -------------------------
    # Calculate EMA indicators
    # -------------------------
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["ema200"]   = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

    total_profit = 0.0
    successful_trades = 0
    total_trades = 0

    # Need enough candles for EMA200
    i = EMA_TREND + 3

    while i < len(df) - 1:

        close_price = df["close"].iloc[i]
        open_price  = df["open"].iloc[i]
        high_i      = df["high"].iloc[i]
        low_i       = df["low"].iloc[i]

        ts = df["timestamp"].iloc[i].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

        ema_fast_now = df["ema_fast"].iloc[i]
        ema_fast_prev = df["ema_fast"].iloc[i - 1]

        ema_slow_now = df["ema_slow"].iloc[i]
        ema_slow_prev = df["ema_slow"].iloc[i - 1]

        ema200_now = df["ema200"].iloc[i]

        # ------------------------------------------------------------
        # BUY CONDITIONS (ALL MUST MATCH)
        # ------------------------------------------------------------

        cond1 = (ema_fast_prev < ema_slow_prev) and (ema_fast_now >= ema_slow_now)   # EMA9 crosses above EMA21
        cond2 = (close_price > ema200_now)                                           # Above EMA200 trend
        cond3 = (close_price > open_price)                                           # Green candle

        buy_signal = cond1 and cond2 and cond3

        print(f"{ts} | Price={close_price:.2f} | EMA9={ema_fast_now:.2f} | EMA21={ema_slow_now:.2f} | EMA200={ema200_now:.2f}")

        if buy_signal:
            buy_price = close_price
            tp_price = buy_price * (1 + profit_target)
            sl_price = buy_price * (1 - stop_loss_threshold)

            total_trades += 1

            print(
                f"\nBUY SIGNAL 🔔 | {ts}\n"
                f"  EMA9 crossed above EMA21\n"
                f"  Price > EMA200\n"
                f"  Green candle (Open={open_price:.2f} → Close={close_price:.2f})\n"
                f"  Buy Price = {buy_price:.2f}, TP={tp_price:.2f}, SL={sl_price:.2f}\n"
            )

            trade_closed = False

            # simulate forward price movement
            for j in range(i + 1, len(df)):
                high_ = df["high"].iloc[j]
                low_ = df["low"].iloc[j]
                ts_j = df["timestamp"].iloc[j].tz_convert(tz).strftime("%Y-%m-%d %H:%M:%S")

                # Take Profit hit
                if high_ >= tp_price:
                    profit = (tp_price - buy_price) * quantity
                    total_profit += profit
                    successful_trades += 1

                    print(f"TP HIT ✔ | {ts_j} | Profit: {profit:.4f} USDC\n")
                    trade_closed = True
                    i = j
                    break

                # Stop Loss hit
                if low_ <= sl_price:
                    profit = (sl_price - buy_price) * quantity
                    total_profit += profit

                    print(f"STOP LOSS ❌ | {ts_j} | Loss: {profit:.4f} USDC\n")
                    trade_closed = True
                    i = j
                    break

            if not trade_closed:
                print("Trade still open at end of data.\n")
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
