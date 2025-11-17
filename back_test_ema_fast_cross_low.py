from binance.client import Client
import pandas as pd
import pytz
from datetime import datetime, timedelta
import time

from key_config import apikey, apisecret

client = Client(apikey, apisecret)

# Config
symbol = "BTCFDUSD"
timeframe = Client.KLINE_INTERVAL_15MINUTE
quantity = 0.01
BUY_DISCOUNT = 0.9980
TP_MULTIPLIER = 1.0015
SL_MULTIPLIER = 0.99
ORDER_EXPIRATION = 10
tz = pytz.timezone("America/Los_Angeles")
MAX_LIMIT = 1000

EMA_FAST_PERIOD = 9
EMA_SLOW_PERIOD = 21
EMA_TREND_PERIOD = 200


def fetch_historical_ohlcv(symbol, timeframe, days_back=30):
    all_klines = []
    end_time = int(time.time() * 1000)
    start_time = end_time - days_back * 24 * 60 * 60 * 1000

    while start_tim_
