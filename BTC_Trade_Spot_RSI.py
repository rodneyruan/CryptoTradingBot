#Spot trade: BUY when RSI crosses  above 30
import time
import pandas as pd
from binance.client import Client
from binance.streams import ThreadedWebsocketManager
from ta.momentum import RSIIndicator

# -----------------------------
# Binance API keys
# -----------------------------
from key_config import apikey
from key_config import apisecret

# Binance Futures configuration
client = Client(apikey, apisecret)

symbol = "BTCFDUSD"
quantity = 0.001  # BTC to buy
rsi_period = 6
rsi_buy = 30
tp_pct = 0.003  # 0.3%
sl_pct = 0.01   # 1%

# Global order IDs
limit_buy_id = None
tp_id = None
sl_id = None

# Stats
total_trades = 0
successful_trades = 0
total_profit = 0.0
entry_price_global = 0.0

# -----------------------------
# Fetch historical candles
# -----------------------------
def get_ohlcv(limit=50):
    klines = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=limit)
    df = pd.DataFrame(klines, columns=[
        "timestamp","open","high","low","close","volume","close_time",
        "quote_volume","trades","tb_base_vol","tb_quote_vol","ignore"
    ])
    df["close"] = df["close"].astype(float)
    return df

# -----------------------------
# Place TP/SL orders
# -----------------------------
def place_tp_sl(entry_price):
    global tp_id, sl_id, total_trades

    tp_price = entry_price * (1 + tp_pct)
    sl_price = entry_price * (1 - sl_pct)

    # Take-Profit (limit sell)
    tp_order = client.create_order(
        symbol=symbol,
        side="SELL",
        type="LIMIT",
        quantity=quantity,
        price=str(round(tp_price, 2)),
        timeInForce="GTC"
    )
    tp_id = tp_order["orderId"]

    # Stop-Loss (STOP-LOSS-LIMIT sell)
    sl_order = client.create_order(
        symbol=symbol,
        side="SELL",
        type="STOP_LOSS_LIMIT",
        quantity=quantity,
        price=str(round(sl_price, 2)),  # limit price
        stopPrice=str(round(sl_price, 2)),  # trigger price
        timeInForce="GTC"
    )
    sl_id = sl_order["orderId"]

    total_trades += 1
    print(f"Trade #{total_trades} placed: Entry={entry_price}, TP={tp_price}, SL={sl_price}")

# -----------------------------
# WebSocket User Event Handler
# -----------------------------
def user_data_handler(msg):
    global limit_buy_id, tp_id, sl_id, total_profit, entry_price_global, successful_trades

    if msg["e"] != "executionReport":
        return

    order_id = int(msg["i"])
    status = msg["X"]
    filled_price = float(msg.get("L", 0))

    # If limit buy fills → place TP and SL
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
    print(f"Total Trades: {total_trades}, Successful Trades: {successful_trades}, Success Rate: {success_rate:.2f}%, Total P/L: {total_profit:.4f} USDT")

# -----------------------------
# Main Trading Loop
# -----------------------------
def check_rsi_and_trade():
    global limit_buy_id
    df = get_ohlcv(limit=50)
    df["rsi"] = RSIIndicator(df["close"], window=rsi_period).rsi()
    rsi_prev = df["rsi"].iloc[-2]
    rsi_now = df["rsi"].iloc[-1]
    current_price = df["close"].iloc[-1]

    print(f"Price: {current_price}, RSI: {rsi_now:.2f}")

    # Buy signal: RSI crosses above 30
    if rsi_prev < rsi_buy and rsi_now >= rsi_buy:
        print("RSI Buy signal detected!")

        # Place a LIMIT BUY at current_price - 50
        buy_price = current_price - 50
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
# Run WebSocket & Loop
# -----------------------------
if __name__ == "__main__":
    twm = ThreadedWebsocketManager(api_key=API_KEY, api_secret=API_SECRET)
    twm.start()

    twm.start_user_socket(callback=user_data_handler)
    print("WebSocket user data started…")

    while True:
        try:
            check_rsi_and_trade()
        except Exception as e:
            print("Error:", e)
        time.sleep(60)  # check every 1 min
