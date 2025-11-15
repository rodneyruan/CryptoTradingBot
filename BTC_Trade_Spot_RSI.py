"""
Spot RSI WebSocket Bot with:
 - Limit Buy at close-50 when RSI crosses above RSI_BUY
 - TP placed after buy fills (limit sell)
 - Manual SL monitored via candle closes
 - Cancel unfilled limit buy after CANCEL_AFTER seconds
 - Telegram notifications
 - CSV trade logging
 - Debug prints for each transaction
"""

import time
import threading
import requests
import pandas as pd
import csv
from datetime import datetime
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
        # reset all relevant globals once
        with lock:
            limit_buy_id = None
            limit_buy_timestamp = None
            tp_id = None
            entry_price = 0.0
            position_open = False

# -----------------------------
# User data handler
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, limit_buy_timestamp, cancel_event, tp_id, entry_price, position_open, successful_trades, total_profit
    try:
        if msg.get("e") != "executionReport":
            return

        order_id = int(msg.get("i", 0))
        status = msg.get("X")
        last_filled_price = float(msg.get("L", 0)) if msg.get("L") else 0.0
        print(f"[USER EVENT] orderId={order_id}, status={status}, lastPrice={last_filled_price}")

        with lock:
            if limit_buy_id is not None and order_id == limit_buy_id and status == "FILLED":
                entry_price = last_filled_price
                print(f"[USER EVENT] Limit BUY FILLED at {entry_price} (order {order_id})")
                send_telegram(f"?? Limit Buy FILLED at {entry_price} (order {order_id})")
                if cancel_event:
                    cancel_event.set()
                limit_buy_id = None
                limit_buy_timestamp = None
                place_take_profit(entry_price)
                position_open = True

            elif tp_id is not None and order_id == tp_id and status == "FILLED":
                filled_price = last_filled_price
                print(f"[USER EVENT] TP FILLED at {filled_price} (order {order_id})")
                profit = (filled_price - entry_price) * QUANTITY
                total_profit += profit
                successful_trades += 1
                position_open = False
                send_telegram(f"?? TP FILLED at {filled_price}. Profit: {profit:.8f} USDT")
                log_trade("TP", entry_price, filled_price, QUANTITY)
                tp_id = None
                entry_price = 0.0
    except Exception as e:
        print(f"[USER HANDLER ERROR] {e}")
        send_telegram(f"? USER HANDLER ERROR: {e}")

# -----------------------------
# Kline handler
# -----------------------------
def kline_handler(msg):
    global klines_history, limit_buy_id, limit_buy_timestamp, position_open, entry_price, tp_id, cancel_event

    try:
        k = msg.get('k', {})
        if not k:
            return
        is_closed = k.get('x', False)
        close_price = float(k.get('c', 0))
        if not is_closed:
            return

        klines_history.append(close_price)
        if len(klines_history) > KL_HISTORY_LIMIT:
            klines_history.pop(0)

        if len(klines_history) >= RSI_PERIOD + 2:
            df = pd.DataFrame({'close': klines_history})
            df['rsi'] = RSIIndicator(df['close'], window=RSI_PERIOD).rsi()
            rsi_prev = df['rsi'].iloc[-2]
            rsi_now = df['rsi'].iloc[-1]
            print(f"[KLINE] Close={close_price:.2f}, RSI_prev={rsi_prev:.2f}, RSI_now={rsi_now:.2f}")

            with lock:
                if (rsi_prev < RSI_BUY and rsi_now >= RSI_BUY) and not position_open:
                    buy_price = round(close_price - 50, 2)
                    try:
                        print(f"[ORDER] Placing LIMIT BUY at {buy_price}")
                        send_telegram(f"?? RSI buy signal. Placing LIMIT BUY at {buy_price}")
                        order = client.create_order(symbol=SYMBOL, side="BUY", type="LIMIT",
                                                   quantity=QUANTITY, price=str(buy_price), timeInForce="GTC")
                        limit_buy_id = order.get("orderId")
                        limit_buy_timestamp = time.time()
                        position_open = True
                        print(f"[ORDER] LIMIT BUY placed orderId={limit_buy_id} at {buy_price}")
                        cancel_event = start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)
                    except Exception as e:
                        print(f"[ORDER ERROR] Failed to place limit buy: {e}")
                        send_telegram(f"? Failed to place limit buy at {buy_price}: {e}")
                        limit_buy_id = None
                        limit_buy_timestamp = None
                        position_open = False

                if position_open and entry_price > 0:
                    sl_threshold = entry_price * (1 - SL_PCT)
                    if close_price <= sl_threshold:
                        print(f"[SL CHECK] Close {close_price} <= SL threshold {sl_threshold}. Executing manual SL.")
                        execute_manual_sl(close_price)
    except Exception as e:
        print(f"[KLINE HANDLER ERROR] {e}")
        send_telegram(f"? KLINE HANDLER ERROR: {e}")

# -----------------------------
# Start Bot
# -----------------------------
def start_bot():
    print("[BOT] Initializing...")
    initialize_klines_history(limit=KL_HISTORY_LIMIT)

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
