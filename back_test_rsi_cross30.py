from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime

from key_config import apikey
from key_config import apisecret

# Binance Futures configuration
client = Client(apikey, apisecret, testnet=True)

# Trading parameters
symbol = 'BTCUSDC'
timeframe = Client.KLINE_INTERVAL_15MINUTE
ema_period = 7
profit_target = 0.003     # 0.3% profit
stop_loss_threshold = 0.01  # 1% stop loss
quantity = 0.01            # Trade size in BTC
default_limit = 50
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

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df['rsi']

def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe)
    if df.empty:
        print("No data fetched. Exiting.")
        return

    df['ema'] = calculate_ema(df, ema_period)
    df['rsi'] = calculate_rsi(df, 14)

    total_profit = 0.0
    successful_trades = 0
    total_trades = 0

    # Start after RSI, EMA available
    i = max(ema_period, 14) + 2

    while i < len(df) - 1:
        current_price = df['close'].iloc[i]
        candle_time = df['timestamp'].iloc[i].tz_convert(tz).strftime('%Y-%m-%d %H:%M:%S')

        rsi_prev = df['rsi'].iloc[i-1]
        rsi_now = df['rsi'].iloc[i]

        print(f"{datetime.now(tz)} | {candle_time}: Price {current_price}, RSI {rsi_now}")

        # ⭐ New Buy Condition: RSI rises above 30
        if rsi_prev < 30 and rsi_now >= 30:
            buy_price = current_price
            profit_price = buy_price * (1 + profit_target)
            stop_loss_price = buy_price * (1 - stop_loss_threshold)
            total_trades += 1

            print(f"{datetime.now(tz)} | BUY triggered: RSI crossed 30 at {rsi_now}, Price {buy_price}")

            trade_closed = False
            for j in range(i + 1, len(df)):
                high_price = df['high'].iloc[j]
                low_price = df['low'].iloc[j]

                # Profit target hit
                if high_price >= profit_price:
                    trade_profit = (profit_price - buy_price) * quantity
                    total_profit += trade_profit
                    successful_trades += 1
                    print(f"{datetime.now(tz)} | PROFIT: Buy {buy_price}, TP {profit_price}, Profit {trade_profit} USDC")
                    trade_closed = True
                    i = j
                    break

                # Stop loss hit
                if low_price <= stop_loss_price:
                    trade_profit = (stop_loss_price - buy_price) * quantity
                    total_profit += trade_profit
                    print(f"{datetime.now(tz)} | STOP LOSS: Buy {buy_price}, SL {stop_loss_price}, Loss {trade_profit} USDC")
                    trade_closed = True
                    i = j
                    break

            if not trade_closed:
                print(f"{datetime.now(tz)} | Trade not closed before end of data.")
                break

        i += 1

    success_ratio = successful_trades / total_trades if total_trades > 0 else 0
    print("\n========== Backtest Results ==========")
    print(f"Total Trades: {total_trades}")
    print(f"Successful Trades: {successful_trades}")
    print(f"Success Ratio: {success_ratio:.2%}")
    print(f"Total Profit: {total_profit:.4f} USDC")

if __name__ == "__main__":
    backtest()
