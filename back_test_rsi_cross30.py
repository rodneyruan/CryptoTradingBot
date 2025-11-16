from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime

from ta.momentum import RSIIndicator  # <-- using ta library

from key_config import apikey
from key_config import apisecret

# Binance Futures configuration
client = Client(apikey, apisecret, testnet=True)

# Trading parameters
symbol = 'BTCFDUSD'
timeframe = Client.KLINE_INTERVAL_1MINUTE
ema_period = 7
rsi_period = 6
profit_target = 0.002         # 0.3%
stop_loss_threshold = 0.006   # 1%
quantity = 0.01
default_limit = 500
tz = pytz.timezone('America/Los_Angeles')


def fetch_historical_ohlcv(symbol, timeframe, limit=default_limit):
    """Fetch OHLCV from Binance Futures."""
    try:
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=timeframe,
            start_str=None,
            end_str=None,
            limit=limit
        )
        df = pd.DataFrame(klines, columns=[
            'timestamp','open','high','low','close','volume',
            'close_time','quote_volume','trades',
            'taker_base_volume','taker_quote_volume','ignored'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC')
        df[['open','high','low','close']] = df[['open','high','low','close']].astype(float)
        return df
    except Exception as e:
        print(f"{datetime.now(tz)} | Error fetching klines: {e}")
        return pd.DataFrame()


def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()


def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe)
    if df.empty:
        print("No data. Exiting.")
        return

    # EMA
    df['ema'] = calculate_ema(df, ema_period)

    # ⭐ RSI using ta library
    df["rsi"] = RSIIndicator(close=df["close"], window=rsi_period).rsi()

    total_profit = 0.0
    successful_trades = 0
    total_trades = 0

    # Starting point after indicators valid
    i = max(ema_period, rsi_period) + 2

    while i < len(df) - 1:
        current_price = df['close'].iloc[i]
        candle_time = df['timestamp'].iloc[i].tz_convert(tz).strftime('%Y-%m-%d %H:%M:%S')

        rsi_prev = df["rsi"].iloc[i - 1]
        rsi_now = df["rsi"].iloc[i]

        print(f"{datetime.now(tz)} | {candle_time}: Price {current_price}, RSI {rsi_now}")

        # ⭐ Buy when RSI crosses above 30
        if rsi_prev < 30 and rsi_now >= 30:
            buy_price = current_price
            profit_price = buy_price * (1 + profit_target)
            stop_loss_price = buy_price * (1 - stop_loss_threshold)
            total_trades += 1

            print(f"{datetime.now(tz)} | BUY: RSI crossed above 30 → {rsi_prev:.2f} → {rsi_now:.2f}, Price {buy_price}")

            trade_closed = False

            # Simulate trade outcome
            for j in range(i + 1, len(df)):
                high_price = df['high'].iloc[j]
                low_price = df['low'].iloc[j]

                # Take profit first
                if high_price >= profit_price:
                    trade_profit = (profit_price - buy_price) * quantity
                    total_profit += trade_profit
                    successful_trades += 1
                    print(f"{datetime.now(tz)} | PROFIT: Buy {buy_price}, TP {profit_price}, Profit {trade_profit} USDC")
                    trade_closed = True
                    i = j
                    break

                # Stop loss
                if low_price <= stop_loss_price:
                    trade_profit = (stop_loss_price - buy_price) * quantity
                    total_profit += trade_profit
                    print(f"{datetime.now(tz)} | STOP LOSS: Buy {buy_price}, SL {stop_loss_price}, Loss {trade_profit} USDC")
                    trade_closed = True
                    i = j
                    break

            if not trade_closed:
                print(f"{datetime.now(tz)} | Trade still open at end of data.")
                break

        i += 1

    # Summary
    success_ratio = successful_trades / total_trades if total_trades else 0
    print("\n===== Backtest Results =====")
    print(f"Total Trades: {total_trades}")
    print(f"Successful Trades: {successful_trades}")
    print(f"Success Rate: {success_ratio:.2%}")
    print(f"Total Profit: {total_profit:.4f} USDC")


if __name__ == "__main__":
    backtest()
