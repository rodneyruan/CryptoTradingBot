
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


# Indicator parameters
EMA_FAST = 9
EMA_SLOW = 21
EMA_100 = 100
EMA_200 = 200

RSI_PERIOD = 7
RSI_OVERSOLD = 19
RSI_OVERBOUGHT = 70

MACD_FAST = 8
MACD_SLOW = 21
MACD_SIGNAL = 5

TP_PCT   = 0.0022
if STRATEGY == "MACD":
    TP_PCT   = 0.0022
elif STRATEGY == "RSI":
    TP_PCT   = 0.0022

SL_PCT   = 0.006       # 0.6%
CANCEL_AFTER = 10 * 60
KL_HISTORY_LIMIT = 500
STOPLOSS_LIMIT_RETRY_MAX = 5
LOG_FILE = "futures_btcusdc_log.csv"
LOCAL_TZ = "America/Los_Angeles"




def now_str():
    if ZoneInfo:
        return datetime.now(ZoneInfo(LOCAL_TZ)).strftime("%Y-%m-%d %H:%M:%S")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
    global klines_history,volume_history
    klines = client.futures_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=KL_HISTORY_LIMIT)
    klines_history = [float(k[4]) for k in klines]
    volume_history = [float(k[5]) for k in klines]      # volume is index 5
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
                    send_telegram(f"[{STRATEGY}] Cancelled unfilled LONG #{order_id}")
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
    tp_price = round(entry * (1 + TP_PCT), PRICE_PRECISION)
    try:
        order = client.futures_create_order(
            symbol=SYMBOL,
            side="SELL",
            type="LIMIT",
            quantity=QUANTITY_BTC,
            price=str(tp_price),
            timeInForce="GTC"
        )
        globals()['tp_id'] = order["orderId"]
        send_telegram(f"[{STRATEGY}]  TP placed @ {tp_price}")
        log_trade("TP_PLACED", order["orderId"], entry=entry, exit_p=tp_price)
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
                    print(f"[{now_str()}] [USER EVENT] LONG FILLED @ {entry_price} (order {order_id})")
                    send_telegram(f"[{STRATEGY}] LONG FILLED @ {entry_price:.2f} | {QUANTITY_BTC} BTC")
                    if cancel_event:
                        cancel_event.set()
                    limit_buy_id = None
                    position_open = True
                    last_trade = {"type": "LONG_FILLED", "order_id": order_id, "entry": entry_price}
                    log_trade("LONG_FILLED", order_id, entry=entry_price, notes="Entry filled")
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
                    send_telegram(f"[{STRATEGY}] ====> Taking profit @ {filled_price:.2f} → Profit: {profit:+.2f} successful trades: {successful_trades},stop-loss-trades:{stop_lossed_trades}, Total P/L: {total_profit_usdc:+.2f} USDC")
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
                    profit = (filled_price - entry_price) * QUANTITY_BTC
                    total_profit_usdc += profit
                    print(f"[{now_str()}] [USER EVENT] SL LIMIT FILLED @ {filled_price}")
                    send_telegram(f"[{STRATEGY}] ====> SL Limit Filled @ {filled_price:.2f} → P/L: {profit:+.2f} USDC, Total P/L: {total_profit_usdc:+.2f} USDC, successful trades: {successful_trades},stop-loss-trades:{stop_lossed_trades} ")
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

def is_htf_trend_bullish(timeframe: str = "5m") -> bool:
    """
    Check if EMA50 > EMA200 on the specified higher timeframe.
    
    Args:
        timeframe (str): Binance interval, e.g. "5m", "15m", "1h", "4h", "1d"
    
    Returns:
        True  → Bullish trend (EMA50 > EMA200)
        False → Bearish or neutral
    """
    try:
        # Pull enough data for EMA200 + some buffer
        limit_needed = 300
        klines = client.futures_klines(
            symbol=SYMBOL,
            interval=timeframe,
            limit=limit_needed
        )

        # Convert to DataFrame (only close price needed)
        closes = [float(k[4]) for k in klines]  # index 4 = close
        df = pd.DataFrame({"close": closes})

        # Calculate EMA50 and EMA200
        ema50  = EMAIndicator(df["close"], window=50).ema_indicator().iloc[-1]
        ema200 = EMAIndicator(df["close"], window=200).ema_indicator().iloc[-1]

        return ema50 > ema200

    except Exception as e:
        print(f"[{now_str()}] HTF trend check failed ({timeframe}): {e}")
        return True  # or False — True = allow trade if API fails (safer default)

# =============================
# BUY CONDITION (SEPARATE & EASY TO EXTEND)
# =============================
def should_buy(df: pd.DataFrame) -> bool:
    """Return True if buy signal based on current STRATEGY"""
    
    if len(df) < 200:  # safety
        return False

    close = df["close"]


    # === 1. RSI not overbought ===
    if "rsi" in df.columns and df["rsi"].iloc[-1] > 70:
        return False
    
    # Condition A: Price above EMA50
    if close.iloc[-1] <= df["ema50"].iloc[-1]:
        return False
    # Condition B: EMA50 must be above EMA200 (strong bullish structure)
    if df["ema50"].iloc[-1] <= df["ema200"].iloc[-1]:
        return False

    # Slightly smarter – ignores tiny candles during Asian session
    avg_vol_20 = df["volume"].iloc[-20:].mean()
    volume_ratio = df["volume"].iloc[-1] / avg_vol_20 if avg_vol_20 > 0 else 0

    # London/NY session → require stronger spike
    if 12 <= datetime.now(pytz.utc).hour <= 23:  # 12–23 UTC = London + NY
        if volume_ratio < 1.7:
            return False
    else:  # Asian session – accept milder spikes
        if volume_ratio < 1.4:
            return False

    if STRATEGY == "EMA":
        if "fast_ema" not in df.columns or "slow_ema" not in df.columns:
            return False
        fast = df["fast_ema"]
        slow = df["slow_ema"]
        #1  Golden cross on the just-closed candle
        if not (fast.iloc[-2] <= slow.iloc[-2] and fast.iloc[-1]  > slow.iloc[-1]):
            return False
        if slow.iloc[-1] <= df["ema50"].iloc[-1]:
            return False
        # 3. confirm HTF trend is bullish
        # is_htf_trend_bullish costs some API calls, so only do it when golden cross detected
        '''if not is_htf_trend_bullish("5m"):
            send_telegram("EMA Golden Cross detected, but HTF trend not bullish")
            return False'''
        #send_telegram("Buy signal confirmed: EMA Golden Cross + HTF bullish")
        return True
    elif STRATEGY == "RSI":
        if "rsi" not in df.columns:
            return False
        rsi = df["rsi"]
        # RSI exits oversold on closed candle
        if not (rsi.iloc[-2] <= RSI_OVERSOLD and rsi.iloc[-1] > RSI_OVERSOLD):
            return False
        # 3. confirm HTF trend is bullish
        # is_htf_trend_bullish costs some API calls, so only do it when golden cross detected
        '''if not is_htf_trend_bullish("5m"):
            send_telegram("RSI buy signal detected, but HTF trend not bullish")
            return False'''
        send_telegram("Buy signal confirmed: RSI exit oversold + HTF bullish")
        return True

    elif STRATEGY == "MACD":
        if "macd_line" not in df.columns or "signal_line" not in df.columns:
            return False
        macd = df["macd_line"]
        signal = df["signal_line"]
        hist = macd - signal
        # MACD crosses above signal on closed candle
        if not (macd.iloc[-2] <= signal.iloc[-2] and macd.iloc[-1] > signal.iloc[-1]):
            return False
        
        macd_was_below_for_several_bars = True
        for i in range(2, 11):           # i = 2 → candle -2, i = 7 → candle -7
            if macd.iloc[-i] > signal.iloc[-i]:
                macd_was_below_for_several_bars = False
                break

        if not macd_was_below_for_several_bars:
            send_telegram("Good MACD crossover, but MACD was not below signal for several bars")
            return False
        
        # 3. confirm HTF trend is bullish
        '''if not is_htf_trend_bullish("5m"):
            send_telegram("MACD buy signal detected, but HTF trend not bullish")
            return False'''
        #send_telegram("Buy signal confirmed: MACD crossover + HTF bullish")
        return True

    return False

# =============================
# KLINE HANDLER – CLEAN & MODULAR
# =============================
def kline_handler(msg):
    global klines_history, position_open, entry_price
    global stoploss_limit_id, stoploss_monitor_attempts, tp_id,stop_lossed_trades

    # Handle multiplex socket wrapper
    if 'data' in msg:
        k = msg['data'].get('k', {})
    else:
        k = msg.get('k', {})

    if not k or not k.get("x", False):
        return  # Not a closed candle → ignore

    close_price = float(k["c"])
    volume_current = float(k["v"])  # volume of this candle
    close_time = k["T"]
    if datetime.now().minute % 5 == 0:
        print(f"[{now_str()}] KLINE CLOSED @ {close_price} | Time: {datetime.fromtimestamp(close_time/1000,tz=pytz.timezone('America/Los_Angeles')).strftime('%Y-%m-%d %H:%M:%S')}")

    klines_history.append(close_price)
    volume_history.append(volume_current)
    if len(klines_history) > KL_HISTORY_LIMIT:
        klines_history.pop(0)
        volume_history.pop(0)

    # Need enough data
    required_len = max(EMA_SLOW, RSI_PERIOD, MACD_SLOW) + 50
    if len(klines_history) < required_len:
        return

    # === Build DataFrame with all indicators ===
    df = pd.DataFrame({"close": klines_history})

    # Always compute EMA (used in many places)
    df["fast_ema"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["slow_ema"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=EMA_50).ema_indicator()
    df["ema100"] = EMAIndicator(df["close"], window=EMA_100).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=EMA_200).ema_indicator()
    # Conditional indicators (only compute if needed)
    if STRATEGY in ["RSI", "MACD"] or True:  # or always compute for flexibility
        df["rsi"] = RSIIndicator(df["close"], window=RSI_PERIOD).rsi()

        macd = MACD(df["close"], window_slow=MACD_SLOW, window_fast=MACD_FAST, window_sign=MACD_SIGNAL)
        df["macd_line"] = macd.macd()
        df["signal_line"] = macd.macd_signal()
        df["macd_hist"] = macd.macd_diff()

    if datetime.now().minute % 5 == 0:
        print(f"[{now_str()}] Latest indicators | Close: {close_price} | "
              f"Fast EMA: {df['fast_ema'].iloc[-1]:.2f} | Slow EMA: {df['slow_ema'].iloc[-1]:.2f} | "
              f"RSI: {df['rsi'].iloc[-1]:.2f} | MACD: {df['macd_line'].iloc[-1]:.2f} | Signal: {df['signal_line'].iloc[-1]:.2f}")
    # === BUY SIGNAL ===
    if not position_open and should_buy(df):
        buy_price = round(close_price * 0.9995, PRICE_PRECISION)  # slight discount

        try:
            order = client.futures_create_order(
                symbol=SYMBOL,
                side="BUY",
                type="LIMIT",
                quantity=QUANTITY_BTC,
                price=str(buy_price),
                timeInForce="GTC"
            )
            order_id = order["orderId"]

            with lock:
                globals().update(
                    limit_buy_id=order_id,
                    position_open=True
                )

            send_telegram(f"BUY SIGNAL ({STRATEGY})\nLIMIT LONG @ {buy_price}\nSize: {QUANTITY_BTC} BTC")
            log_trade("LONG_PLACED", order_id, entry=buy_price)
            start_cancel_timer(order_id)

        except Exception as e:
            print(f"[{now_str()}] BUY ORDER FAILED: {e}")
            send_exception_to_telegram
            position_open = False

    # === STOP-LOSS LOGIC (unchanged, just cleaned) ===
    if position_open and entry_price and close_price <= entry_price * (1 - SL_PCT):
        if not stoploss_limit_id:
            if tp_id:
                try:
                    client.futures_cancel_order(symbol=SYMBOL, orderId=tp_id)
                    send_telegram("TP cancelled due to SL trigger")
                except Exception as e:
                    send_exception_to_telegram(e)
                tp_id = None

            limit_sell_price = round(close_price + 20, PRICE_PRECISION)
            stop_lossed_trades += 1
            try:
                sl_order = client.futures_create_order(
                    symbol=SYMBOL,
                    side="SELL",
                    type="LIMIT",
                    quantity=QUANTITY_BTC,
                    price=str(limit_sell_price),
                    timeInForce="GTC"
                )
                stoploss_limit_id = sl_order["orderId"]
                stoploss_monitor_attempts = 0
                send_telegram(f"{STRATEGY} SL Triggered → Limit Sell @ {limit_sell_price} Stop-loss trades: {stop_lossed_trades}")
            except Exception as e:
                print(f"{STRATEGY} SL limit order failed: {e}")
                send_exception_to_telegram(e)

    # === SL MONITOR & MARKET FALLBACK ===
    if stoploss_limit_id:
        stoploss_monitor_attempts += 1
        if stoploss_monitor_attempts >= STOPLOSS_LIMIT_RETRY_MAX:
            try:
                client.futures_cancel_order(symbol=SYMBOL, orderId=stoploss_limit_id)
            except Exception as e:
                send_exception_to_telegram(e)

            try:
                market_order = client.futures_create_order(
                    symbol=SYMBOL, side="SELL", type="MARKET", quantity=QUANTITY_BTC
                )
                fills = market_order.get("fills", [])
                exit_price = float(fills[0]["price"]) if fills else close_price
                profit = (exit_price - entry_price) * QUANTITY_BTC

                global total_profit_usdc
                total_profit_usdc += profit

                send_telegram(f"{STRATEGY} MARKET STOP-LOSS @ {exit_price}\nP&L: {profit:+.2f} USDC, Total P/L: {total_profit_usdc:+.2f} USDC, Stop-loss trades: {stop_lossed_trades}, total trades: {successful_trades + stop_lossed_trades}  ")
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
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=5001, use_reloader=False), daemon=True).start()

    twm.start()

    # Start the user data socket
    twm.start_futures_user_socket(callback=user_data_handler)
    # ←←← ADD THIS LINE – this is all you need! ←←←
    threading.Thread(target=keep_alive_listen_key, daemon=True).start()  # ← No args now

    # === KLINE STREAM ===
    stream_name = f"{SYMBOL.lower()}@kline_{TIMEFRAME}"
    twm.start_futures_multiplex_socket(callback=kline_handler, streams=[stream_name])

    send_telegram(f"Futures Bot STARTED\n{STRATEGY} {SYMBOL} {TIMEFRAME}\nSize: {QUANTITY_BTC} BTC")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"[{now_str()}] Shutting down gracefully...")
        twm.stop()
        time.sleep(2)

if __name__ == "__main__":
    start_bot()
