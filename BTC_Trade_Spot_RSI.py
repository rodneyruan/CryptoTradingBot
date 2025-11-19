#!/usr/bin/env python3
"""
Spot RSI WebSocket Bot (timeframe from argv[1], default 1m)
 - BUY when RSI crosses upward above RSI_LOWER from below
 - TP placed after buy fills (limit sell)
 - NO initial stop-loss order
 - Stop-loss handled by monitoring closed candles:
     * If close <= entry*(1-SL_PCT): place limit SL at close+20 and monitor 5 candles
 - Cancel unfilled limit buy after CANCEL_AFTER seconds
 - Telegram notifications & CSV logging
 - Flask health endpoint
"""

import time
import threading
import requests
import pandas as pd
import csv
import traceback
import sys
from datetime import datetime
from flask import Flask, jsonify

from ta.momentum import RSIIndicator
from binance.client import Client
from binance import ThreadedWebsocketManager

from key_config import (
    apikey,
    apisecret,
    TELEGRAM_TOKEN,
    CHAT_ID
)

try:
    from zoneinfo import ZoneInfo
except:
    ZoneInfo = None


# ============================================================
# USER CONFIG
# ============================================================

SYMBOL = "BTCFDUSD"
QUANTITY = 0.01
TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1m"

RSI_PERIOD = 14
RSI_LOWER = 30        # Buy when rsi crosses upward through this

TP_PCT = 0.002        # 0.2%
SL_PCT = 0.01         # 1% SL trigger
CANCEL_AFTER = 10*60  # 10 min

USE_MARKET_ON_SL = True
KL_HISTORY_LIMIT = 200
STOPLOSS_LIMIT_RETRY_MAX = 5

LOG_FILE = "trade_log.csv"
LOCAL_TZ = "America/Los_Angeles"

# ============================================================
# GLOBAL STATE
# ============================================================

client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)

klines_history = []

limit_buy_id = None
limit_buy_timestamp = None
cancel_event = None
tp_id = None
stoploss_limit_id = None
stoploss_monitor_attempts = 0

position_open = False
entry_price = 0.0

total_trades = 0
successful_trades = 0
total_profit = 0
last_trade = None

lock = threading.Lock()
app = Flask(__name__)

# ============================================================
# UTILITIES
# ============================================================

def now_str():
    if ZoneInfo:
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

def send_exception_to_telegram(exc):
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": f"Exception:\n{text}"})
    except:
        pass


# ============================================================
# CSV Logging
# ============================================================

try:
    with open(LOG_FILE, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Timestamp","Event","OrderID",
            "EntryPrice","ExitPrice","Quantity",
            "Profit","Notes"
        ])
except FileExistsError:
    pass

def log_trade(event, order_id=None, entry=0, exit_price=0, quantity=0, profit=0, notes=""):
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            now_str(), event, order_id or "",
            f"{entry:.8f}", f"{exit_price:.8f}",
            f"{quantity:.8f}", f"{profit:.8f}", notes
        ])

# ============================================================
# INIT KLINES
# ============================================================

def timeframe_to_interval(tf):
    mapping = {
        "1m": Client.KLINE_INTERVAL_1MINUTE,
        "3m": Client.KLINE_INTERVAL_3MINUTE,
        "5m": Client.KLINE_INTERVAL_5MINUTE,
        "15m": Client.KLINE_INTERVAL_15MINUTE,
        "30m": Client.KLINE_INTERVAL_30MINUTE,
        "1h": Client.KLINE_INTERVAL_1HOUR,
        "4h": Client.KLINE_INTERVAL_4HOUR,
        "1d": Client.KLINE_INTERVAL_1DAY,
    }
    return mapping.get(tf, tf)

def initialize_klines_history():
    global klines_history
    try:
        interval = timeframe_to_interval(TIMEFRAME)
        klines = client.get_klines(symbol=SYMBOL, interval=interval, limit=KL_HISTORY_LIMIT)
        klines_history = [float(k[4]) for k in klines]
        print(f"[INIT] Loaded {len(klines_history)} historical closes.")
    except Exception as e:
        print("[INIT ERROR]", e)
        klines_history = []

# ============================================================
# LIMIT BUY CANCEL TIMER
# ============================================================

def start_limit_buy_cancel_timer(order_id, timeout_seconds):
    global cancel_event

    cancel_event = threading.Event()

    def worker():
        waited = 0
        while waited < timeout_seconds:
            if cancel_event.is_set():
                return
            time.sleep(1)
            waited += 1

        with lock:
            global limit_buy_id, limit_buy_timestamp, position_open
            if limit_buy_id == order_id:
                try:
                    client.cancel_order(symbol=SYMBOL, orderId=order_id)
                    send_telegram(f"Cancelled unfilled BUY {order_id}")
                    log_trade("BUY_CANCELLED_TIMEOUT", order_id)
                except:
                    pass
                limit_buy_id = None
                limit_buy_timestamp = None
                position_open = False

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return cancel_event


# ============================================================
# TAKE PROFIT
# ============================================================

def place_take_profit(entry):
    global tp_id, last_trade, total_trades

    tp_price = round(entry * (1 + TP_PCT), 2)

    try:
        order = client.create_order(
            symbol=SYMBOL,
            side="SELL",
            type="LIMIT",
            quantity=QUANTITY,
            price=str(tp_price),
            timeInForce="GTC"
        )
        tp_id = order["orderId"]
        total_trades += 1

        log_trade("TP_PLACED", tp_id, entry=entry, exit_price=tp_price, quantity=QUANTITY)
        send_telegram(f"TP placed @ {tp_price}")

    except Exception as e:
        send_exception_to_telegram(e)


# ============================================================
# MANUAL SL
# ============================================================

def execute_manual_sl(price):
    global tp_id, stoploss_limit_id, position_open, entry_price, total_profit

    try:
        if tp_id:
            try: client.cancel_order(symbol=SYMBOL, orderId=tp_id)
            except: pass
            tp_id = None

        if stoploss_limit_id:
            try: client.cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
            except: pass
            stoploss_limit_id = None

        if USE_MARKET_ON_SL:
            sell = client.order_market_sell(symbol=SYMBOL, quantity=QUANTITY)
            filled_price = float(sell["fills"][0]["price"])
        else:
            filled_price = round(price + 20, 2)
            client.create_order(symbol=SYMBOL, side="SELL", type="LIMIT",
                                quantity=QUANTITY, price=str(filled_price))

        profit = (filled_price - entry_price) * QUANTITY
        total_profit += profit

        log_trade("SL", None, entry_price, filled_price, QUANTITY, profit)
        send_telegram(f"SL executed @ {filled_price}")

    except Exception as e:
        send_exception_to_telegram(e)

    finally:
        position_open = False
        entry_price = 0
        stoploss_limit_id = None


# ============================================================
# USER DATA HANDLER
# ============================================================

def user_data_handler(msg):
    global limit_buy_id, tp_id, entry_price, position_open
    global stoploss_limit_id, stoploss_monitor_attempts, total_profit

    if msg.get("e") != "executionReport":
        return

    status = msg.get("X")
    order_id = int(msg.get("i"))
    filled_price = float(msg.get("L") or 0)

    # -------------------------
    # BUY FILLED
    # -------------------------
    if limit_buy_id == order_id:
        if status == "FILLED":
            entry_price = filled_price
            position_open = True
            limit_buy_id = None

            if cancel_event:
                cancel_event.set()

            log_trade("BUY_FILLED", order_id, entry=entry_price, quantity=QUANTITY)
            send_telegram(f"Buy filled @ {entry_price}")

            place_take_profit(entry_price)

        elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
            limit_buy_id = None
            position_open = False

    # -------------------------
    # TP FILLED
    # -------------------------
    elif tp_id == order_id:
        if status == "FILLED":
            profit = (filled_price - entry_price) * QUANTITY
            total_profit += profit

            log_trade("TP_FILLED", order_id, entry=entry_price,
                      exit_price=filled_price, quantity=QUANTITY, profit=profit)
            send_telegram(f"TP hit @ {filled_price}  Profit={profit:.4f}")

            tp_id = None
            entry_price = 0
            position_open = False


# ============================================================
# KLINE HANDLER
# ============================================================

def kline_handler(msg):
    global klines_history, position_open
    global limit_buy_id, limit_buy_timestamp
    global stoploss_limit_id, stoploss_monitor_attempts, entry_price

    k = msg.get("k", {})
    if not k or not k.get("x"):
        return

    close_price = float(k["c"])
    klines_history.append(close_price)

    if len(klines_history) > KL_HISTORY_LIMIT:
        klines_history.pop(0)

    # Need RSI_PERIOD+1 candles
    if len(klines_history) < RSI_PERIOD + 2:
        return

    df = pd.DataFrame({"close": klines_history})
    df["rsi"] = RSIIndicator(df["close"], RSI_PERIOD).rsi()

    rsi_prev = df["rsi"].iloc[-2]
    rsi_now  = df["rsi"].iloc[-1]

    print(f"[KLINE] Close={close_price:.2f}, RSI_prev={rsi_prev:.2f}, RSI_now={rsi_now:.2f}")

    # ============================================================
    # BUY CONDITION (RSI CROSS-UP)
    # ============================================================
    buy_signal = (rsi_prev < RSI_LOWER) and (rsi_now >= RSI_LOWER)

    if buy_signal:
        with lock:
            if not position_open:
                buy_price = round(close_price - 30, 2)
                try:
                    order = client.create_order(
                        symbol=SYMBOL,
                        side="BUY",
                        type="LIMIT",
                        quantity=QUANTITY,
                        price=str(buy_price),
                        timeInForce="GTC"
                    )
                    limit_buy_id = order["orderId"]
                    limit_buy_timestamp = time.time()
                    position_open = True

                    log_trade("BUY_PLACED", limit_buy_id,
                              entry=buy_price, quantity=QUANTITY,
                              notes="RSI cross-up")

                    send_telegram(f"RSI BUY @ {buy_price}")
                    start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)

                except Exception as e:
                    send_exception_to_telegram(e)
                    limit_buy_id = None
                    position_open = False

    # ============================================================
    # STOP-LOSS MONITORING
    # ============================================================
    if position_open and entry_price > 0:

        sl_threshold = entry_price * (1 - SL_PCT)

        # 1) First SL trigger
        if close_price <= sl_threshold and stoploss_limit_id is None:

            if tp_id:
                try: client.cancel_order(symbol=SYMBOL, orderId=tp_id)
                except: pass
                tp_id = None

            limit_price = round(close_price + 20, 2)

            try:
                order = client.create_order(
                    symbol=SYMBOL,
                    side="SELL",
                    type="LIMIT",
                    quantity=QUANTITY,
                    price=str(limit_price),
                    timeInForce="GTC"
                )

                stoploss_limit_id = order["orderId"]
                stoploss_monitor_attempts = 0

                log_trade("SL_LIMIT_PLACED", stoploss_limit_id,
                          entry=entry_price, exit_price=limit_price)

                send_telegram(f"SL LIMIT placed @ {limit_price}")

            except Exception as e:
                send_exception_to_telegram(e)
                stoploss_limit_id = None

        # 2) Monitor SL limit
        elif stoploss_limit_id is not None:
            stoploss_monitor_attempts += 1

            try:
                o = client.get_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                status = o["status"]
            except:
                status = None

            # If filled → user_data_handler will finalize
            if status == "FILLED":
                return

            # After 5 closed candles, fallback to market SL
            if stoploss_monitor_attempts >= STOPLOSS_LIMIT_RETRY_MAX:
                try:
                    client.cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                except:
                    pass
                stoploss_limit_id = None
                execute_manual_sl(close_price)


# ============================================================
# FLASK HEALTH ENDPOINT
# ============================================================

@app.route("/health")
def health():
    return jsonify({"status": "running", "symbol": SYMBOL})


# ============================================================
# MAIN START
# ============================================================

if __name__ == "__main__":

    initialize_klines_history()

    twm.start()

    twm.start_user_socket(user_data_handler)
    twm.start_kline_socket(callback=kline_handler,
                           symbol=SYMBOL,
                           interval=TIMEFRAME)

    app.run(host="0.0.0.0", port=5000)
