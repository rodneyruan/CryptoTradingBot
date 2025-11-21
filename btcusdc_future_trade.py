#!/usr/bin/env python3
"""
Futures EMA Bot – BTCUSDC Perpetual
→ Fixed position size = 0.05 BTC every trade
→ NO leverage change (uses your current account leverage)
→ All your original logic preserved (limit entry, TP, candle-based SL, cancel timer, etc.)
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
except Exception:
    ZoneInfo = None

from flask import Flask, jsonify
from binance import ThreadedWebsocketManager
from binance.client import Client
from ta.trend import EMAIndicator
from key_config import apikey, apisecret, TELEGRAM_TOKEN, CHAT_ID

# =============================
# USER CONFIG
# =============================
SYMBOL = "BTCUSDC"                    # ← exactly as you asked
QUANTITY_BTC = 0.05                   # ← 0.05 BTC per trade (fixed)
TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1m"

EMA_FAST = 9
EMA_SLOW = 21
TP_PCT   = 0.002      # 0.2 %
SL_PCT   = 0.01       # 1.0 %
CANCEL_AFTER = 10 * 60
KL_HISTORY_LIMIT = 200
STOPLOSS_LIMIT_RETRY_MAX = 5
LOG_FILE = "futures_btcdusdc_trade_log.csv"
LOCAL_TZ = "America/Los_Angeles"

# =============================
# GLOBALS & FUTURES SETUP
# =============================
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)

# ---- Get precision & contract size for BTCUSDC Perpetual ----
info = client.futures_exchange_info()
symbol_info = next(s for s in info["symbols"] if s["symbol"] == SYMBOL)

QUANTITY_PRECISION = symbol_info["quantityPrecision"]      # usually 3 → 0.001 BTC steps
PRICE_PRECISION    = symbol_info["pricePrecision"]        # usually 1 or 2

# BTCUSDC Perpetual: 1 contract = 0.001 BTC  →  0.05 BTC = 50 contracts
CONTRACT_SIZE_BTC = 0.001
QUANTITY_CONTRACTS = round(QUANTITY_BTC / CONTRACT_SIZE_BTC, QUANTITY_PRECISION)  # = 50.000

print(f"[{now_str()}] [INIT] {SYMBOL} → {QUANTITY_BTC} BTC = {QUANTITY_CONTRACTS} contracts")

# ---- State variables (same as your spot bot) ----
limit_buy_id = None
limit_buy_timestamp = None
cancel_event = None
tp_id = None
stoploss_limit_id = None
stoploss_monitor_attempts = 0
entry_price = 0.0
position_open = False
total_trades = successful_trades = 0
total_profit_usdc = 0.0
last_trade = None
klines_history = []
lock = threading.Lock()
app = Flask(__name__)

# =============================
# UTILITIES
# =============================
def now_str():
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(msg: str):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"[{now_str()}] [TG ERROR] {e}")

def send_exception_to_telegram(exc: BaseException):
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": f"Futures Bot Crash\n<pre>{text}</pre>", "parse_mode": "HTML"})
    except:
        pass

# CSV log
try:
    with open(LOG_FILE, "x", newline="") as f:
        csv.writer(f).writerow(["Timestamp","Event","OrderID","Entry","Exit","Qty(BTC)","P/L(USDC)","Notes"])
except FileExistsError:
    pass

def log_trade(event, order_id=None, entry=0, exit_p=0, qty=0, profit=0, notes=""):
    ts = now_str()
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([ts, event, order_id or "", f"{entry:.2f}", f"{exit_p:.2f}", f"{qty:.5f}", f"{profit:+.2f}", notes])
    print(f"[{ts}] [LOG] {event} {notes}")

# =============================
# INIT KLINES
# =============================
def initialize_klines():
    global klines_history
    print(f"[{now_str()}] [INIT] Loading {KL_HISTORY_LIMIT} {TIMEFRAME} klines for {SYMBOL}...")
    klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=KL_HISTORY_LIMIT)
    klines_history = [float(k[4]) for k in klines]  # close prices
    print(f"[{now_str()}] [INIT] Loaded {len(klines_history)} candles")

# =============================
# CANCEL TIMER FOR LIMIT BUY
# =============================
def start_cancel_timer(order_id: int, seconds: int):
    global cancel_event
    cancel_event = threading.Event()
    def worker():
        for _ in range(seconds):
            if cancel_event.is_set(): return
            time.sleep(1)
        with lock:
            if limit_buy_id == order_id:
                try:
                    client.futures_cancel_order(symbol=SYMBOL, orderId=order_id)
                    send_telegram(f"Cancelled unfilled LONG #{order_id} (timeout)")
                    log_trade("CANCELLED_LONG", order_id, notes="timeout")
                except: pass
                finally:
                    globals().update(limit_buy_id=None, position_open=False)
    threading.Thread(target=worker, daemon=True).start()

# =============================
# PLACE TAKE-PROFIT
# =============================
def place_tp(entry: float):
    tp_price = round(entry * (1 + TP_PCT), PRICE_PRECISION)
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side="SELL",
            type="LIMIT",
            quantity=QUANTITY_CONTRACTS,
            price=str(tp_price),
            timeInForce="GTC"
        )
        globals()['tp_id'] = order["orderId"]
        send_telegram(f"TP placed @ {tp_price} (order {order['orderId']})")
        log_trade("TP_PLACED", order["orderId"], entry=entry, exit_p=tp_price, qty=QUANTITY_BTC)
    except Exception as e:
        send_exception_to_telegram(e)

# =============================
# USER DATA STREAM (execution reports)
# =============================
def user_data_handler(msg):
    global limit_buy_id, tp_id, stoploss_limit_id, entry_price, position_open

    if msg.get("e") != "executionReport": return
    o = msg["o"]
    order_id = o["i"]
    status   = o["X"]
    side     = o["S"]
    qty      = float(o["q"])
    price    = float(o.get("L") or o.get("p") or 0)

    print(f"[{now_str()}] [EXEC] {side} {status} #{order_id} @ {price}")

    with lock:
        # ——— LONG filled ———
        if limit_buy_id and order_id == limit_buy_id and status == "FILLED" and side == "BUY":
            entry_price = price
            position_open = True
            limit_buy_id = None
            if cancel_event: cancel_event.set()
            send_telegram(f"LONG FILLED @ {price} | {QUANTITY_BTC} BTC")
            log_trade("LONG_FILLED", order_id, entry=price, qty=QUANTITY_BTC)
            place_tp(price)

        # ——— TP filled ———
        elif tp_id and order_id == tp_id and status == "FILLED":
            profit = (price - entry_price) * QUANTITY_BTC
            global total_profit_usdc, successful_trades
            total_profit_usdc += profit
            successful_trades += 1
            position_open = False
            send_telegram(f"TP HIT @ {price} → +{profit:.2f} USDC")
            log_trade("TP_FILLED", order_id, entry=entry_price, exit_p=price, qty=QUANTITY_BTC, profit=profit)
            tp_id = None
            entry_price = 0

        # ——— SL limit filled ———
        elif stoploss_limit_id and order_id == stoploss_limit_id and status == "FILLED":
            profit = (price - entry_price) * QUANTITY_BTC
            total_profit_usdc += profit
            send_telegram(f"SL limit filled @ {price} → {profit:+.2f} USDC")
            log_trade("SL_LIMIT_FILLED", order_id, profit=profit)
            cleanup_sl_state()

# =============================
# KLINE HANDLER
# =============================
def kline_handler(msg):
    global klines_history, position_open, entry_price, stoploss_limit_id, stoploss_monitor_attempts

    k = msg["k"]
    if not k["x"]: return  # only closed candles

    close = float(k["c"])
    klines_history.append(close)
    if len(klines_history) > KL_HISTORY_LIMIT:
        klines_history.pop(0)

    if len(klines_history) < EMA_SLOW + 1: return

    df = pd.DataFrame({"close": klines_history})
    df["fast"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["slow"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()

    prev_f, prev_s = df["fast"].iloc[-2], df["slow"].iloc[-2]
    f, s           = df["fast"].iloc[-1], df["slow"].iloc[-1]

    # ——— BUY SIGNAL ———
    if not position_open and prev_f <= prev_s and f > s:
        buy_price = round(close * 0.9995, PRICE_PRECISION)  # tiny discount
        with lock:
            if position_open: return
            try:
                order = client.futures_create_order(
                    symbol=SYMBOL,
                    side="BUY",
                    type="LIMIT",
                    quantity=QUANTITY_CONTRACTS,
                    price=str(buy_price),
                    timeInForce="GTC"
                )
                oid = order["orderId"]
                globals().update(limit_buy_id=oid, position_open=True)
                send_telegram(f"LIMIT LONG @ {buy_price} | {QUANTITY_BTC} BTC (order {oid})")
                log_trade("LIMIT_LONG_PLACED", oid, entry=buy_price, qty=QUANTITY_BTC)
                start_cancel_timer(oid, CANCEL_AFTER)
            except Exception as e:
                send_exception_to_telegram(e)
                position_open = False

    # ——— SL MONITORING ———
    if position_open and entry_price > 0:
        sl_level = entry_price * (1 - SL_PCT)

        # Trigger → place rebound limit sell
        if close <= sl_level and not stoploss_limit_id:
            if tp_id:
                try: client.futures_cancel_order(symbol=SYMBOL, orderId=tp_id)
                except: pass
                globals()['tp_id'] = None

            limit_sell_price = round(close + 20, PRICE_PRECISION)
            try:
                order = client.futures_create_order(
                    symbol=SYMBOL,
                    side="SELL",
                    type="LIMIT",
                    quantity=QUANTITY_CONTRACTS,
                    price=str(limit_sell_price),
                    timeInForce="GTC"
                )
                stoploss_limit_id = order["orderId"]
                stoploss_monitor_attempts = 0
                send_telegram(f"SL triggered → limit sell @ {limit_sell_price}")
                log_trade("SL_LIMIT_PLACED", stoploss_limit_id, exit_p=limit_sell_price)
            except Exception as e:
                send_exception_to_telegram(e)

        # Monitor existing SL limit
        elif stoploss_limit_id:
            stoploss_monitor_attempts += 1
            if stoploss_monitor_attempts >= STOPLOSS_LIMIT_RETRY_MAX:
                # cancel limit + market close
                try: client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                except: pass
                try:
                    market = client.futures_create_order(
                        symbol=SYMBOL,
                        side="SELL",
                        type="MARKET",
                        quantity=QUANTITY_CONTRACTS
                    )
                    exit_price = float(market["fills"][0]["price"])
                    profit = (exit_price - entry_price) * QUANTITY_BTC
                    total_profit_usdc += profit
                    send_telegram(f"MARKET SL @ {exit_price} → {profit:+.2f} USDC")
                    log_trade("SL_MARKET", profit=profit)
                except Exception as e:
                    send_exception_to_telegram(e)
                cleanup_sl_state()

def cleanup_sl_state():
    global stoploss_limit_id, stoploss_monitor_attempts, entry_price, position_open
    stoploss_limit_id = None
    stoploss_monitor_attempts = 0
    entry_price = 0
    position_open = False

# =============================
# HEALTH ENDPOINT
# =============================
@app.route("/health")
def health():
    with lock:
        return jsonify({
            "status": "running",
            "symbol": SYMBOL,
            "size_btc": QUANTITY_BTC,
            "position_open": position_open,
            "entry": entry_price,
            "pnl_usdc": round(total_profit_usdc, 2),
            "trades": total_trades
        })

# =============================
# START BOT
# =============================
def start_bot():
    print(f"[{now_str()}] Starting BTCUSDC Futures EMA Bot – {QUANTITY_BTC} BTC per trade")
    initialize_klines()

    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5001, use_reloader=False), daemon=True).start()

    twm.start()
    twm.start_futures_user_socket(callback=user_data_handler)
    twm.start_futures_socket(callback=kline_handler, symbol=SYMBOL, futures_type="kline", interval=TIMEFRAME)

    send_telegram(f"Futures EMA Bot STARTED\n{SYMBOL} {TIMEFRAME}\nSize: {QUANTITY_BTC} BTC per trade")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")
        twm.stop()

if __name__ == "__main__":
    start_bot()
