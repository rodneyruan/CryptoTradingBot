#!/usr/bin/env python3
"""
BTCUSDC Futures EMA Bot (timeframe from argv[1], default 5m)
 - Limit Buy when EMA_FAST crosses above EMA_SLOW
 - TP placed after buy fills (limit sell)
 - NO stop-loss order at entry
 - Stop-loss handled by monitoring each closed candle
 - Cancel unfilled limit buy after CANCEL_AFTER seconds
 - Telegram notifications
 - CSV trade logging
 - Flask /health endpoint
"""

import time
import threading
import requests
import pandas as pd
import csv
import traceback
import sys
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except:
    ZoneInfo = None
from flask import Flask, jsonify
from binance import ThreadedWebsocketManager
from binance.client import Client
from ta.trend import EMAIndicator
from key_config import apikey, apisecret, TELEGRAM_TOKEN, CHAT_ID

# -----------------------------
# USER CONFIG
# -----------------------------
SYMBOL = "BTCUSDC"          # USDC-M Futures
QUANTITY = 0.01              # quantity of BTC
TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1m"

EMA_FAST = 9
EMA_SLOW = 21

TP_PCT = 0.002               # 0.2% TP
SL_PCT = 0.01                # 1% SL
CANCEL_AFTER = 10*60
USE_MARKET_ON_SL = True
KL_HISTORY_LIMIT = 200
STOPLOSS_LIMIT_RETRY_MAX = 5

LOG_FILE = "trade_log.csv"
LOCAL_TZ = "America/Los_Angeles"

# -----------------------------
# GLOBAL STATE
# -----------------------------
client = Client(apikey, apisecret)
client.FUTURES_URL = "https://fapi.binance.com"  # old python-binance futures fix

twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret, futures=True)

limit_buy_id = None
limit_buy_timestamp = None
cancel_event = None
tp_id = None
stoploss_limit_id = None
stoploss_monitor_attempts = 0
entry_price = 0.0
position_open = False
total_trades = 0
successful_trades = 0
total_profit = 0.0
last_trade = None
klines_history = []
lock = threading.Lock()

app = Flask(__name__)

# -----------------------------
# Utilities
# -----------------------------
def now_str():
    if ZoneInfo:
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d %H:%M:%S %Z")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(msg: str):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"[{now_str()}] [TELEGRAM ERROR] {e}")

def send_exception_to_telegram(exc: BaseException):
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    payload = f"?? <b>Bot Exception</b>\n<pre>{text}</pre>"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": payload, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"[{now_str()}] [TELEGRAM ERROR] failed to send exception: {e}")

# -----------------------------
# CSV Logging
# -----------------------------
try:
    with open(LOG_FILE, "x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Event", "OrderID", "EntryPrice", "ExitPrice", "Quantity", "P/L", "Notes"])
except FileExistsError:
    pass

def log_trade(event, order_id=None, entry=0.0, exit_price=0.0, quantity=0.0, profit=0.0, notes=""):
    ts = now_str()
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([ts, event, order_id or "", f"{entry:.8f}" if entry else "", f"{exit_price:.8f}" if exit_price else "", f"{quantity:.8f}" if quantity else "", f"{profit:.8f}" if profit else "", notes])
    print(f"[{ts}] [LOG] {event} order={order_id} entry={entry} exit_price={exit_price} profit={profit} notes={notes}")

# -----------------------------
# Historical klines
# -----------------------------
def timeframe_to_interval(tf: str):
    mapping = {
        "1m": Client.KLINE_INTERVAL_1MINUTE,
        "3m": Client.KLINE_INTERVAL_3MINUTE,
        "5m": Client.KLINE_INTERVAL_5MINUTE,
        "15m": Client.KLINE_INTERVAL_15MINUTE,
        "30m": Client.KLINE_INTERVAL_30MINUTE,
        "1h": Client.KLINE_INTERVAL_1HOUR,
        "2h": Client.KLINE_INTERVAL_2HOUR,
        "4h": Client.KLINE_INTERVAL_4HOUR,
        "6h": Client.KLINE_INTERVAL_6HOUR,
        "8h": Client.KLINE_INTERVAL_8HOUR,
        "12h": Client.KLINE_INTERVAL_12HOUR,
        "1d": Client.KLINE_INTERVAL_1DAY,
    }
    return mapping.get(tf, tf)

def initialize_klines_history(limit=KL_HISTORY_LIMIT):
    global klines_history
    try:
        print(f"[{now_str()}] [INIT] Fetching {limit} historical {TIMEFRAME} klines...")
        interval = timeframe_to_interval(TIMEFRAME)
        klines = client.futures_klines(symbol=SYMBOL, interval=interval, limit=limit)
        klines_history = [float(k[4]) for k in klines]
        print(f"[{now_str()}] [INIT] Loaded {len(klines_history)} historical closes.")
    except Exception as e:
        print(f"[{now_str()}] [INIT ERROR] {e}")
        send_exception_to_telegram(e)
        klines_history = []

# -----------------------------
# Cancel Timer
# -----------------------------
def start_limit_buy_cancel_timer(order_id: int, timeout_seconds: int):
    global cancel_event
    cancel_event = threading.Event()
    def worker():
        waited = 0
        while waited < timeout_seconds:
            if cancel_event.is_set(): return
            time.sleep(1)
            waited += 1
        with lock:
            global limit_buy_id, limit_buy_timestamp, position_open
            if limit_buy_id == order_id:
                try:
                    client.futures_cancel_order(symbol=SYMBOL, orderId=order_id)
                    send_telegram(f"Cancelled unfilled limit buy {order_id} after {timeout_seconds//60} minutes")
                    log_trade("CANCELLED_UNFILLED_BUY", order_id, notes=f"Timed out {timeout_seconds}s")
                except Exception as e:
                    send_exception_to_telegram(e)
                finally:
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    position_open = False
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return cancel_event

# -----------------------------
# Place TP
# -----------------------------
def place_take_profit(filled_entry_price: float):
    global tp_id, total_trades, last_trade
    tp_price = round(filled_entry_price * (1 + TP_PCT), 1)
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side="SELL",
            type="LIMIT",
            quantity=QUANTITY,
            price=str(tp_price),
            timeInForce="GTC"
        )
        tp_id = order.get("orderId")
        total_trades += 1
        last_trade = {"type": "TP_PLACED", "order_id": tp_id, "entry": filled_entry_price, "tp": tp_price}
        send_telegram(f"TP placed at {tp_price}, orderId={tp_id}")
        log_trade("TP_PLACED", tp_id, entry=filled_entry_price, exit_price=tp_price, quantity=QUANTITY, profit=0.0, notes="TP placed after buy fill")
    except Exception as e:
        send_exception_to_telegram(e)
        send_telegram(f"Failed to place TP: {e}")

# -----------------------------
# Manual SL
# -----------------------------
def execute_manual_sl(current_price: float):
    global tp_id, stoploss_limit_id, stoploss_monitor_attempts, position_open, entry_price, total_profit, last_trade, limit_buy_id, limit_buy_timestamp
    try:
        send_telegram(f"Manual SL triggered at {current_price}")
        if tp_id:
            try: client.futures_cancel_order(symbol=SYMBOL, orderId=tp_id)
            except: pass
            tp_id = None
        if stoploss_limit_id:
            try: client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
            except: pass
            stoploss_limit_id = None
            stoploss_monitor_attempts = 0
        if USE_MARKET_ON_SL:
            sell = client.futures_create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=QUANTITY)
            executed_price = float(sell.get("avgFillPrice") or current_price)
        else:
            executed_price = round(current_price+20, 1)
            client.futures_create_order(symbol=SYMBOL, side="SELL", type="LIMIT", quantity=QUANTITY, price=str(executed_price), timeInForce="GTC")
        profit = (executed_price - entry_price) * QUANTITY
        total_profit += profit
        log_trade("SL", None, entry=entry_price, exit_price=executed_price, quantity=QUANTITY, profit=profit, notes="Manual SL")
        last_trade = {"type": "SL", "entry": entry_price, "exit": executed_price, "profit": profit}
    except Exception as e:
        send_exception_to_telegram(e)
    finally:
        with lock:
            limit_buy_id = limit_buy_timestamp = tp_id = stoploss_limit_id = None
            stoploss_monitor_attempts = 0
            entry_price = 0.0
            position_open = False

# -----------------------------
# User Data
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, limit_buy_timestamp, cancel_event, tp_id, stoploss_limit_id, stoploss_monitor_attempts, entry_price, position_open, total_profit, successful_trades, last_trade
    try:
        if msg.get("e") != "executionReport": return
        order_id = int(msg.get("i", 0))
        status = msg.get("X")
        last_filled_price = float(msg.get("L") or 0)
        with lock:
            # Limit buy filled
            if limit_buy_id and order_id == limit_buy_id:
                if status == "FILLED":
                    entry_price = last_filled_price
                    send_telegram(f"Limit Buy FILLED at {entry_price}")
                    if cancel_event: cancel_event.set()
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    position_open = True
                    place_take_profit(entry_price)
                    last_trade = {"type": "BUY_FILLED", "order_id": order_id, "entry": entry_price}
                    log_trade("BUY_FILLED", order_id, entry=entry_price, quantity=QUANTITY)
                elif status in ["CANCELED","EXPIRED","REJECTED"]:
                    limit_buy_id = limit_buy_timestamp = None
                    position_open = False
                    if cancel_event: cancel_event.set()
                    log_trade("BUY_CANCELLED", order_id, notes=f"Limit buy {status}")

            # TP filled
            elif tp_id and order_id == tp_id:
                if status == "FILLED":
                    filled_price = last_filled_price
                    profit = (filled_price - entry_price) * QUANTITY
                    total_profit += profit
                    successful_trades += 1
                    position_open = False
                    send_telegram(f"TP FILLED at {filled_price}. Profit: {profit:.8f} USDT")
                    log_trade("TP", order_id, entry=entry_price, exit_price=filled_price, quantity=QUANTITY, profit=profit)
                    last_trade = {"type": "TP", "entry": entry_price, "exit": filled_price, "profit": profit}
                    tp_id = None
                    entry_price = 0.0
                else: tp_id = None

            # Stop-loss limit filled
            elif stoploss_limit_id and order_id == stoploss_limit_id:
                if status == "FILLED":
                    filled_price = last_filled_price
                    profit = (filled_price - entry_price) * QUANTITY
                    total_profit += profit
                    send_telegram(f"Stop-loss LIMIT FILLED at {filled_price}. P/L: {profit:.8f} USDT")
                    log_trade("SL_LIMIT_FILLED", order_id, entry=entry_price, exit_price=filled_price, quantity=QUANTITY, profit=profit)
                    last_trade = {"type": "SL_LIMIT", "entry": entry_price, "exit": filled_price, "profit": profit}
                    stoploss_limit_id = stoploss_monitor_attempts = 0
                    entry_price = 0.0
                    position_open = False
                else: stoploss_limit_id = stoploss_monitor_attempts = 0
    except Exception as e:
        send_exception_to_telegram(e)

# -----------------------------
# Kline Handler
# -----------------------------
def kline_handler(msg):
    global klines_history, limit_buy_id, limit_buy_timestamp, position_open, entry_price, tp_id, cancel_event, stoploss_limit_id, stoploss_monitor_attempts, total_profit, last_trade
    try:
        k = msg.get('k', {})
        if not k: return
        if not k.get('x', False): return
        close_price = float(k.get('c'))
        klines_history.append(close_price)
        if len(klines_history) > KL_HISTORY_LIMIT:
            klines_history.pop(0)
        if len(klines_history) < EMA_SLOW+1: return

        df = pd.DataFrame({'close': klines_history})
        df["ema_fast"] = EMAIndicator(df["close"], EMA_FAST).ema_indicator()
        df["ema_slow"] = EMAIndicator(df["close"], EMA_SLOW).ema_indicator()
        ema_fast_prev, ema_slow_prev = df["ema_fast"].iloc[-2], df["ema_slow"].iloc[-2]
        ema_fast_now, ema_slow_now = df["ema_fast"].iloc[-1], df["ema_slow"].iloc[-1]

        buy_signal = ema_fast_prev < ema_slow_prev and ema_fast_now >= ema_slow_now

        if buy_signal:
            with lock:
                if not position_open:
                    buy_price = round(close_price - 30, 1)
                    try:
                        order = client.futures_create_order(symbol=SYMBOL, side="BUY", type="LIMIT", quantity=QUANTITY, price=str(buy_price), timeInForce="GTC")
                        limit_buy_id = order.get("orderId")
                        limit_buy_timestamp = time.time()
                        position_open = True
                        send_telegram(f"BUY SIGNAL: EMA crossover. LIMIT BUY at {buy_price}")
                        log_trade("BUY_PLACED", limit_buy_id, entry=buy_price, quantity=QUANTITY)
                        start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)
                    except Exception as e:
                        send_exception_to_telegram(e)
                        limit_buy_id = limit_buy_timestamp = None
                        position_open = False

        # Stop-loss monitoring
        if position_open and entry_price > 0:
            sl_trigger = entry_price * (1 - SL_PCT)
            if close_price <= sl_trigger and stoploss_limit_id is None:
                with lock:
                    if tp_id:
                        try: client.futures_cancel_order(symbol=SYMBOL, orderId=tp_id)
                        except: pass
                        tp_id = None
                    limit_price = round(close_price + 20, 1)
                    try:
                        order = client.futures_create_order(symbol=SYMBOL, side="SELL", type="LIMIT", quantity=QUANTITY, price=str(limit_price), timeInForce="GTC")
                        stoploss_limit_id = order.get("orderId")
                        stoploss_monitor_attempts = 0
                        send_telegram(f"Stop-loss LIMIT placed at {limit_price}, orderId={stoploss_limit_id}")
                        log_trade("SL_LIMIT_PLACED", stoploss_limit_id, entry=entry_price, exit_price=limit_price, quantity=QUANTITY)
                    except Exception as e:
                        send_exception_to_telegram(e)
                        stoploss_limit_id = stoploss_monitor_attempts = 0
            elif stoploss_limit_id:
                stoploss_monitor_attempts += 1
                try:
                    status = client.futures_get_order(symbol=SYMBOL, orderId=stoploss_limit_id).get("status")
                except: status = None
                if status == "FILLED":
                    filled_price = close_price
                    profit = (filled_price - entry_price) * QUANTITY
                    total_profit += profit
                    send_telegram(f"Stop-loss LIMIT FILLED at {filled_price}. P/L: {profit:.8f}")
                    log_trade("SL_LIMIT_FILLED", stoploss_limit_id, entry=entry_price, exit_price=filled_price, quantity=QUANTITY, profit=profit)
                    last_trade = {"type":"SL_LIMIT","entry":entry_price,"exit":filled_price,"profit":profit}
                    stoploss_limit_id = stoploss_monitor_attempts = 0
                    entry_price = 0.0
                    position_open = False
                elif stoploss_monitor_attempts >= STOPLOSS_LIMIT_RETRY_MAX:
                    try: client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                    except: pass
                    try:
                        sell = client.futures_create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=QUANTITY)
                        executed_price = float(sell.get("avgFillPrice") or close_price)
                        profit = (executed_price - entry_price) * QUANTITY
