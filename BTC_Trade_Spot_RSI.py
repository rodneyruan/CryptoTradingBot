"""
Spot RSI WebSocket bot with:
 - limit buy at close-50 when RSI crosses above RSI_BUY
 - TP placed after buy fills (limit sell)
 - Manual SL monitored via candle closes; executes a fallback sell (market or limit)
 - cancel unfilled limit buy after CANCEL_AFTER seconds using a separate cancel thread (Event controlled)
 - telegram notifications
 - CSV logging for all trades
 - startup reconciliation (adopt existing open orders)
 - debug prints for each transaction
 - optional Flask health endpoint
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
SYMBOL = "BTCFDUSD"          # spot symbol
QUANTITY = 0.001             # base asset amount to buy (BTC)
RSI_PERIOD = 6
RSI_BUY = 30
TP_PCT = 0.003               # take profit percent (0.3%)
SL_PCT = 0.01                # stop loss percent (1%)
CANCEL_AFTER = 10 * 60       # seconds to cancel unfilled limit buy
USE_MARKET_ON_SL = True      # If True, SL uses MARKET sell to ensure exit (faster)
KL_HISTORY_LIMIT = 100       # historical klines to fetch on startup
LOG_FILE = "spot_rsi_trades.csv"  # CSV log file

# -----------------------------
# GLOBAL STATE
# -----------------------------
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)

limit_buy_id = None
limit_buy_timestamp = None
cancel_event = None
tp_id = None
entry_price = 0.0
position_open = False
total_trades = 0
successful_trades = 0
total_profit = 0.0
klines_history = []
lock = threading.Lock()  # protect shared globals

# -----------------------------
# TELEGRAM
# -----------------------------
def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")

# -----------------------------
# CSV logging
# -----------------------------
def log_trade(trade_type, price, order_id, profit=0.0):
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([datetime.utcnow().isoformat(), trade_type, price, order_id, profit])

# -----------------------------
# Initialize klines history
# -----------------------------
def initialize_klines_history(limit=KL_HISTORY_LIMIT):
    global klines_history
    try:
        print("[INIT] Fetching historical klines...")
        klines = client.get_klines(symbol=SYMBOL, interval=Client.KLINE_INTERVAL_1MINUTE, limit=limit)
        klines_history = [float(k[4]) for k in klines]
        print(f"[INIT] Loaded {len(klines_history)} historical 1m closes.")
    except Exception as e:
        print(f"[INIT ERROR] {e}")
        klines_history = []

# -----------------------------
# Startup reconciliation for open orders
# -----------------------------
def reconcile_open_orders():
    global limit_buy_id, tp_id, position_open, entry_price, cancel_event
    try:
        orders = client.get_open_orders(symbol=SYMBOL)
        print(f"[RECONCILE] Found {len(orders)} open orders at startup")
        for o in orders:
            side = o.get("side")
            type_ = o.get("type")
            order_id = o.get("orderId")
            price = float(o.get("price", 0))
            if side == "BUY" and type_ == "LIMIT":
                limit_buy_id = order_id
                position_open = True
                print(f"[RECONCILE] Adopting open LIMIT BUY {order_id} at {price}")
                cancel_event = start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)
            elif side == "SELL" and type_ == "LIMIT":
                tp_id = order_id
                position_open = True
                print(f"[RECONCILE] Adopting open TP SELL {order_id} at {price}")
    except Exception as e:
        print(f"[RECONCILE ERROR] {e}")

# -----------------------------
# Cancel thread per limit buy
# -----------------------------
def start_limit_buy_cancel_timer(order_id: int, timeout_seconds: int):
    global cancel_event
    cancel_event = threading.Event()
    def _worker():
        print(f"[CANCEL-TIMER] Started for order {order_id}, will cancel after {timeout_seconds}s")
        waited = 0
        interval = 1
        while waited < timeout_seconds:
            if cancel_event.is_set():
                print(f"[CANCEL-TIMER] Event set, not canceling order {order_id}")
                return
            time.sleep(interval)
            waited += interval
        with lock:
            global limit_buy_id, position_open
            if limit_buy_id == order_id:
                try:
                    print(f"[CANCEL-TIMER] Canceling unfilled LIMIT BUY {order_id}")
                    send_telegram(f"?? Canceling unfilled LIMIT BUY {order_id} after timeout")
                    client.cancel_order(symbol=SYMBOL, orderId=order_id)
                except Exception as e:
                    print(f"[CANCEL-TIMER ERROR] {e}")
                finally:
                    limit_buy_id = None
                    position_open = False
        print(f"[CANCEL-TIMER] Worker exiting for order {order_id}")
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
        print(f"[TP] Placing TP limit sell at {tp_price}")
        order = client.create_order(symbol=SYMBOL, side="SELL", type="LIMIT",
                                    quantity=QUANTITY, price=str(tp_price), timeInForce="GTC")
        tp_id = order.get("orderId")
        total_trades += 1
        print(f"[TP] TP order placed. orderId={tp_id}")
        send_telegram(f"? TP placed at {tp_price}, orderId={tp_id}")
        log_trade("TP_PLACED", tp_price, tp_id)
    except Exception as e:
        print(f"[TP ERROR] {e}")
        send_telegram(f"? TP placement failed: {e}")

# -----------------------------
# Manual SL
# -----------------------------
def execute_manual_sl(current_price: float):
    global tp_id, position_open, entry_price, total_profit
    try:
        print(f"[SL] Manual SL triggered at {current_price}")
        send_telegram(f"?? Manual SL triggered at {current_price}")
        # cancel TP
        if tp_id:
            try:
                client.cancel_order(symbol=SYMBOL, orderId=tp_id)
                print(f"[SL] Canceled TP {tp_id}")
            except Exception as e:
                print(f"[SL ERROR] Cancel TP {tp_id}: {e}")
        # sell
        if USE_MARKET_ON_SL:
            sell = client.order_market_sell(symbol=SYMBOL, quantity=QUANTITY)
            fills = sell.get("fills", [])
            executed_price = float(fills[0]["price"]) if fills else current_price
            print(f"[SL] MARKET sell executed at {executed_price}")
        else:
            executed_price = round(current_price, 2)
            sell = client.create_order(symbol=SYMBOL, side="SELL", type="LIMIT",
                                       quantity=QUANTITY, price=str(executed_price), timeInForce="GTC")
            print(f"[SL] LIMIT sell placed: {sell}")
        profit = (executed_price - entry_price) * QUANTITY
        total_profit += profit
        print(f"[SL] P/L: {profit:.8f} USDT")
        send_telegram(f"?? SL executed at {executed_price}, P/L: {profit:.8f} USDT")
        log_trade("SL_EXECUTED", executed_price, 0, profit)
    except Exception as e:
        print(f"[SL ERROR] {e}")
        send_telegram(f"? SL execution error: {e}")
    finally:
        with lock:
            global limit_buy_id, limit_buy_timestamp, tp_id, entry_price, position_open
            limit_buy_id = None
            limit_buy_timestamp = None
            tp_id = None
            entry_price = 0.0
            position_open = False

# -----------------------------
# User data handler
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, cancel_event, tp_id, entry_price, position_open, total_profit, successful_trades
    try:
        if msg.get("e") != "executionReport":
            return
        order_id = int(msg.get("i", 0))
        status = msg.get("X")
        last_filled_price = float(msg.get("L", 0)) if msg.get("L") else 0.0
        print(f"[USER EVENT] orderId={order_id}, status={status}, lastPrice={last_filled_price}")
        with lock:
            if limit_buy_id and order_id == limit_buy_id and status == "FILLED":
                entry_price = last_filled_price
                print(f"[USER EVENT] LIMIT BUY FILLED at {entry_price}")
                send_telegram(f"? LIMIT BUY FILLED at {entry_price}")
                if cancel_event:
                    cancel_event.set()
                limit_buy_id = None
                limit_buy_timestamp = None
                place_take_profit(entry_price)
                position_open = True
            elif tp_id and order_id == tp_id and status == "FILLED":
                filled_price = last_filled_price
                profit = (filled_price - entry_price) * QUANTITY
                total_profit += profit
                successful_trades += 1
                position_open = False
                print(f"[USER EVENT] TP FILLED at {filled_price}, P/L={profit:.8f}")
                send_telegram(f"? TP FILLED at {filled_price}, P/L={profit:.8f} USDT")
                tp_id = None
                entry_price = 0.0
                log_trade("TP_FILLED", filled_price, order_id, profit)
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
        if not k: return
        is_closed = k.get('x', False)
        close_price = float(k.get('c', 0))
        if not is_closed: return
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
                if rsi_prev < RSI_BUY and rsi_now >= RSI_BUY and not position_open:
                    buy_price = round(close_price - 50, 2)
                    try:
                        print(f"[ORDER] Placing LIMIT BUY at {buy_price}")
                        send_telegram(f"? RSI Buy signal: LIMIT BUY at {buy_price}")
                        order = client.create_order(symbol=SYMBOL, side="BUY", type="LIMIT",
                                                    quantity=QUANTITY, price=str(buy_price), timeInForce="GTC")
                        limit_buy_id = order.get("orderId")
                        limit_buy_timestamp = time.time()
                        position_open = True
                        print(f"[ORDER] LIMIT BUY placed orderId={limit_buy_id} at {buy_price}")
                        cancel_event = start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)
                        log_trade("LIMIT_BUY_PLACED", buy_price, limit_buy_id)
                    except Exception as e:
                        print(f"[ORDER ERROR] {e}")
                        send_telegram(f"? Failed LIMIT BUY at {buy_price}: {e}")
                        limit_buy_id = None
                        limit_buy_timestamp = None
                        position_open = False
            if position_open and entry_price > 0:
                sl_threshold = entry_price * (1 - SL_PCT)
                if close_price <= sl_threshold:
                    print(f"[SL CHECK] Close {close_price} <= SL threshold {sl_threshold}")
                    execute_manual_sl(close_price)
    except Exception as e:
        print(f"[KLINE HANDLER ERROR] {e}")
        send_telegram(f"? KLINE HANDLER ERROR: {e}")

# -----------------------------
# Flask health endpoint
# -----------------------------
app = Flask(__name__)
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "symbol": SYMBOL,
        "position_open": position_open,
        "entry_price": entry_price,
        "limit_buy_id": limit_buy_id,
        "tp_id": tp_id,
        "total_trades": total_trades,
        "successful_trades": successful_trades,
        "total_profit": total_profit
    })

# -----------------------------
# Start bot
# -----------------------------
def start_bot():
    print("[BOT] Initializing...")
    initialize_klines_history(KL_HISTORY_LIMIT)
    reconcile_open_orders()
    twm.start()
    twm.start_user_socket(callback=user_data_handler)
    print("[BOT] User WebSocket started")
    twm.start_kline_socket(symbol=SYMBOL.lower(), interval='1m', callback=kline_handler)
    print("[BOT] Kline WebSocket started")
    # Flask server in separate thread
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5000), daemon=True).start()
    print("[BOT] Flask health endpoint running at /health")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[BOT] Shutting down...")
        try: twm.stop()
        except: pass

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    start_bot()
