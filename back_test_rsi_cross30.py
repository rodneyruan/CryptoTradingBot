from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime

from ta.momentum import RSIIndicator
from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_5MINUTE
rsi_period = 14
profit_target = 0.005        # +0.5%
stop_loss_threshold = 0.01   # -1%
quantity = 0.01
default_limit = 1500
tz = pytz.timezone("America/Los_Angeles")


def fetch_historical_ohlcv(symbol, timeframe, limit=default_limit):
    """Fetch OHLCV from Binance Futures."""
    try:
        klines = client.get_historical_klines(
            symbol=symbol,
            interval=timeframe,
            limit=limit
        )
        df = pd.DataFrame(klines, columns=[
            "timestamp","open","high","low","close","volume",
            "close_time","quote_volume","trades",
            "taker_base_volume","taker_quote_volume","ignored"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms").dt.tz_localize("UTC")
        df[["open","high","low","close"]] = df[["open","high","low","close"]].astype(float)
        return df
    except Exception as e:
        print(f"{datetime.now(tz)} | Error fetching klines: {e}")
        return pd.DataFrame()


def backtest():
    df = fetch_historical_ohlcv(symbol, timeframe)
    if df.empty:
        print("No data. Exiting.")
        return

    # RSI calculation
    df["rsi"] = RSIIndicator(close=df["close"], window=rsi_period).rsi()

    total_profit = 0.0
    successful_trades = 0
    total_trades = 0

    i = rsi_pe_
