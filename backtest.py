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
buy_threshold = 0.003  # Price 0.3% below EMA
profit_target = 0.003  # 0.3% profit
stop_loss_threshold = 0.01  # 1% stop loss
quantity = 0.01  # Trade size in BTC
default_limit = 50  # Default limit for get_historical_klines
tz = pytz.timezone('Asia/Singapore')  # UTC+8 timezone

def fetch_historical_ohlcv(symbol, timeframe, limit=default_limit):
    """Fetch the most recent historical OHLCV data from Binance Futures."""
    try:
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=timeframe,
            start_str=None,
            end_str=None,
            limit=limit
        )
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'trades', 'taker_base_volume', 'taker_quote_volume', 'ignored'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC')
        df['open'] = df['open'].astype(float)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        return df
    except Exception as e:
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Error fetching klines: {e}")
        return pd.DataFrame()

def calculate_ema(df, period):
    """Calculate EMA for the given period."""
    return df['close'].ewm(span=period, adjust=False).mean()

def is_negative_candle(row):
    """Check if a candle is negative (close < open)."""
    return row['close'] < row['open']

def backtest():
    """Backtest the EMA strategy with profit target and stop loss."""
    # Fetch the most recent 50 candles
    df = fetch_historical_ohlcv(symbol, timeframe)
    
    if df.empty:
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | No data fetched. Exiting backtest.")
        return
    
    # Calculate EMA
    df['ema'] = calculate_ema(df, ema_period)
    
    total_profit = 0.0
    successful_trades = 0
    total_trades = 0
    
    # Start after enough data for EMA and 2 previous candles
    i = ema_period + 2
    while i < len(df) - 1:
        current_price = df['close'].iloc[i]
        latest_ema = df['ema'].iloc[i]
        candle_time = df['timestamp'].iloc[i].tz_convert(tz).strftime('%Y-%m-%d %H:%M:%S')
        
        # Print price and EMA check
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | {candle_time}: current_price: {current_price}, latest_ema: {latest_ema}")

        # Check for three consecutive negative candles (including current)
        negative_candles = all(is_negative_candle(df.iloc[j]) for j in range(i-2, i+1))

        # Check buy condition: 3 negative candles and price 0.3% below EMA
        if negative_candles and current_price < latest_ema * (1 - buy_threshold):
            buy_price = current_price
            profit_price = buy_price * (1 + profit_target)  # 0.3% profit
            stop_loss_price = buy_price * (1 - stop_loss_threshold)  # 1% stop loss
            total_trades += 1
            
            # Simulate trade outcome by checking subsequent candles
            trade_closed = False
            for j in range(i + 1, len(df)):
                high_price = df['high'].iloc[j]
                low_price = df['low'].iloc[j]
                
                # Check if profit target is hit first
                if high_price >= profit_price:
                    trade_profit = (profit_price - buy_price) * quantity
                    total_profit += trade_profit
                    successful_trades += 1
                    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Trade at {candle_time}: Buy at {buy_price}, Take Profit at {profit_price}, Profit: {trade_profit} USDC")
                    trade_closed = True
                    i = j  # Move to the candle after the trade closes
                    break
                
                # Check if stop loss is hit first
                if low_price <= stop_loss_price:
                    trade_profit = (stop_loss_price - buy_price) * quantity
                    total_profit += trade_profit
                    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Trade at {candle_time}: Buy at {buy_price}, Stop Loss at {stop_loss_price}, Loss: {trade_profit} USDC")
                    trade_closed = True
                    i = j  # Move to the candle after the trade closes
                    break
            
            # If trade not closed (end of data), assume it remains open and skip
            if not trade_closed:
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Trade at {candle_time}: Buy at {buy_price}, Not closed by end of data")
                break
        
        i += 1
    
    # Calculate success ratio
    success_ratio = successful_trades / total_trades if total_trades > 0 else 0
    print(f"\n{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Backtest Results:")
    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Total Trades: {total_trades}")
    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Successful Trades (Take Profit): {successful_trades}")
    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Success Ratio: {success_ratio:.2%}")
    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Total Profit: {total_profit:.4f} USDC")

if __name__ == "__main__":
    backtest()
