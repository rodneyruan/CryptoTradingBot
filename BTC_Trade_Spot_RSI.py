"""
Spot RSI WebSocket Bot with:
 - Limit Buy at close-50 when RSI crosses above RSI_BUY
 - TP placed after buy fills (limit sell)
 - Manual SL monitored via candle closes
 - Cancel unfilled limit buy after CANCEL_AFTER seconds
 - Telegram notifications
 - CSV trade logging
 - Debug prints for each transaction
 - Flask /health endpoint
"""

import time
import threading
import requests
import pandas as pd
import csv
from datetime import datetime
from flask import Flask, jsonify
from binance import ThreadedWebsocketManager
from binance.client import Client
from ta.momentum import RSIIndicator
from key_config import apikey, apisecret, TELEGRAM_TOKEN, CHAT_ID

# -----------------------------
# USER CONFIG
# -----------------------------
SYMBOL = "BTCFDUSD"      # Spot symbol
QUANTITY = 0.001          # BTC to buy
RSI_PERIOD = 6
RSI_BUY = 30
TP_PCT = 0.003            # 0.3%
SL_PCT = 0.01             # 1%
CANCEL_AFTER = 10 * 60    # seconds to cancel unfilled limit buy
USE_MARKET_ON_SL = True   # True: SL uses MARKET sell, else LIMIT
KL_HISTORY_LIMIT = 100     # Historical klines on startup

LOG_FILE = "trade_log.csv"

# -----------------------------
# GLOBAL STATE
# -----------------------------
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)

# Order tracking
limit_buy_id = None
limit_buy_timestamp = None
cancel_event = None
tp_id = None
entry_price = 0.0
position_open = False
total_trades = 0
successful_trades = 0
total_profit = 0.0

# Klines history
klines_history = []

# Lock for shared state
lock = threading.Lock()

# Flask app for health monitoring
app = Flask(__name__)

# -----------------------------
# Initialize CSV log
# -----------------------------
try:
    with open(LOG_FILE, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "TradeType", "EntryPrice", "ExitPrice", "Quantity", "P/L"])
except FileExistsError:
    pass

def log_trade(trade_type: str, entry: float, exit: float, quantity: float):
    profit = (exit - entry) * quantity
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, trade_type, entry, exit, quantity, profit])
    print(f"[LOG] {trade_type} trade logged: Entry={entry}, Exit={exit}, P/L={profit:.8f}")

# -----------------------------
# Telegram helper
# -----------------------------
def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

# -----------------------------
# Initialize klines
# -----------------------------
def initialize_klines_history(limit=KL_HISTORY_LIMIT):
    global klines_history
    try:
        print("[INIT] Fetching historical klines...")
        klines = client.get_klines(symbol=SYMBOL, interval=Client.KLINE_INTERVAL_1MINUTE, limit=limit)
        klines_history = [float(k[4]) for k in klines]  # Close prices
        print(f"[INIT] Loaded {len(klines_history)} historical 1m closes.")
    except Exception as e:
        print(f"[INIT ERROR] fetching historical klines: {e}")
        klines_history = []

# -----------------------------
# Cancel thread for limit buy
# -----------------------------
def start_limit_buy_cancel_timer(order_id: int, timeout_seconds: int):
    global cancel_event
    cancel_event = threading.Event()

    def _worker():
        print(f"[CANCEL-TIMER] Started for order {order_id}, will cancel after {timeout_seconds}s unless filled.")
        waited = 0
        interval = 1
        while waited < timeout_seconds:
            if cancel_event.is_set():
                print(f"[CANCEL-TIMER] Event set - not canceling order {order_id}.")
                return
            time.sleep(interval)
            waited += interval
        with lock:
            global limit_buy_id, limit_buy_timestamp, position_open
            if limit_buy_id == order_id:
                try:
                    print(f"[CANCEL-TIMER] Canceling unfilled limit buy {order_id} ...")
                    send_telegram(f"? Canceling unfilled limit buy {order_id} after {timeout_seconds//60} minutes")
                    client.cancel_order(symbol=SYMBOL, orderId=order_id)
                except Exception as e:
                    print(f"[CANCEL-TIMER ERROR] Failed to cancel order {order_id}: {e}")
                finally:
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    position_open = False
        print(f"[CANCEL-TIMER] Worker exiting for order {order_id}.")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return cancel_event

# -----------------------------
# Place TP
# -----------------------------
def place_take_profit(filled_entry_price: float):
    global tp_id, total_trades
    tp_price = round(filled_entry_price * (1 + TP_PCT), 2)
    try:
        print(f"[TP] Placing TP limit sell at {tp_price} ...")
        order = client.create_order(
            symbol=SYMBOL,
            side="SELL",
            type="LIMIT",
            quantity=QUANTITY,
            price=str(tp_price),
            timeInForce="GTC"
        )
        tp_id = order.get("orderId")
        total_trades += 1
        print(f"[TP] TP order placed. orderId={tp_id}, TP={tp_price}")
        send_telegram(f"? TP placed at {tp_price}, orderId={tp_id}")
    except Exception as e:
        print(f"[TP ERROR] Failed to place TP: {e}")
        send_telegram(f"? Failed to place TP: {e}")

# -----------------------------
# Manual SL
# -----------------------------
def execute_manual_sl(current_price: float):
    global tp_id, position_open, entry_price, total_profit, limit_buy_id, limit_buy_timestamp
    try:
        print(f"[SL] Manual SL triggered at {current_price}. Exiting position.")
        send_telegram(f"?? Manual SL triggered at {current_price}. Exiting position.")
        
        if tp_id:
            try:
                client.cancel_order(symbol=SYMBOL, orderId=tp_id)
                print(f"[SL] Canceled TP order {tp_id} before SL execution.")
            except Exception as e:
                print(f"[SL] Error canceling TP {tp_id}: {e}")

        if USE_MARKET_ON_SL:
            sell = client.order_market_sell(symbol=SYMBOL, quantity=QUANTITY)
            fills = sell.get("fills", [])
            executed_price = float(fills[0]["price"]) if fills else current_price
            print(f"[SL] MARKET sell executed at {executed_price}")
        else:
            executed_price = round(current_price, 2)
            sell = client.create_order(symbol=SYMBOL, side="SELL", type="LIMIT",
                                       quantity=QUANTITY, price=str(executed_price), timeInForce="GTC")
            print(f"[SL] LIMIT sell order placed: {sell}")

        profit = (executed_price - entry_price) * QUANTITY
        total_profit += profit
        print(f"[SL] Position closed. P/L: {profit:.8f}")
        send_telegram(f"?? Stop Loss executed at {executed_price}. P/L: {profit:.8f} USDT")
        log_trade("SL", entry_price, executed_price, QUANTITY)

    except Exception as e:
        print(f"[SL ERROR] {e}")
        send_telegram(f"? SL execution error: {e}")
    finally:
        with lock:
            limit_buy_id = None
            limit_buy_timestamp = None
            tp_id = None
            entry_price = 0.0
            position_open = False

# -----------------------------
# Flask health endpoint
# -----------------------------
@app.route("/health", methods=["GET"])
def health():
    with lock:
        return jsonify({
            "status": "running",
            "position_open": position_open,
            "total_trades": total_trades,
            "successful_trades": successful_trades,
            "total_profit": total_profit
        })

def start_flask():
    app.run(host="0.0.0.0", port=5000)

# -----------------------------
# Start Bot
# -----------------------------
def start_bot():
    print("[BOT] Initializing...")
    initialize_klines_history(limit=KL_HISTORY_LIMIT)

    # Start Flask in separate thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    print("[BOT] Flask /health endpoint started on port 5000")

    # Start Binance WebSockets
    twm.start()
    twm.start_user_socket(callback=user_data_handler)
    print("[BOT] User data WebSocket started")
    twm.start_kline_socket(symbol=SYMBOL.lower(), interval='1m', callback=kline_handler)
    print("[BOT] Kline WebSocket started (1m)")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[BOT] Shutting down...")
        try:
            twm.stop()
        except:
            pass

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    start_bot()
