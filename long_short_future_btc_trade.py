
#!/usr/bin/env python3
"""
Futures EMA Bot – BTCUSDC Perpetual
→ Uses python-binance ThreadedWebsocketManager with futures_user_socket
→ Works perfectly with current python-binance (1.0.19 / 2.x)
→ No leverage change, no contract conversion needed
"""
# Usage filename.py EMA 0.01 1m

import time
import threading
import requests
import pandas as pd
import csv
import traceback
import sys
from datetime import datetime
import pytz
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

from flask import Flask, jsonify
from binance import ThreadedWebsocketManager
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from key_config import apikey, apisecret, TELEGRAM_TOKEN, CHAT_ID

# =============================
# USER CONFIG
# =============================
SYMBOL = "BTCUSDC"                    # BTC-settled perpetual
QUANTITY_BTC = 0.01                   # ← We pass this directly as quantity!
TIMEFRAME =  "3m"
STRATEGY = "RSI"          # default
TRADE_DIRECTION = "LONG"  # default


STRATEGY = sys.argv[1].upper() if len(sys.argv) > 1 else"RSI"
QUANTITY_BTC = float(sys.argv[2]) if len(sys.argv) > 2 else 0.01
TIMEFRAME =  sys.argv[3] if len(sys.argv) > 3 else"3m"
TRADE_DIRECTION = sys.argv[4].upper() if len(sys.argv) > 4 else "LONG"
EMA_CHECK = sys.argv[5].upper() if len(sys.argv) > 5 else "EMA50"

# Indicator parameters
EMA_FAST = 9
EMA_SLOW = 21
EMA_50 = 50
EMA_100 = 100
EMA_200 = 200

RSI_PERIOD = 7
RSI_OVERSOLD = 22
RSI_OVERBOUGHT = 78

MACD_FAST = 8
MACD_SLOW = 21
MACD_SIGNAL = 5

TP_PCT   = 0.002    # 0.2%
SL_PCT   = 0.005    # 0.5%
if STRATEGY == "MACD":
    TP_PCT   = 0.0023
    SL_PCT   = 0.005
'''
elif STRATEGY == "RSI":
    TP_PCT   = 0.0045
    SL_PCT   = 0.01
'''
CANCEL_AFTER = 10 * 60
KL_HISTORY_LIMIT = 500
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
stop_lossed_trades = 0
klines_history = []
volume_history = []
high_history = []
low_history = []

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

def send_exception_to_telegram(exc):
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": f"Exception:\n{text}"})
    except:
        pass
def log_trade(event, order_id=None, entry=0, exit_p=0, profit=0, notes=""):
    ts = now_str()
    with open(LOG_FILE, "a", newline="") as f:
        csv.writer(f).writerow([ts, event, order_id or "", f"{entry:.2f}", f"{exit_p:.2f}", QUANTITY_BTC, f"{profit:+.2f}", notes])
    print(f"[{ts}] {event}: {notes}")

# =============================
# INIT KLINES
# =============================
def init_klines():
    global klines_history,volume_history,high_history,low_history
    klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=KL_HISTORY_LIMIT)
    klines_history = [float(k[4]) for k in klines]
    volume_history = [float(k[5]) for k in klines]        # ← Volume per candle
    high_history   = [float(k[2]) for k in klines]        # ← Highest price per candle
    low_history    = [float(k[3]) for k in klines]        # ← Lowest price per candle
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
                    send_telegram(f"[{STRATEGY}] Cancelled unfilled {TRADE_DIRECTION} #{order_id}")
                    log_trade("CANCELLED", order_id, notes="timeout")
                except Exception as e:
                    send_exception_to_telegram(e)
                finally:
                    globals().update(limit_buy_id=None, position_open=False)
    threading.Thread(target=worker, daemon=True).start()

# =============================
# PLACE TP
# =============================
def place_tp(entry: float):
    if TRADE_DIRECTION == "LONG":
        tp_price = round(entry * (1 + TP_PCT), PRICE_PRECISION)
        side = "SELL"
    else:  # SHORT
        tp_price = round(entry * (1 - TP_PCT), PRICE_PRECISION)
        side = "BUY"
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side=side,
            type="LIMIT",
            quantity=QUANTITY_BTC,
            price=str(tp_price),
            timeInForce="GTC"
        )
        globals()['tp_id'] = order["orderId"]
        send_telegram(f"[{STRATEGY}] [{TRADE_DIRECTION}] TP placed @ {tp_price}")
        log_trade("TP_PLACED", order["orderId"], entry=entry, exit_p=tp_price, notes=f"{TRADE_DIRECTION} TP")
    except Exception as e:
        print("TP error:", e)
        send_exception_to_telegram(e)


# =============================
# USER DATA HANDLER – FUTURES (executionReport)
# =============================
def user_data_handler(msg):
    global limit_buy_id, tp_id, stoploss_limit_id, stoploss_monitor_attempts
    global entry_price, position_open, total_profit_usdc, successful_trades, last_trade,stop_lossed_trades

    try:
        # Debug: always print raw message first few times
        if not hasattr(user_data_handler, "debug_count"):
            user_data_handler.debug_count = 0
        if user_data_handler.debug_count < 1:
            print(f"[RAW USER MSG] {msg}")
            user_data_handler.debug_count += 1

        # Handle both old and new formats
        if msg.get("e") == "executionReport":
            o = msg["o"]
        elif msg.get("e") == "ORDER_TRADE_UPDATE":
            o = msg["o"]  # new format: order is directly under "o"
        else:
            # Maybe it's outboundAccountPosition, balance, etc.
            print(f"[USER STREAM] Ignored event type: {msg.get('e')}")
            return


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
                    print(f"[{now_str()}] [USER EVENT] {TRADE_DIRECTION} FILLED @ {entry_price} (order {order_id})")
                    send_telegram(f"[{STRATEGY}] [{TRADE_DIRECTION}] FILLED @ {entry_price:.2f} | {QUANTITY_BTC} BTC")
                    
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    position_open = True
                    last_trade = {"type": f"{TRADE_DIRECTION}_FILLED", "order_id": order_id, "entry": entry_price}
                    log_trade(f"{TRADE_DIRECTION}_FILLED", order_id, entry=entry_price, notes="Entry filled")
                    place_tp(entry_price)  # place take-profit

                elif status in ["CANCELED", "EXPIRED", "REJECTED"]:
                    print(f"[{now_str()}] [USER EVENT] Limit BUY {status} #{order_id}")
                    send_telegram(f"Limit {TRADE_DIRECTION} {status} #{order_id}")
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    position_open = False
                    log_trade(f"{TRADE_DIRECTION}_CANCELLED", order_id, notes=f"Status: {status}")

            # ==================================================================
            # 2. TAKE PROFIT (LIMIT SELL)
            # ==================================================================
            elif tp_id is not None and order_id == tp_id:
                if status == "FILLED" or (status == "PARTIALLY_FILLED" and cum_filled_qty >= orig_qty * 0.999):
                    filled_price = last_filled_price if last_filled_price else float(o["p"])
                    # Correct P&L for both directions
                    if TRADE_DIRECTION == "LONG":
                        profit = (filled_price - entry_price) * QUANTITY_BTC
                    else:
                        profit = (entry_price - filled_price) * QUANTITY_BTC
                    total_profit_usdc += profit
                    successful_trades += 1
                    position_open = False

                    print(f"[{now_str()}] [USER EVENT] TP FILLED @ {filled_price}")
                    send_telegram(f"[{STRATEGY}] {TRADE_DIRECTION} {EMA_CHECK}  ====> Taking profit filled @ {filled_price:.2f} → Profit: {profit:+.2f} successful trades: {successful_trades},stop-loss-trades:{stop_lossed_trades}, Total P/L: {total_profit_usdc:+.2f} USDC")
                    log_trade("TP_FILLED", order_id, entry=entry_price, exit_p=filled_price,
                            profit=profit, notes="Take profit")
                    last_trade = {"type": "TP", "entry": entry_price, "exit": filled_price, "profit": profit}
                    tp_id = None
                    entry_price = 0.0

                    # Cancel any pending SL limit if it exists
                    if stoploss_limit_id:
                        try:
                            client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                            send_telegram(f"Canceled SL limit #{stoploss_limit_id} (TP filled)")
                            log_trade("SL_CANCELLED_BY_TP", stoploss_limit_id)
                        except Exception:
                            send_exception_to_telegram
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
                    if TRADE_DIRECTION == "LONG":
                        profit = (filled_price - entry_price) * QUANTITY_BTC
                    else:
                        profit = (entry_price - filled_price) * QUANTITY_BTC

                    total_profit_usdc += profit
                    print(f"[{now_str()}] [USER EVENT] SL LIMIT FILLED @ {filled_price}")
                    send_telegram(f"[{STRATEGY} {TRADE_DIRECTION} {EMA_CHECK}] ====> SL Limit Filled @ {filled_price:.2f} → P/L: {profit:+.2f} USDC, Total P/L: {total_profit_usdc:+.2f} USDC, successful trades: {successful_trades},stop-loss-trades:{stop_lossed_trades} ")
                    log_trade("SL_LIMIT_FILLED", order_id, entry=entry_price, exit_p=filled_price, profit=profit)
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

def is_htf_bullish(timeframe: str = "1h") -> bool:
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval=timeframe, limit=100)
        closes = [float(k[4]) for k in klines]
        ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().iloc[-1]
        return closes[-1] > ema50
    except:
        return True

def is_htf_bearish(timeframe: str = "1h") -> bool:
    try:
        klines = client.futures_klines(symbol=SYMBOL, interval=timeframe, limit=100)
        closes = [float(k[4]) for k in klines]
        ema50 = pd.Series(closes).ewm(span=50, adjust=False).mean().iloc[-1]
        return closes[-1] < ema50
    except:
        return True

# =============================
# UNIVERSAL ENTRY CONDITION (LONG + SHORT)
# =============================
def should_enter(df: pd.DataFrame) -> str:
    global STRATEGY, TRADE_DIRECTION, high_history, low_history,EMA_CHECK
    """Return 'LONG', 'SHORT', or None"""
    if len(df) < 200:
        return None

    close = df["close"]

    if STRATEGY == "RSI":
        EMA_CHECK = "None"
    # === GLOBAL FILTERS (mirrored by direction) ===
    if TRADE_DIRECTION == "LONG":
        if "rsi14" in df.columns and df["rsi14"].iloc[-1] > 70:
            return None
        '''if close.iloc[-1] <= df["ema50"].iloc[-1] or df["ema50"].iloc[-1] <= df["ema200"].iloc[-1]:
            return None'''

        if EMA_CHECK == "EMA50" and close.iloc[-1] <= df["ema50"].iloc[-1]:
            return None
        #if not is_htf_bullish("15m"):
        #    return None
    else:  # SHORT
        if "rsi14" in df.columns and df["rsi14"].iloc[-1] < 30:
            return None
        '''if close.iloc[-1] >= df["ema50"].iloc[-1] or df["ema50"].iloc[-1] >= df["ema200"].iloc[-1]:
            return None'''
        if EMA_CHECK == "EMA50" and close.iloc[-1] >= df["ema50"].iloc[-1]:
            return None
        #if not is_htf_bearish("15m"):
        #    return None

    # === STRATEGY LOGIC ===
    if STRATEGY == "RSI":
        rsi = df["rsi"]
        if TRADE_DIRECTION == "LONG":
            if not (rsi.iloc[-2] <= RSI_OVERSOLD and rsi.iloc[-1] > RSI_OVERSOLD):
                return None
            return "LONG"
        else:
            if not (rsi.iloc[-2] >= RSI_OVERBOUGHT and rsi.iloc[-1] < RSI_OVERBOUGHT):
                return None
            return "SHORT"

    elif STRATEGY == "MACD":
        macd = df["macd_line"]
        signal = df["signal_line"]
        hist = df["macd_hist"]
        if TRADE_DIRECTION == "LONG":
            
            if not (macd.iloc[-2] <= signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]):
                return None
            #if macd.iloc[-1] >= 0:  # optional extra filter
            #    return None
            if not( (macd.iloc[-1] - signal.iloc[-1] > 4.0) or  (signal.iloc[-2] - macd.iloc[-2] > 4.0)
                   or  (signal.iloc[-3] - macd.iloc[-3] > 4.0) or  (signal.iloc[-4] - macd.iloc[-4] > 4.0)):
                send_telegram("MACD crossover detected, but histogram difference too small")
                return None
            macd_was_below_for_several_bars = 0
            target_price = close.iloc[-1] * (1+ TP_PCT) - 100
            previous_high = max(high_history[-25:-1])
            if target_price > previous_high:
                send_telegram(f"Good MACD crossover, current price {close.iloc[-1]},but TP price {target_price} is above recent high {previous_high}")
                return None
            for i in range(2, 16):           # i = 2 → candle -2, i = 7 → candle -7
                if macd.iloc[-i] < signal.iloc[-i]:
                    macd_was_below_for_several_bars += 1
            if  macd_was_below_for_several_bars <= 7:
                send_telegram("Good MACD crossover, but MACD was not below signal for 7 bars out of 15")
                return None
            return "LONG"
        else:
            if not (macd.iloc[-2] >= signal.iloc[-2] and macd.iloc[-1] < signal.iloc[-1]):
                return None
            if not ( (signal.iloc[-1] - macd.iloc[-1] > 4.0) or (macd.iloc[-2] - signal.iloc[-2] > 4.0)
                    or (macd.iloc[-3] - signal.iloc[-3] > 4.0) or (macd.iloc[-4] - signal.iloc[-4] > 4.0)):
                send_telegram("MACD crossover detected, but histogram difference too small")
                return None
            #if macd.iloc[-1] <= 0:
            #    return None
            macd_was_above_for_several_bars = 0
            target_price = close.iloc[-1] * (1 - TP_PCT) + 100
            previous_low = min(low_history[-25:-1])
            if target_price < previous_low:
                send_telegram(f"Good MACD crossover, but TP price {target_price} is below recent low {previous_low}")
                return None
            for i in range(2, 16):           # i = 2 → candle -2, i = 7 → candle -7
                if macd.iloc[-i] > signal.iloc[-i]:
                    macd_was_above_for_several_bars += 1
            if  macd_was_above_for_several_bars <= 7:
                send_telegram("Good MACD crossover, but MACD was not above signal for 7 bars out of 15")
                return None
            return "SHORT"

    elif STRATEGY == "EMA":
        fast = df["fast_ema"]
        slow = df["slow_ema"]
        if TRADE_DIRECTION == "LONG":
            if not (fast.iloc[-4] <= slow.iloc[-4] and fast.iloc[-3] <= slow.iloc[-3] and fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1] > slow.iloc[-1]):
                return None
            if not ((slow.iloc[-5] - fast.iloc[-5] >=10) or (slow.iloc[-4] - fast.iloc[-4] >=10) or (slow.iloc[-3] - fast.iloc[-3] >=10) or (slow.iloc[-2] - fast.iloc[-2] >=10) or (fast.iloc[-1] -slow.iloc[-1] >= 7) ):
                send_telegram("EMA crossover detected, but difference too small")
                return None
            target_price = close.iloc[-1] * (1+ TP_PCT) -100
            previous_high = max(high_history[-25:-1])
            if target_price > previous_high:
                send_telegram(f"Good EMA crossover, but TP price {target_price} is above recent high {previous_high}")
                return None
            '''if slow.iloc[-1] <= df["ema50"].iloc[-1]:
                return False'''
            return "LONG"
        else:
            if not (fast.iloc[-4] >= slow.iloc[-4] and fast.iloc[-3] >= slow.iloc[-3] and fast.iloc[-2] >= slow.iloc[-2] and fast.iloc[-1] < slow.iloc[-1]):
                return None
            if not ( (fast.iloc[-5]-10) >= slow.iloc[-5] or (fast.iloc[-4]-10) >= slow.iloc[-4] or (fast.iloc[-3]-10) >= slow.iloc[-3] or (fast.iloc[-2]-10) >= slow.iloc[-2] or (fast.iloc[-1]+7)  < slow.iloc[-1]):
                return None
            target_price = close.iloc[-1] * (1 - TP_PCT) + 100
            previous_low = min(low_history[-25:-1])
            if target_price < previous_low:
                send_telegram(f"Good EMA crossover, but TP price {target_price} is below recent low {previous_low}")
                return None
            return "SHORT"

    return None

# =============================
# KLINE HANDLER – CLEAN & MODULAR
# =============================
def kline_handler(msg):
    global klines_history, position_open,volume_history, entry_price, high_history, low_history
    global stoploss_limit_id, stoploss_monitor_attempts, tp_id,stop_lossed_trades,limit_buy_id

    # Handle multiplex socket wrapper
    if 'data' in msg:
        k = msg['data'].get('k', {})
    else:
        k = msg.get('k', {})

    if not k or not k.get("x", False):
        return  # Not a closed candle → ignore

    close_price = float(k["c"])
    high_current = float(k["h"])
    low_current = float(k["l"])

    volume_current = float(k["v"])  # volume of this candle
    close_time = k["T"]
    if datetime.now().minute % 5 == 0:
        print(f"[{now_str()}] KLINE CLOSED @ {close_price} | Time: {datetime.fromtimestamp(close_time/1000,tz=pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M:%S')}")

    klines_history.append(close_price)
    volume_history.append(volume_current)
    high_history.append(high_current)
    low_history.append(low_current)
    if len(klines_history) > KL_HISTORY_LIMIT:
        klines_history.pop(0)
        volume_history.pop(0)
        high_history.pop(0)
        low_history.pop(0)

    # Need enough data
    required_len = max(EMA_SLOW, RSI_PERIOD, MACD_SLOW) + 50
    if len(klines_history) < required_len:
        return

    # === Build DataFrame with all indicators ===
    df = pd.DataFrame({
            "close" : klines_history,
            "volume": volume_history,
            "high": high_history,
            "low": low_history
        })
    # Always compute EMA (used in many places)
    df["fast_ema"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["slow_ema"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=EMA_50).ema_indicator()
    df["ema100"] = EMAIndicator(df["close"], window=EMA_100).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=EMA_200).ema_indicator()
    df["rsi"] = RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    df["rsi14"] = RSIIndicator(df["close"], window=14).rsi()
    macd = MACD(df["close"], window_slow=MACD_SLOW, window_fast=MACD_FAST, window_sign=MACD_SIGNAL)
    df["macd_line"] = macd.macd()
    df["signal_line"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    if datetime.now().minute % 5 == 0:
        print(f"[{now_str()}] Latest indicators | Close: {close_price} | "
              f"Fast EMA: {df['fast_ema'].iloc[-1]:.2f} | Slow EMA: {df['slow_ema'].iloc[-1]:.2f} | "
              f"RSI: {df['rsi'].iloc[-1]:.2f} | MACD: {df['macd_line'].iloc[-1]:.2f} | Signal: {df['signal_line'].iloc[-1]:.2f}")

    # === ENTRY ===
    if not position_open:
        direction = should_enter(df)
        if direction:
            side = "BUY" if direction == "LONG" else "SELL"
            price_adj = close_price * (0.9995 if direction == "LONG" else 1.0005)
            limit_price = round(price_adj, PRICE_PRECISION)

            try:
                order = client.futures_create_order(
                    symbol=SYMBOL, side=side, type="LIMIT",
                    quantity=QUANTITY_BTC, price=str(limit_price), timeInForce="GTC"
                )
                order_id = order["orderId"]
                with lock:
                    limit_buy_id = order_id
                    position_open = True
                send_telegram(f"{direction} SIGNAL ({STRATEGY})\nLIMIT {side} @ {limit_price}\nSize: {QUANTITY_BTC} BTC")
                start_cancel_timer(order_id)
            except Exception as e:
                print("Order failed:", e)
                position_open = False
                send_exception_to_telegram(e)

    # === STOP-LOSS LOGIC (unchanged, just cleaned) ===
    if position_open and entry_price:
        # Determine if SL is hit based on direction
        sl_hit = False
        if TRADE_DIRECTION == "LONG":
            if close_price <= entry_price * (1 - SL_PCT):
                sl_hit = True
                sl_side = "SELL"
                sl_price = round(close_price + 20, PRICE_PRECISION)  # slightly above market
        else:  # SHORT
            if close_price >= entry_price * (1 + SL_PCT):
                sl_hit = True
                sl_side = "BUY"
                sl_price = round(close_price - 20, PRICE_PRECISION)  # slightly below market

        if sl_hit and not stoploss_limit_id:
            # Cancel TP if exists
            if tp_id:
                try:
                    client.futures_cancel_order(symbol=SYMBOL, orderId=tp_id)
                    send_telegram("TP cancelled due to SL trigger")
                except Exception as e:
                    send_exception_to_telegram(e)
                tp_id = None

            stop_lossed_trades += 1
            try:
                sl_order = client.futures_create_order(
                    symbol=SYMBOL,
                    side=sl_side,
                    type="LIMIT",
                    quantity=QUANTITY_BTC,
                    price=str(sl_price),
                    timeInForce="GTC"
                )
                stoploss_limit_id = sl_order["orderId"]
                stoploss_monitor_attempts = 0
                send_telegram(
                    f"[{STRATEGY}] SL TRIGGERED ({TRADE_DIRECTION})\n"
                    f"→ Limit {sl_side} @ {sl_price}\n"
                    f"Entry: {entry_price:.2f} | Current: {close_price:.2f}\n"
                    f"SL trades: {stop_lossed_trades}"
                )
            except Exception as e:
                print(f"SL limit order failed ({TRADE_DIRECTION}): {e}")
                send_exception_to_telegram(e)



    # === SL MONITOR & MARKET FALLBACK ===
    if stoploss_limit_id:
        stoploss_monitor_attempts += 1
        if stoploss_monitor_attempts >= STOPLOSS_LIMIT_RETRY_MAX:
            try:
                client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
                send_telegram(f"{STRATEGY} SL limit #{stoploss_limit_id} cancelled after {STOPLOSS_LIMIT_RETRY_MAX} attempts, placing MARKET sell")
            except Exception as e:
                send_exception_to_telegram(e)
            market_side = "SELL" if TRADE_DIRECTION == "LONG" else "BUY"
            try:
                market_order = client.futures_create_order(
                    symbol=SYMBOL, side=market_side, type="MARKET", quantity=QUANTITY_BTC
                )
                fills = market_order.get("fills", [])
                exit_price = float(fills[0]["price"]) if fills else close_price

                if TRADE_DIRECTION == "LONG":
                    profit = (exit_price - entry_price) * QUANTITY_BTC
                else:  # SHORT
                    profit = (entry_price - exit_price) * QUANTITY_BTC
                global total_profit_usdc, EMA_CHECK
                total_profit_usdc += profit

                send_telegram(f"{STRATEGY} {TRADE_DIRECTION} {EMA_CHECK}   MARKET STOP-LOSS @ {exit_price}\nP&L: {profit:+.2f} USDC, Total P/L: {total_profit_usdc:+.2f} USDC, Stop-loss trades: {stop_lossed_trades}, total trades: {successful_trades + stop_lossed_trades}  ")
                log_trade("SL_MARKET", profit=profit, exit_p=exit_price)
                cleanup_sl_state()

            except Exception as e:
                print(f"{STRATEGY} Market SL failed: {e}")
                send_exception_to_telegram(e)

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

def keep_alive_listen_key():
    current_listen_key = None  # Will fetch on first run
    
    while True:
        if current_listen_key is None:
            current_listen_key = client.futures_stream_get_listen_key()
            print(f"[{now_str()}] Fresh listenKey fetched: {current_listen_key[-20:]}...")
        
        time.sleep(1800)  # 30 minutes
        try:
            client.futures_stream_keepalive(listenKey=current_listen_key)
            print(f"[{now_str()}] User stream listenKey renewed")
        except Exception as e:
            print(f"[{now_str()}] Failed to renew listenKey: {e}")
            send_telegram(f"listenKey renewal failed: {e}")
            current_listen_key = None  # Reset → will refetch next loop

def start_bot():
    print(f"[{now_str()}] Starting {SYMBOL} Futures Trading Bot: {STRATEGY} {QUANTITY_BTC} {TIMEFRAME}  ")
    init_klines()

    # Flask (if any)
    #threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5001, use_reloader=False), daemon=True).start()

    twm.start()

    # Start the user data socket
    twm.start_futures_user_socket(callback=user_data_handler)
    # ←←← ADD THIS LINE – this is all you need! ←←←
    threading.Thread(target=keep_alive_listen_key, daemon=True).start()  # ← No args now

    # === KLINE STREAM ===
    stream_name = f"{SYMBOL.lower()}@kline_{TIMEFRAME}"
    twm.start_futures_multiplex_socket(callback=kline_handler, streams=[stream_name])

    send_telegram(f"Futures Bot STARTED\n{STRATEGY} {TRADE_DIRECTION} {SYMBOL} {TIMEFRAME}\nSize: {QUANTITY_BTC} BTC")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"[{now_str()}] Shutting down gracefully...")
        twm.stop()
        time.sleep(2)

if __name__ == "__main__":
    start_bot()
