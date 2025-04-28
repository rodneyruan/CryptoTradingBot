import time
import logging
from binance.client import Client
from binance.enums import *
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from datetime import datetime

# Configure logging
logging.basicConfig(filename='trading_bot.log', level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Binance API credentials (replace with your own)
API_KEY = 'your_api_key_here'
API_SECRET = 'your_api_secret_here'

# Initialize Binance client
client = Client(API_KEY, API_SECRET)

# Trading parameters
SYMBOL = 'BTCUSDT'
QUANTITY = 0.001  # Amount of BTC to buy
PROFIT_TARGET = 1.05  # 5% profit
KLINE_INTERVAL = Client.KLINE_INTERVAL_4HOUR
RSI_PERIOD = 14
RSI_THRESHOLD = 30

def calculate_rsi(klines, periods=14):
    """Calculate RSI using ta library."""
    # Create DataFrame from klines
    df = pd.DataFrame(klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
        'taker_buy_quote_volume', 'ignore'
    ])
    # Convert close prices to float
    df['close'] = df['close'].astype(float)
    # Calculate RSI
    rsi = RSIIndicator(close=df['close'], window=periods, fillna=False)
    return rsi.rsi().iloc[-1] if not rsi.rsi().empty else None

def get_account_balance(asset='USDT'):
    """Get available balance for a given asset."""
    try:
        account = client.get_account()
        for balance in account['balances']:
            if balance['asset'] == asset:
                return float(balance['free'])
        return 0.0
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return 0.0

def place_buy_order(price):
    """Place a market buy order."""
    try:
        order = client.order_market_buy(
            symbol=SYMBOL,
            quantity=QUANTITY
        )
        logging.info(f"Buy order placed: {QUANTITY} BTC at market price ~{price}")
        return order
    except Exception as e:
        logging.error(f"Error placing buy order: {e}")
        return None

def place_sell_order(buy_price):
    """Place a limit sell order with 5% profit target."""
    sell_price = round(buy_price * PROFIT_TARGET, 2)
    try:
        order = client.order_limit_sell(
            symbol=SYMBOL,
            quantity=QUANTITY,
            price=sell_price
        )
        logging.info(f"Sell order placed: {QUANTITY} BTC at {sell_price}")
        return order
    except Exception as e:
        logging.error(f"Error placing sell order: {e}")
        return None

def main():
    logging.info("Starting trading bot...")
    position_open = False
    last_rsi = None

    while True:
        try:
            # Fetch 4H klines (last 50 candles for RSI calculation)
            klines = client.get_historical_klines(
                SYMBOL, KLINE_INTERVAL, "200 hours ago UTC"
            )
            if not klines:
                logging.error("No kline data retrieved")
                time.sleep(60)
                continue

            # Calculate current RSI using ta
            current_rsi = calculate_rsi(klines, RSI_PERIOD)
            if current_rsi is None:
                logging.error("RSI calculation failed")
                time.sleep(60)
                continue

            logging.info(f"Current RSI: {current_rsi:.2f}")

            # Check for buy signal
            if not position_open and current_rsi > RSI_THRESHOLD and (last_rsi is None or last_rsi <= RSI_THRESHOLD):
                # Check available USDT balance
                usdt_balance = get_account_balance('USDT')
                current_price = float(klines[-1][4])  # Latest close price
                required_usdt = current_price * QUANTITY

                if usdt_balance >= required_usdt:
                    # Place buy order
                    buy_order = place_buy_order(current_price)
                    if buy_order:
                        # Place sell order with 5% profit
                        sell_order = place_sell_order(current_price)
                        if sell_order:
                            position_open = True
                            logging.info("Position opened")
                else:
                    logging.warning(f"Insufficient USDT balance: {usdt_balance} < {required_usdt}")

            last_rsi = current_rsi

            # Check if sell order is filled
            if position_open:
                open_orders = client.get_open_orders(symbol=SYMBOL)
                if not open_orders:  # No open sell orders, assume position closed
                    position_open = False
                    logging.info("Position closed")

            # Wait for the next 4-hour candle (4 hours = 14400 seconds)
            time.sleep(14400)

        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            time.sleep(60)  # Wait before retrying

if __name__ == "__main__":
    main()
