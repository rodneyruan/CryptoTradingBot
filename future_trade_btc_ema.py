#!/usr/bin/env python3
"""
Futures EMA Bot – BTCUSDC Perpetual
→ quantity = 0.05 means 0.05 BTC (NOT contracts)
→ Works perfectly with current python-binance (1.0.19 / 2.x)
→ No leverage change, no contract conversion needed
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
SYMBOL = "BTCUSDC"                    # BTC-settled perpetual
QUANTITY_BTC = 0.01                   # ← We pass this directly as quantity!
TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "1m"

EMA_FAST = 9
EMA_SLOW = 21
TP_PCT   = 0.002      # 0.2%
SL_PCT   = 0.01       # 1.0%
CANCEL_AFTER = 10 * 60
KL_HISTORY_LIMIT = 200
STOPLOSS_LIMIT_RETRY_MAX = 5
LOG_FILE = "futures_btcusdc_log.csv"
LOCAL_TZ = "America/Los_Angeles"

# =============================
# GLOBALS
# =============================
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)

# Get price precision once
info = client.futures_exchange_info()
symbol_info = [s for s in info["symbols"] if s["symbol"] == SYMBOL][0]
PRICE_PRECISION = symbol_info["pricePrecision"]

# State
limit_buy_id = None
cancel_event = None
tp_id = None
stoploss_limit_id = None
stoploss_monitor_attempts = 0
entry_price = 0.0
position_open = False
total_profit_usdc = 0.0
successful_trades = 0
klines_history = []
lock = threading.Lock()
app = Flask(__name__)

# =============================
# UTILS
# =============================
def now_str():
    if ZoneInfo:
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def send_telegram(msg: str):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except: pass

def log_trade(event, order_id=None, entry=0, exit_p=0, profit=0, notes=""):
    ts = now_str()
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([ts, event, order_id or "", f"{entry:.2f}", f"{exit_p:.2f}", QUANTITY_BTC, f"{profit:+.2f}", notes])
    print(f"[{ts}] {event}: {notes}")

# =============================
# INIT KLINES
# =============================
def init_klines():
    global klines_history
    klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=KL_HISTORY_LIMIT)
    klines_history = [float(k[4]) for k in klines]
    print(f"[{now_str()}] Loaded {len(klines_history)} klines")

# =============================
# CANCEL TIMER
# =============================
def start_cancel_timer(order_id: int):
    global cancel_event
    cancel_event = threading.Event()
    def worker():
        time.sleep(CANCEL_AFTER)
        if cancel_event.is_set(): return
        with lock:
            if limit_buy_id == order_id:
                try:
                    client.futures_cancel_order(symbol=SYMBOL, orderId=order_id)
                    send_telegram(f"Cancelled unfilled LONG #{order_id}")
                    log_trade("CANCELLED", order_id, notes="timeout")
                except: pass
                finally:
                    globals().update(limit_buy_id=None, position_open=False)
    threading.Thread(target=worker, daemon=True).start()

# =============================
# PLACE TP
# =============================
def place_tp(entry: float):
    tp_price = round(entry * (1 + TP_PCT), PRICE_PRECISION)
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side="SELL",
            type="LIMIT",
            quantity=QUANTITY_BTC,           # ← BTC amount directly
            price=str(tp_price),
            timeInForce="GTC"
        )
        globals()['tp_id'] = order["orderId"]
        send_telegram(f"TP placed @ {tp_price}")
        log_trade("TP_PLACED", order["orderId"], entry=entry, exit_p=tp_price)
    except Exception as e:
        print("TP error:", e)


# =============================
# USER DATA HANDLER – FUTURES (executionReport)
# =============================
def user_data_handler(msg):
    global limit_buy_id, tp_id, stoploss_limit_id, stoploss_monitor_attempts
    global entry_price, position_open, total_profit_usdc, successful_trades, last_trade

    try:
        # Futures user stream wraps executionReport inside "o"
        if msg.get("e") != "executionReport":
            return

        o = msg["o"]  # order data is under "o"
        order_id = int(o["i"])
        status = o["X"]                    # NEW, FILLED, CANCELED, EXPIRED, etc.
        side = o["S"]                      # BUY or SELL
        symbol = o["s"]                    # e.g. "BTCUSDC"

        # Last executed price in this update (0 if no fill yet)
        last_filled_price = float(o.get("L") or 0)
        # Cumulative filled quantity (in BTC for BTCUSDC)
        cum_filled_qty = float(o["z"])
        orig_qty = float(o["q"])

        print(f"[{now_str()}] [USER EVENT] {side} {status} #{order_id} | "
              f"filled: {cum_filled_qty}/{orig_qty} @ {last_filled_price or 'N/A'}")

        with lock:
            # ==================================================================
            # 1. LIMIT BUY (ENTRY)
            # ==================================================================
            if limit_buy_id is not None and order_id == limit_buy_id:
                if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_filled_qty >= orig_qty * 0.999):
                    entry_price = last_filled_price if last_filled_price else float(o["p"])  # fallback to order price
                    print(f"[{now_str()}] [USER EVENT] LONG FILLED @ {entry_price} (order {order_id})")
                    send_telegram(f"LONG FILLED @ {entry_price:.2f} | {QUANTITY_BTC} BTC")
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    position_open = True
                    last_trade = {"type": "LONG_FILLED", "order_id": order_id, "entry": entry_price}
                    log_trade("LONG_FILLED", order_id, entry=entry_price, qty=QUANTITY_BTC, notes="Entry filled")
                    place_tp(entry_price)  # place take-profit

                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    print(f"[{now_str()}] [USER EVENT] Limit BUY {status} #{order_id}")
                    send_telegram(f"Limit LONG {status} #{order_id}")
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    position_open = False
                    log_trade("LONG_CANCELLED", order_id, notes=f"Status: {status}")

            # ==================================================================
            # 2. TAKE PROFIT (LIMIT SELL)
            # ==================================================================
            elif tp_id is not None and order_id == tp_id:
                if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_filled_qty >= orig_qty * 0.999):
                    filled_price = last_filled_price if last_filled_price else float(o["p"])
                    profit = (filled_price - entry_price) * QUANTITY_BTC
                    total_profit_usdc += profit
                    successful_trades += 1
                    position_open = False

                    print(f"[{now_str()}] [USER EVENT] TP FILLED @ {filled_price}")
                    send_telegram(f"TP HIT @ {filled_price:.2f} → Profit: {profit:+.2f} USDC")
                    log_trade("TP_FILLED", order_id, entry=entry_price, exit_p=filled_price,
                              qty=QUANTITY_BTC, profit=profit, notes="Take profit")
                    last_trade = {"type": "TP", "entry": entry_price, "exit": filled_price, "profit": profit}
                    tp_id = None
                    entry_price = 0.0

                    # Cancel any pending SL limit if it exists
                    if stoploss_limit_id:
                        try:
                            client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                            send_telegram(f"Canceled SL limit #{stoploss_limit_id} (TP filled)")
                            log_trade("SL_CANCELLED_BY_TP", stoploss_limit_id)
                        except:
                            pass
                        finally:
                            stoploss_limit_id = None
                            stoploss_monitor_attempts = 0

                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    print(f"[{now_str()}] [USER EVENT] TP order {status} #{order_id}")
                    send_telegram(f"TP order {status} #{order_id}")
                    tp_id = None
                    log_trade("TP_CANCELLED", order_id, notes=status)

            # ==================================================================
            # 3. STOP-LOSS REBOUND LIMIT SELL
            # ==================================================================
            elif stoploss_limit_id is not None and order_id == stoploss_limit_id:
                if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_filled_qty >= orig_qty * 0.999):
                    filled_price = last_filled_price if last_filled_price else float(o["p"])
                    profit = (filled_price - entry_price) * QUANTITY_BTC
                    total_profit_usdc += profit
                    print(f"[{now_str()}] [USER EVENT] SL LIMIT FILLED @ {filled_price}")
                    send_telegram(f"SL Limit Filled @ {filled_price:.2f} → P/L: {profit:+.2f} USDC")
                    log_trade("SL_LIMIT_FILLED", order_id, entry=entry_price, exit_p=filled_price,
                              qty=QUANTITY_BTC, profit=profit)
                    last_trade = {"type": "SL_LIMIT", "profit": profit}
                    cleanup_sl_state()

                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    print(f"[{now_str()}] [USER EVENT] SL limit {status} #{order_id}")
                    send_telegram(f"SL limit order {status} #{order_id}")
                    log_trade("SL_LIMIT_CANCELLED", order_id, notes=status)
                    # Don't reset position_open here — kline handler will trigger market sell
                    stoploss_limit_id = None
                    stoploss_monitor_attempts = 0

    except Exception as e:
        print(f"[{now_str()}] [USER HANDLER ERROR] {e}")
        send_exception_to_telegram(e)


# Helper to reset SL state (used in kline handler too)
def cleanup_sl_state():
    global stoploss_limit_id, stoploss_monitor_attempts, entry_price, position_open
    stoploss_limit_id = None
    stoploss_monitor_attempts = 0
    entry_price = 0.0
    position_open = False

# =============================
# KLINE HANDLER (FIXED FOR MULTIPLEX)
# =============================
def kline_handler(msg):
    global klines_history, position_open, entry_price, stoploss_limit_id, stoploss_monitor_attempts

    # ← FIX: Handle multiplex wrapper ('data' key)
    if 'data' in msg:
        inner_msg = msg['data']
    else:
        inner_msg = msg  # fallback for non-multiplex

    k = inner_msg.get('k')
    if not k: return  # invalid message
    if not k["x"]: return  # only closed candles
    close = float(k["c"])

    # ← TEMP DEBUG: Print first 3 messages to verify format (remove after testing)
    if len(klines_history) < 3:
        print(f"[{now_str()}] [DEBUG] Raw msg: {msg}")
        print(f"[{now_str()}] [DEBUG] Inner: {inner_msg}")
        print(f"[{now_str()}] [DEBUG] K: {k}")

    klines_history.append(close)
    if len(klines_history) > KL_HISTORY_LIMIT:
        klines_history.pop(0)

    if len(klines_history) < EMA_SLOW + 1: return

    df = pd.DataFrame({"close": klines_history})
    df["fast"] = EMAIndicator(df["close"], EMA_FAST).ema_indicator()
    df["slow"] = EMAIndicator(df["close"], EMA_SLOW).ema_indicator()

    if df["fast"].iloc[-2] <= df["slow"].iloc[-2] and df["fast"].iloc[-1] > df["slow"].iloc[-1]:
        if position_open: return

        buy_price = round(close * 0.9995, PRICE_PRECISION)
        try:
            order = client.futures_create_order(
                symbol=SYMBOL,
                side="BUY",
                type="LIMIT",
                quantity=QUANTITY_BTC,        # ← BTC amount directly
                price=str(buy_price),
                timeInForce="GTC"
            )
            oid = order["orderId"]
            with lock:
                globals().update(limit_buy_id=oid, position_open=True)
            send_telegram(f"Buy signal detected, Placed LIMIT LONG @ {buy_price} | {QUANTITY_BTC} BTC")
            log_trade("LONG_PLACED", oid, entry=buy_price)
            start_cancel_timer(oid)
        except Exception as e:
            print("Buy error:", e)
            position_open = False

    # SL monitoring
    if position_open and entry_price and close <= entry_price * (1 - SL_PCT) and not stoploss_limit_id:
        if tp_id:
            try: client.futures_cancel_order(symbol=SYMBOL, orderId=tp_id)
            except: pass
            globals()['tp_id'] = None

        limit_sell = round(close + 20, PRICE_PRECISION)
        try:
            order = client.futures_create_order(
                symbol=SYMBOL,
                side="SELL",
                type="LIMIT",
                quantity=QUANTITY_BTC,
                price=str(limit_sell),
                timeInForce="GTC"
            )
            stoploss_limit_id = order["orderId"]
            stoploss_monitor_attempts = 0
            send_telegram(f"Stop loss triggered, SL → limit sell @ {limit_sell}")
        except Exception as e:
            print("SL limit error:", e)

    if stoploss_limit_id:
        stoploss_monitor_attempts += 1
        if stoploss_monitor_attempts >= STOPLOSS_LIMIT_RETRY_MAX:
            try: client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
            except: pass
            try:
                market = client.futures_create_order(symbol=SYMBOL, side="SELL", type="MARKET", quantity=QUANTITY_BTC)
                exit_price = float(market["fills"][0]["price"])
                profit = (exit_price - entry_price) * QUANTITY_BTC
                total_profit_usdc += profit
                send_telegram(f"SL didnet filled, MARKET SL @ {exit_price} → {profit:+.2f}")
                log_trade("SL_MARKET", profit=profit)
            except Exception as e:
                print("Market SL error:", e)
            cleanup_sl_state()

# =============================
# HEALTH & START
# =============================
@app.route("/health")
def health():
    with lock:
        return jsonify({
            "status": "running",
            "symbol": SYMBOL,
            "size_btc": QUANTITY_BTC,
            "position": position_open,
            "entry": entry_price,
            "pnl_usdc": round(total_profit_usdc, 2)
        })

def start_bot():
    print(f"[{now_str()}] Starting BTCUSDC Futures EMA Bot – {QUANTITY_BTC} BTC per trade")
    init_klines()

    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5001, use_reloader=False), daemon=True).start()

    twm.start()
    twm.start_futures_user_socket(callback=user_data_handler)
    
    # ← FIXED: Use multiplex for symbol-specific futures klines
    stream_name = f"{SYMBOL.lower()}@kline_{TIMEFRAME}"
    twm.start_futures_multiplex_socket(callback=kline_handler, streams=[stream_name])

    send_telegram(f"Futures EMA Bot STARTED\n{SYMBOL} {TIMEFRAME}\nSize: {QUANTITY_BTC} BTC")

    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        twm.stop()

if __name__ == "__main__":
    start_bot()
