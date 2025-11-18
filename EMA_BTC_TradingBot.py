#!/usr/bin/env python3
"""
Spot EMA WebSocket Bot (timeframe from argv[1], default 5m)
 - Limit Buy when EMA_FAST crosses above EMA_SLOW
 - TP placed after buy fills (limit sell)
 - Manual SL monitored via candle closes (market sell by default)
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

# -----------------------------
# USER CONFIG (defaults)
# -----------------------------
SYMBOL = "BTCFDUSD"      # Spot symbol
QUANTITY = 0.01          # BTC to buy
TIMEFRAME = sys.argv[1] if len(sys.argv) > 1 else "5m"  # timeframe from argv[1], default "5m"

# EMA parameters
EMA_FAST = 9
EMA_SLOW = 21

TP_PCT = 0.003           # 0.3% TP
SL_PCT = 0.01            # 1.0% SL
CANCEL_AFTER = 10 * 60   # cancel unfilled limit buy after 10 minutes
USE_MARKET_ON_SL = True  # execute MARKET sell on SL
KL_HISTORY_LIMIT = 200   # how many historical klines to fetch at startup

LOG_FILE = "trade_log.csv"
LOCAL_TZ = "America/Los_Angeles"  # for readable timestamps

# -----------------------------
# GLOBAL STATE
# -----------------------------
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)

# order / position tracking
limit_buy_id = None
limit_buy_timestamp = None
cancel_event = None
tp_id = None
entry_price = 0.0
position_open = False  # True if we have an open position or outstanding buy
total_trades = 0
successful_trades = 0
total_profit = 0.0
last_trade = None  # store last trade info dict

# kline history for indicators
klines_history = []

# sync lock
lock = threading.Lock()

# Flask app
app = Flask(__name__)

# -----------------------------
# Utilities
# -----------------------------
def now_str():
    """Return timestamp string in local timezone (LA) if available, else local system time."""
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
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
# Initialize klines (fetch batch history)
# -----------------------------
def timeframe_to_interval(tf: str):
    """
    Map common timeframe strings to Binance Client constants.
    If not matched, default to tf (twm accepts interval strings) or 1m.
    """
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
        klines = client.get_klines(symbol=SYMBOL, interval=interval, limit=limit)
        klines_history = [float(k[4]) for k in klines]  # close prices
        print(f"[{now_str()}] [INIT] Loaded {len(klines_history)} historical closes.")
    except Exception as e:
        print(f"[{now_str()}] [INIT ERROR] {e}")
        send_exception_to_telegram(e)
        klines_history = []

# -----------------------------
# Reconcile open orders on startup
# -----------------------------
def reconcile_open_orders():
    global limit_buy_id, tp_id, position_open, limit_buy_timestamp
    try:
        open_orders = client.get_open_orders(symbol=SYMBOL)
        print(f"[{now_str()}] [RECONCILE] Found {len(open_orders)} open orders at startup")
        for o in open_orders:
            side = o.get("side")
            type_ = o.get("type")
            order_id = o.get("orderId")
            price = float(o.get("price") or 0)
            if side == "BUY" and type_ == "LIMIT":
                limit_buy_id = order_id
                limit_buy_timestamp = time.time()
                position_open = True
                print(f"[{now_str()}] [RECONCILE] Adopted LIMIT BUY {order_id} at {price}")
                send_telegram(f"Adopted existing LIMIT BUY {order_id} @ {price}")
                start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)
            elif side == "SELL" and type_ == "LIMIT":
                tp_id = order_id
                position_open = True
                print(f"[{now_str()}] [RECONCILE] Adopted TP SELL {order_id} at {price}")
                send_telegram(f"Adopted existing TP SELL {order_id} @ {price}")
    except Exception as e:
        print(f"[{now_str()}] [RECONCILE ERROR] {e}")
        send_exception_to_telegram(e)

# -----------------------------
# Cancel timer thread for limit buy
# -----------------------------
def start_limit_buy_cancel_timer(order_id: int, timeout_seconds: int):
    global cancel_event
    cancel_event = threading.Event()

    def worker():
        print(f"[{now_str()}] [CANCEL-TIMER] Started for order {order_id}. Timeout {timeout_seconds}s")
        waited = 0
        while waited < timeout_seconds:
            if cancel_event.is_set():
                print(f"[{now_str()}] [CANCEL-TIMER] Event set - not canceling {order_id}")
                return
            time.sleep(1)
            waited += 1
        # timed out -> cancel if still outstanding
        with lock:
            global limit_buy_id, limit_buy_timestamp, position_open
            if limit_buy_id == order_id:
                try:
                    print(f"[{now_str()}] [CANCEL-TIMER] Canceling unfilled limit buy {order_id} ...")
                    client.cancel_order(symbol=SYMBOL, orderId=order_id)
                    send_telegram(f" Cancelled unfilled limit buy {order_id} after {timeout_seconds//60} minutes")
                    log_trade("CANCELLED_UNFILLED_BUY", order_id, notes=f"Timed out {timeout_seconds}s")
                except Exception as e:
                    print(f"[{now_str()}] [CANCEL-TIMER ERROR] {e}")
                    send_exception_to_telegram(e)
                finally:
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    position_open = False
        print(f"[{now_str()}] [CANCEL-TIMER] Worker exiting for {order_id}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return cancel_event

# -----------------------------
# Place take-profit (limit sell)
# -----------------------------
def place_take_profit(filled_entry_price: float):
    global tp_id, total_trades, last_trade
    tp_price = round(filled_entry_price * (1 + TP_PCT), 2)
    try:
        print(f"[{now_str()}] [TP] Placing TP limit sell at {tp_price} ...")
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
        last_trade = {"type": "TP_PLACED", "order_id": tp_id, "entry": filled_entry_price, "tp": tp_price}
        print(f"[{now_str()}] [TP] TP order placed. orderId={tp_id}, TP={tp_price}")
        send_telegram(f" TP placed at {tp_price}, orderId={tp_id}")
        log_trade("TP_PLACED", tp_id, entry=filled_entry_price, exit_price=tp_price, quantity=QUANTITY, profit=0.0, notes="TP placed after buy fill")
    except Exception as e:
        print(f"[{now_str()}] [TP ERROR] Failed to place TP: {e}")
        send_exception_to_telegram(e)
        send_telegram(f" Failed to place TP: {e}")

# -----------------------------
# Manual stop-loss execution
# -----------------------------
def execute_manual_sl(current_price: float):
    global tp_id, position_open, entry_price, total_profit, last_trade, limit_buy_id, limit_buy_timestamp
    try:
        print(f"[{now_str()}] [SL] Manual SL triggered at {current_price}. Exiting position.")
        send_telegram(f"Manual SL triggered at {current_price}. Exiting position.")
        if tp_id:
            try:
                client.cancel_order(symbol=SYMBOL, orderId=tp_id)
                print(f"[{now_str()}] [SL] Canceled TP order {tp_id}")
            except Exception as e:
                print(f"[{now_str()}] [SL] Error canceling TP {tp_id}: {e}")

        if USE_MARKET_ON_SL:
            sell = client.order_market_sell(symbol=SYMBOL, quantity=QUANTITY)
            fills = sell.get("fills", [])
            executed_price = float(fills[0]["price"]) if fills else current_price
            print(f"[{now_str()}] [SL] MARKET sell executed at {executed_price}")
        else:
            executed_price = round(current_price+20, 2)
            sell = client.create_order(symbol=SYMBOL, side="SELL", type="LIMIT", quantity=QUANTITY, price=str(executed_price), timeInForce="GTC")
            print(f"[{now_str()}] [SL] LIMIT sell order placed: {sell}")

        profit = (executed_price - entry_price) * QUANTITY
        total_profit += profit
        print(f"[{now_str()}] [SL] Position closed. P/L: {profit:.8f}")
        send_telegram(f"Stop Loss executed at {executed_price}. P/L: {profit:.8f} USDT")
        log_trade("SL", None, entry=entry_price, exit_price=executed_price, quantity=QUANTITY, profit=profit, notes="Manual SL")
        last_trade = {"type": "SL", "entry": entry_price, "exit": executed_price, "profit": profit}
    except Exception as e:
        print(f"[{now_str()}] [SL ERROR] {e}")
        send_exception_to_telegram(e)
    finally:
        with lock:
            limit_buy_id = None
            limit_buy_timestamp = None
            tp_id = None
            entry_price = 0.0
            position_open = False

# -----------------------------
# User data handler (executionReport)
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, limit_buy_timestamp, cancel_event, tp_id, entry_price, position_open, successful_trades, total_profit, last_trade
    try:
        if msg.get("e") != "executionReport":
            return

        order_id = int(msg.get("i", 0))
        status = msg.get("X")
        last_filled_price = float(msg.get("L", 0)) if msg.get("L") else 0.0
        print(f"[{now_str()}] [USER EVENT] orderId={order_id}, status={status}, lastPrice={last_filled_price}")

        with lock:
            # Handle limit buy outcomes
            if limit_buy_id is not None and order_id == limit_buy_id:
                if status == "FILLED":
                    entry_price = last_filled_price
                    print(f"[{now_str()}] [USER EVENT] Limit BUY FILLED at {entry_price} (order {order_id})")
                    send_telegram(f"Limit Buy FILLED at {entry_price} (order {order_id})")
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    place_take_profit(entry_price)
                    position_open = True
                    last_trade = {"type": "BUY_FILLED", "order_id": order_id, "entry": entry_price}
                    log_trade("BUY_FILLED", order_id, entry=entry_price, exit_price=0.0, quantity=QUANTITY, profit=0.0, notes="Limit buy filled")
                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    print(f"[{now_str()}] [USER EVENT] Limit BUY {order_id} was {status}. Clearing state.")
                    send_telegram(f"Limit Buy {order_id} {status}.")
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    limit_buy_timestamp = None
                    position_open = False
                    log_trade("BUY_CANCELLED", order_id, notes=f"Limit buy {status}")

            # Handle TP outcomes
            elif tp_id is not None and order_id == tp_id:
                if status == "FILLED":
                    filled_price = last_filled_price
                    profit = (filled_price - entry_price) * QUANTITY
                    total_profit += profit
                    successful_trades += 1
                    position_open = False
                    print(f"[{now_str()}] [USER EVENT] TP FILLED at {filled_price} (order {order_id})")
                    send_telegram(f"TP FILLED at {filled_price}. Profit: {profit:.8f} USDT")
                    log_trade("TP", order_id, entry=entry_price, exit_price=filled_price, quantity=QUANTITY, profit=profit, notes="TP hit")
                    last_trade = {"type": "TP", "entry": entry_price, "exit": filled_price, "profit": profit}
                    tp_id = None
                    entry_price = 0.0
                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    print(f"[{now_str()}] [USER EVENT] TP order {order_id} was {status}. Clearing state.")
                    send_telegram(f"TP order {order_id} {status}.")
                    tp_id = None
                    log_trade("TP_CANCELLED", order_id, notes=f"TP {status}")
    except Exception as e:
        print(f"[{now_str()}] [USER HANDLER ERROR] {e}")
        send_exception_to_telegram(e)

# -----------------------------
# Kline handler: compute EMAs and trigger buy on crossover
# -----------------------------
def kline_handler(msg):
    global klines_history, limit_buy_id, limit_buy_timestamp, position_open, entry_price, tp_id, cancel_event
    try:
        k = msg.get('k', {})
        if not k:
            return

        is_closed = k.get('x', False)
        close_price = float(k.get('c', 0))
        open_price = float(k.get('o', 0))

        if not is_closed:
            return

        # Append closed candle price
        klines_history.append(close_price)
        if len(klines_history) > KL_HISTORY_LIMIT:
            klines_history.pop(0)

        # Need at least EMA_SLOW + 1 candles for a valid previous and current EMA
        if len(klines_history) >= EMA_SLOW + 1:
            df = pd.DataFrame({'close': klines_history})

            # Compute EMAs
            df["ema_fast"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
            df["ema_slow"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()

            # previous (second-last) and current (last)
            ema_fast_prev = df["ema_fast"].iloc[-2]
            ema_slow_prev = df["ema_slow"].iloc[-2]

            ema_fast_now  = df["ema_fast"].iloc[-1]
            ema_slow_now  = df["ema_slow"].iloc[-1]

            print(f"[{now_str()}] [KLINE] Close={close_price:.2f}, "
                  f"EMA_fast_prev={ema_fast_prev:.2f}, EMA_slow_prev={ema_slow_prev:.2f}, "
                  f"EMA_fast_now={ema_fast_now:.2f}, EMA_slow_now={ema_slow_now:.2f}")

            # ============================================================
            # BUY CONDITION: EMA FAST crosses above EMA SLOW
            # ============================================================
            buy_signal = (
                (ema_fast_prev < ema_slow_prev) and
                (ema_fast_now  >= ema_slow_now)
            )

            if buy_signal:
                buy_reason = "EMA fast/slow bullish crossover"

                with lock:
                    if not position_open:
                        # place a limit buy a small amount below close (keeps behavior consistent with original)
                        buy_price = round(close_price - 30, 2)
                        try:
                            print(f"[{now_str()}] [ORDER] {buy_reason}: Placing LIMIT BUY at {buy_price}")
                            send_telegram(f"BUY SIGNAL: {buy_reason}. Placing LIMIT BUY at {buy_price}")

                            order = client.create_order(
                                symbol=SYMBOL,
                                side="BUY",
                                type="LIMIT",
                                quantity=QUANTITY,
                                price=str(buy_price),
                                timeInForce="GTC"
                            )

                            limit_buy_id = order.get("orderId")
                            limit_buy_timestamp = time.time()
                            position_open = True

                            print(f"[{now_str()}] [ORDER] LIMIT BUY placed orderId={limit_buy_id} at {buy_price}")
                            log_trade("BUY_PLACED", limit_buy_id, entry=buy_price, exit_price=0.0,
                                      quantity=QUANTITY, profit=0.0,
                                      notes=buy_reason)

                            start_limit_buy_cancel_timer(limit_buy_id, CANCEL_AFTER)

                        except Exception as e:
                            print(f"[{now_str()}] [ORDER ERROR] Failed to place limit buy: {e}")
                            send_exception_to_telegram(e)
                            limit_buy_id = None
                            limit_buy_timestamp = None
                            position_open = False

            # ============================================================
            # SL check (unchanged)
            # ============================================================
            if position_open and entry_price > 0:
                sl_trigger = entry_price * (1 - SL_PCT)
                if close_price <= sl_trigger:
                    print(f"[{now_str()}] [SL] Close {close_price} <= {sl_trigger}. Triggering manual SL.")
                    execute_manual_sl(close_price)

    except Exception as e:
        print(f"[{now_str()}] [KLINE ERROR] {e}")
        send_exception_to_telegram(e)

# -----------------------------
# Flask /health endpoint
# -----------------------------
@app.route("/health", methods=["GET"])
def health():
    with lock:
        return jsonify({
            "status": "running",
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "position_open": position_open,
            "entry_price": entry_price,
            "limit_buy_id": limit_buy_id,
            "tp_id": tp_id,
            "total_trades": total_trades,
            "successful_trades": successful_trades,
            "total_profit": total_profit,
            "last_trade": last_trade
        })

def start_flask():
    # disable Flask debug reloader when running in thread
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)

# -----------------------------
# Start bot
# -----------------------------
def start_bot():
    print(f"[{now_str()}] [BOT] Initializing... (TIMEFRAME={TIMEFRAME}, EMA_FAST={EMA_FAST}, EMA_SLOW={EMA_SLOW})")
    initialize_klines_history(limit=KL_HISTORY_LIMIT)
    # reconcile_open_orders()  # optional: uncomment if you want to adopt existing orders at startup

    # start Flask in separate thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    print(f"[{now_str()}] [BOT] Flask /health endpoint started on port 5000")

    # start websockets
    twm.start()
    twm.start_user_socket(callback=user_data_handler)
    print(f"[{now_str()}] [BOT] User data WebSocket started")
    # Start kline socket for timeframe
    twm.start_kline_socket(symbol=SYMBOL.lower(), interval=TIMEFRAME, callback=kline_handler)
    print(f"[{now_str()}] [BOT] Kline WebSocket started ({TIMEFRAME})")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"[{now_str()}] [BOT] Shutting down...")
        try:
            twm.stop()
        except:
            pass

# -----------------------------
# Entry
# -----------------------------
if __name__ == "__main__":
    try:
        start_bot()
    except Exception as e:
        print(f"[{now_str()}] [MAIN ERROR] {e}")
        send_exception_to_telegram(e)
        raise
