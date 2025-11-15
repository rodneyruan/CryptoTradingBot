import pandas as pd
import time
import threading
import requests
from binance import ThreadedWebsocketManager
from binance.client import Client
from ta.momentum import RSIIndicator
from key_config import apikey, apisecret

# -----------------------------
# Configuration
# -----------------------------
symbol = "BTCFDUSD"        # Spot symbol
quantity = 0.001           # BTC to buy
rsi_period = 6
rsi_buy = 30
tp_pct = 0.003             # 0.3%
sl_pct = 0.01              # 1%
cancel_after = 10 * 60     # Cancel unfilled limit buy after 10 minutes

# Telegram configuration
TELEGRAM_TOKEN = "YOUR_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

# Global order IDs and stats
limit_buy_id = None
tp_id = None
sl_id = None
entry_price_global = 0.0
limit_buy_timestamp = None
position_open = False
total_trades = 0
successful_trades = 0
total_profit = 0.0

# Klines history
klines_history = []

# -----------------------------
# Telegram notification
# -----------------------------
def send_telegram(msg):
    return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print(f"Telegram error: {e}")

# -----------------------------
# Binance client and WebSocket
# -----------------------------
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)
twm.start()

# -----------------------------
# Initialize historical klines
# -----------------------------
def initialize_klines_history(limit=50):
    global klines_history
    try:
        klines = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=limit)
        klines_history = [float(k[4]) for k in klines]  # closing price is index 4
        print(f"Initialized klines_history with {len(klines_history)} candles")
    except Exception as e:
        print(f"Error fetching historical klines: {e}")

# -----------------------------
# Place TP/SL orders
# -----------------------------
def place_tp_sl(entry_price):
    global tp_id, sl_id, total_trades

    tp_price = round(entry_price * (1 + tp_pct), 2)
    sl_price = round(entry_price * (1 - sl_pct) * 1.001, 2)  # buffer for spot

    # Take-Profit (limit sell)
    tp_order = client.create_order(
        symbol=symbol,
        side="SELL",
        type="LIMIT",
        quantity=quantity,
        price=str(tp_price),
        timeInForce="GTC"
    )
    tp_id = tp_order["orderId"]
    send_telegram(f"Take Profit order placed at {tp_price}, Order ID: {tp_id}")

    # Stop-Loss (STOP-LOSS-LIMIT sell)
    sl_order = client.create_order(
        symbol=symbol,
        side="SELL",
        type="STOP_LOSS_LIMIT",
        quantity=quantity,
        price=str(sl_price),
        stopPrice=str(round(entry_price * (1 - sl_pct), 2)),
        timeInForce="GTC"
    )
    sl_id = sl_order["orderId"]
    send_telegram(f"Stop Loss order placed at {sl_price}, Order ID: {sl_id}")

    total_trades += 1
    print(f"Trade #{total_trades} placed: Entry={entry_price}, TP={tp_price}, SL={sl_price}")

# -----------------------------
# User Data Handler
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, tp_id, sl_id, total_profit, entry_price_global, successful_trades, position_open

    if msg["e"] != "executionReport":
        return

    order_id = int(msg["i"])
    status = msg["X"]
    filled_price = float(msg.get("L", 0))

    # Limit Buy filled → place TP/SL
    if order_id == limit_buy_id and status == "FILLED":
        entry_price_global = filled_price
        send_telegram(f"Limit Buy FILLED at {entry_price_global}, placing TP/SL")
        print(f"Limit Buy filled at {entry_price_global}")
        place_tp_sl(entry_price_global)

    # TP filled → cancel SL
    if order_id == tp_id and status == "FILLED":
        print(f"TP filled at {filled_price} → canceling SL")
        total_profit += (filled_price - entry_price_global) * quantity
        successful_trades += 1
        position_open = False
        send_telegram(f"Take Profit FILLED at {filled_price}, profit: {(filled_price - entry_price_global)*quantity:.4f} USDT")
        try:
            client.cancel_order(symbol=symbol, orderId=sl_id)
        except:
            pass

    # SL filled → cancel TP
    if order_id == sl_id and status == "FILLED":
        print(f"SL filled at {filled_price} → canceling TP")
        total_profit += (filled_price - entry_price_global) * quantity
        position_open = False
        send_telegram(f"Stop Loss FILLED at {filled_price}, loss: {(filled_price - entry_price_global)*quantity:.4f} USDT")
        try:
            client.cancel_order(symbol=symbol, orderId=tp_id)
        except:
            pass

    # Print statistics
    success_rate = (successful_trades / total_trades * 100) if total_trades > 0 else 0
    print(f"Total Trades: {total_trades}, Successful Trades: {successful_trades}, "
          f"Success Rate: {success_rate:.2f}%, Total P/L: {total_profit:.4f} USDT")

# -----------------------------
# Kline WebSocket Handler
# -----------------------------
def kline_handler(msg):
    global klines_history, limit_buy_id, position_open, limit_buy_timestamp

    k = msg['k']
    is_closed = k['x']
    close_price = float(k['c'])

    if is_closed:
        klines_history.append(close_price)
        if len(klines_history) > 50:
            klines_history.pop(0)

        if len(klines_history) > rsi_period:
            df = pd.DataFrame({'close': klines_history})
            df['rsi'] = RSIIndicator(df['close'], window=rsi_period).rsi()
            rsi_prev = df['rsi'].iloc[-2]
            rsi_now = df['rsi'].iloc[-1]

            print(f"Close Price: {close_price}, RSI: {rsi_now:.2f}")

            # RSI Buy signal, only if no active position
            if rsi_prev < rsi_buy and rsi_now >= rsi_buy and not position_open:
                print("RSI Buy signal detected!")
                buy_price = close_price - 50
                limit_order = client.create_order(
                    symbol=symbol,
                    side="BUY",
                    type="LIMIT",
                    quantity=quantity,
                    price=str(round(buy_price, 2)),
                    timeInForce="GTC"
                )
                limit_buy_id = limit_order["orderId"]
                limit_buy_timestamp = time.time()
                position_open = True
                send_telegram(f"Limit Buy placed at {buy_price}, Order ID: {limit_buy_id}")

# -----------------------------
# Monitor thread to cancel unfilled limit buy
# -----------------------------
def monitor_unfilled_limit_buy():
    global limit_buy_id, limit_buy_timestamp, position_open
    while True:
        if position_open and limit_buy_id and limit_buy_timestamp:
            elapsed = time.time() - limit_buy_timestamp
            if elapsed > cancel_after:
                try:
                    client.cancel_order(symbol=symbol, orderId=limit_buy_id)
                    send_telegram(f"Unfilled limit buy {limit_buy_id} canceled after {cancel_after/60:.0f} minutes")
                    print(f"Unfilled limit buy {limit_buy_id} canceled")
                except Exception as e:
                    print(f"Error canceling limit buy {limit_buy_id}: {e}")
                finally:
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    position_open = False
        time.sleep(5)

# -----------------------------
# Start Bot
# -----------------------------
if __name__ == "__main__":
    # Initialize history
    initialize_klines_history()

    # Start WebSockets
    twm.start_user_socket(callback=user_data_handler)
    twm.start_kline_socket(symbol=symbol.lower(), interval='1m', callback=kline_handler)
    print("WebSocket started…")

    # Start unfilled limit buy monitor thread
    cancel_thread = threading.Thread(target=monitor_unfilled_limit_buy, daemon=True)
    cancel_thread.start()
    print("Unfilled limit buy monitor thread started…")

    # Keep main thread alive
    while True:
        time.sleep(1)
