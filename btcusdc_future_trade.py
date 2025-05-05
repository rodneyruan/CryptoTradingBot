from binance.client import Client
import pandas as pd
import time
import os
from datetime import datetime
import pytz

from key_config import apikey
from key_config import apisecret

# Binance Futures configuration
client = Client(apikey, apisecret)

# Trading parameters
symbol = 'BTCUSDC'
timeframe = Client.KLINE_INTERVAL_15MINUTE
ema_period = 7
buy_threshold = 0.0025  # Price 0.25% below EMA
profit_target = 0.003  # 0.3% profit
stop_loss_threshold = 0.03  # 3% stop loss
quantity = 0.001  # Trade size in BTC (adjust based on account size)
leverage = 1  # 1x leverage
has_position = False  # Local tracking of position
total_profit = 0.0  # Cumulative profit
successful_trades = 0  # Count of take-profit trades
total_trades = 0  # Total trades
profit_file = 'btcusdc_future_profit_history.txt'  # File to store profit
log_file = 'btcusdc_future_trade_log.txt'  # File to store trade log
buy_order_timeout = 300  # 5 minutes in seconds
tz = pytz.timezone('Asia/Singapore')  # UTC+8 timezone
PRICE_PRECISION = 2  # Price precision for rounding
rsi_period = 6  # RSI period
rsi_threshold = 15  # RSI below 15 for buy signal

def load_historical_profit():
    """Read historical profit and trade counts from file."""
    global total_profit, successful_trades, total_trades
    if os.path.exists(profit_file):
        try:
            with open(profit_file, 'r') as f:
                lines = f.readlines()
                total_profit = float(lines[0].strip())
                if len(lines) > 1:
                    successful_trades = int(lines[1].strip())
                    total_trades = int(lines[2].strip())
            print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Loaded historical data: Profit: {total_profit} USDC, Successful Trades: {successful_trades}, Total Trades: {total_trades}")
        except (ValueError, IOError) as e:
            print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Error reading profit file: {e}. Starting with 0 profit.")
            total_profit = 0.0
            successful_trades = 0
            total_trades = 0
    else:
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | No profit history file found. Starting with 0 profit.")
        total_profit = 0.0
        successful_trades = 0
        total_trades = 0

def save_profit():
    """Save total profit and trade counts to file."""
    try:
        with open(profit_file, 'w') as f:
            f.write(f"{total_profit}\n{successful_trades}\n{total_trades}")
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Saved total profit: {total_profit} USDC, Successful Trades: {successful_trades}, Total Trades: {total_trades}")
    except IOError as e:
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Error saving profit file: {e}")

def log_trade(trade_type, price, profit=0.0):
    """Log trade details to file with UTC+8 timestamp."""
    timestamp = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"{timestamp} | {trade_type} | Price: {price} | Profit: {profit} USDC\n"
    try:
        with open(log_file, 'a') as f:
            f.write(log_entry)
    except IOError as e:
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Error writing to log file: {e}")

def fetch_ohlcv(symbol, timeframe, limit=100):
    """Fetch OHLCV data from Binance Futures."""
    klines = client.futures_klines(symbol=symbol, interval=timeframe, limit=limit)
    df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_volume', 'trades', 'taker_base_volume', 'taker_quote_volume', 'ignored'])
    df['open'] = df['open'].astype(float)
    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    return df

def calculate_ema(df, period):
    """Calculate EMA for the given period."""
    return df['close'].ewm(span=period, adjust=False).mean()

def calculate_rsi(df, period=6):
    """Calculate RSI for the given period."""
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def is_negative_candle(row):
    """Check if a candle is negative (close < open)."""
    return row['close'] < row['open']

def get_current_price(symbol):
    """Fetch current market price with precision using futures ticker."""
    ticker = client.futures_symbol_ticker(symbol=symbol)
    return round(float(ticker['price']), PRICE_PRECISION)

def set_leverage(symbol, leverage):
    """Set leverage for the trading pair."""
    client.futures_change_leverage(symbol=symbol, leverage=leverage)

def place_limit_buy_order(symbol, quantity, price):
    """Place a limit buy order with rounded price."""
    price = round(price, PRICE_PRECISION)
    order = client.futures_create_order(
        symbol=symbol,
        side=Client.SIDE_BUY,
        type=Client.ORDER_TYPE_LIMIT,
        price=price,
        quantity=quantity,
        timeInForce=Client.TIME_IN_FORCE_GTC
    )
    return order

def place_limit_sell_order(symbol, quantity, price):
    """Place a limit sell order with rounded price."""
    price = round(price, PRICE_PRECISION)
    order = client.futures_create_order(
        symbol=symbol,
        side=Client.SIDE_SELL,
        type=Client.ORDER_TYPE_LIMIT,
        price=price,
        quantity=quantity,
        timeInForce=Client.TIME_IN_FORCE_GTC
    )
    return order

def place_stop_market_sell_order(symbol, quantity, stop_price):
    """Place a stop-market sell order with rounded price."""
    stop_price = round(stop_price, PRICE_PRECISION)
    order = client.futures_create_order(
        symbol=symbol,
        side=Client.SIDE_SELL,
        type=Client.ORDER_TYPE_STOP_MARKET,
        stopPrice=stop_price,
        quantity=quantity
    )
    return order

def check_order_status(order_id, symbol):
    """Check if an order is filled."""
    order = client.futures_get_order(symbol=symbol, orderId=order_id)
    return order['status'] == 'FILLED'

def cancel_order(order_id, symbol):
    """Cancel an order."""
    try:
        client.futures_cancel_order(symbol=symbol, orderId=order_id)
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Order {order_id} canceled")
    except Exception as e:
        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Error canceling order {order_id}: {e}")

def main():
    """Main trading loop."""
    global has_position, total_profit, successful_trades, total_trades
    load_historical_profit()  # Load historical profit and trade counts
    # set_leverage(symbol, leverage)
    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Starting trading bot for {symbol} on {timeframe} timeframe...")

    while True:
        try:
            # Skip if there's a local position
            if has_position:
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Local position active. Waiting...")
                time.sleep(60)
                continue

            # Fetch OHLCV data
            df = fetch_ohlcv(symbol, timeframe, limit=ema_period + rsi_period)
            
            # Calculate EMA and RSI
            df['ema'] = calculate_ema(df, ema_period)
            df['rsi'] = calculate_rsi(df, rsi_period)
            latest_ema = df['ema'].iloc[-1]
            latest_rsi = df['rsi'].iloc[-1]
            
            # Get current price and candle data
            current_price = get_current_price(symbol)
            latest_candles = df.iloc[-4:]  # Last 4 candles, including current
            
            # Check for negative candles
            negative_candles_3 = len(latest_candles) >= 3 and all(is_negative_candle(row) for _, row in latest_candles.iloc[-3:].iterrows())
            negative_candles_4 = len(latest_candles) >= 4 and all(is_negative_candle(row) for _, row in latest_candles.iterrows())
            
            # Check buy condition
            if (negative_candles_3 and current_price < latest_ema * (1 - buy_threshold)) or negative_candles_4 or (latest_rsi < rsi_threshold):
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Buy signal! Price: {current_price}, EMA: {latest_ema}, RSI: {latest_rsi}, 3 Neg Candles: {negative_candles_3}, 4 Neg Candles: {negative_candles_4}")
                
                # Place limit buy order at 0.01% below current price
                buy_price = round(current_price * 0.9999, PRICE_PRECISION)
                buy_order = place_limit_buy_order(symbol, quantity, buy_price)
                log_trade("LIMIT_BUY", buy_price)
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Limit buy order placed at {buy_price}: {buy_order['orderId']}")
                
                # Monitor buy order for 5 minutes
                start_time = time.time()
                while time.time() - start_time < buy_order_timeout:
                    if check_order_status(buy_order['orderId'], symbol):
                        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Limit buy order filled")
                        break
                    time.sleep(60)  # Check every 60 seconds
                
                # If buy order not filled, cancel it and continue
                if not check_order_status(buy_order['orderId'], symbol):
                    cancel_order(buy_order['orderId'], symbol)
                    log_trade("BUY_CANCELED", buy_price)
                    print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Limit buy order not filled within 5 minutes. Canceled.")
                    time.sleep(15 * 60)  # Wait for next 15-minute candle
                    continue
                
                # Buy order filled, update position
                has_position = True
                
                # Calculate profit target and stop loss prices
                profit_price = round(buy_price * (1 + profit_target), PRICE_PRECISION)
                stop_loss_price = round(buy_price * (1 - stop_loss_threshold), PRICE_PRECISION)
                
                # Place limit sell order (profit target)
                sell_order = place_limit_sell_order(symbol, quantity, profit_price)
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Sell order placed at {profit_price}: {sell_order['orderId']}")
                
                # Place stop-market sell order (stop loss)
                stop_order = place_stop_market_sell_order(symbol, quantity, stop_loss_price)
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Stop-loss order placed at {stop_loss_price}: {stop_order['orderId']}")
                
                # Monitor sell and stop-loss orders
                while has_position:
                    if check_order_status(sell_order['orderId'], symbol):
                        trade_profit = (profit_price - buy_price) * quantity
                        total_profit += trade_profit
                        successful_trades += 1
                        total_trades += 1
                        log_trade("TAKE_PROFIT", profit_price, trade_profit)
                        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Take-profit triggered: Profit: {trade_profit} USDC")
                        cancel_order(stop_order['orderId'], symbol)  # Cancel stop-loss
                        log_trade("STOP_LOSS_CANCELED", stop_loss_price)
                        has_position = False
                    elif check_order_status(stop_order['orderId'], symbol):
                        trade_profit = (stop_loss_price - buy_price) * quantity
                        total_profit += trade_profit
                        total_trades += 1
                        log_trade("STOP_LOSS", stop_loss_price, trade_profit)
                        print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Stop-loss triggered: Loss: {trade_profit} USDC")
                        has_position = False
                    else:
                        time.sleep(10)  # Check every 10 seconds
                
                # Calculate and print success ratio
                success_ratio = successful_trades / total_trades if total_trades > 0 else 0
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Total Profit: {total_profit:.4f} USDC, Success Ratio: {success_ratio:.2%}")
                
                # Save profit and trade counts
                save_profit()
            
            else:
                print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | No buy signal. Price: {current_price}, EMA: {latest_ema}, RSI: {latest_rsi}, 3 Neg Candles: {negative_candles_3}, 4 Neg Candles: {negative_candles_4}")
            
            # Wait 3 minutes
            time.sleep(3 * 60)
        
        except Exception as e:
            print(f"{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')} | Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
