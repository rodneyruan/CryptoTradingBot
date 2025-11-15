import pandas as pd
from binance import ThreadedWebsocketManager
from ta.momentum import RSIIndicator
from key_config import apikey, apisecret

# -----------------------------
# Binance Spot Configuration
# -----------------------------
symbol = "BTCFDUSD"   # Spot symbol
quantity = 0.001      # BTC to buy
rsi_period = 6
rsi_buy = 30
tp_pct = 0.003        # 0.3%
sl_pct = 0.01         # 1%

# Global order IDs and stats
limit_buy_id = None
tp_id = None
sl_id = None
entry_price_global = 0.0
total_trades = 0
successful_trades = 0
total_profit = 0.0

# Initialize Binance client and WebSocket manager
from binance.client import Client
client = Client(apikey, apisecret)
twm = ThreadedWebsocketManager(api_key=apikey, api_secret=apisecret)
twm.start()

# -----------------------------
# Place TP/SL orders
# -----------------------------
def place_tp_sl(entry_price):
    global tp_id, sl_id, total_trades

    tp_price = round(entry_price * (1 + tp_pct), 2)
    sl_price = round(entry_price * (1 - sl_pct) * 1.001, 2)  # buffer for spot

    # Take-Profit (limit sell)
    tp_order = client.create_order(
        symbol=symbol,
        side="SELL",
        type="LIMIT",
        quantity=quantity,
        price=str(tp_price),
        timeInForce="GTC"
    )
    tp_id = tp_order["orderId"]

    # Stop-Loss (STOP-LOSS-LIMIT sell)
    sl_order = client.create_order(
        symbol=symbol,
        side="SELL",
        type="STOP_LOSS_LIMIT",
        quantity=quantity,
        price=str(sl_price),
        stopPrice=str(round(entry_price * (1 - sl_pct), 2)),
        timeInForce="GTC"
    )
    sl_id = sl_order["orderId"]

    total_trades += 1
    print(f"Trade #{total_trades} placed: Entry={entry_price}, TP={tp_price}, SL={sl_price}")

# -----------------------------
# User Data Handler
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, tp_id, sl_id, total_profit, entry_price_global, successful_trades

    if msg["e"] != "executionReport":
        return

    order_id = int(msg["i"])
    status = msg["X"]
    filled_price = float(msg.get("L", 0))

    # Limit Buy filled → place TP/SL
    if order_id == limit_buy_id and status == "FILLED":
        entry_price_global = filled_price
        print(f"Limit Buy filled at {entry_price_global}")
        place_tp_sl(entry_price_global)

    # TP filled → cancel SL
    if order_id == tp_id and status == "FILLED":
        print(f"TP filled at {filled_price} → canceling SL")
        total_profit += (filled_price - entry_price_global) * quantity
        successful_trades += 1
        try:
            client.cancel_order(symbol=symbol, orderId=sl_id)
        except:
            pass

    # SL filled → cancel TP
    if order_id == sl_id and status == "FILLED":
        print(f"SL filled at {filled_price} → canceling TP")
        total_profit += (filled_price - entry_price_global) * quantity
        try:
            client.cancel_order(symbol=symbol, orderId=tp_id)
        except:
            pass

    # Print statistics
    success_rate = (successful_trades / total_trades * 100) if total_trades > 0 else 0
    print(f"Total Trades: {total_trades}, Successful Trades: {successful_trades}, "
          f"Success Rate: {success_rate:.2f}%, Total P/L: {total_profit:.4f} USDT")

# -----------------------------
# Kline WebSocket Handler
# -----------------------------
# We'll keep a small history to calculate RSI
klines_history = []

def kline_handler(msg):
    global klines_history, limit_buy_id

    k = msg['k']
    is_closed = k['x']  # True if candle is closed
    close_price = float(k['c'])

    if is_closed:
        # Append closing price to history
        klines_history.append(close_price)
        if len(klines_history) > 50:
            klines_history.pop(0)

        # Calculate RSI if enough data
        if len(klines_history) > rsi_period:
            df = pd.DataFrame({'close': klines_history})
            df['rsi'] = RSIIndicator(df['close'], window=rsi_period).rsi()
            rsi_prev = df['rsi'].iloc[-2]
            rsi_now = df['rsi'].iloc[-1]

            print(f"Close Price: {close_price}, RSI: {rsi_now:.2f}")

            # RSI Buy signal
            if rsi_prev < rsi_buy and rsi_now >= rsi_buy:
                print("RSI Buy signal detected!")

                # Place limit buy at current_price - 50
                buy_price = close_price - 50
                limit_order = client.create_order(
                    symbol=symbol,
                    side="BUY",
                    type="LIMIT",
                    quantity=quantity,
                    price=str(round(buy_price, 2)),
                    timeInForce="GTC"
                )
                limit_buy_id = limit_order["orderId"]
                print(f"Limit Buy order placed at {buy_price}, Order ID: {limit_buy_id}")

# -----------------------------
# Start WebSockets
# -----------------------------
# User data for execution reports
twm.start_user_socket(callback=user_data_handler)
print("WebSocket user data started…")

# Kline stream for 1m candles
twm.start_kline_socket(symbol=symbol.lower(), interval='1m', callback=kline_handler)
print("Kline WebSocket started…")

# Keep main thread alive
import time
while True:
    time.sleep(1)
