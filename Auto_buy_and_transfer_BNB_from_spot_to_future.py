# 在币安上，使用BNB支付手续费可以享受9折优惠，现货跟合约交易账户必须存够足够的BNB才能享受优惠
# 这个程序会定期检测BNB 的余额，如果不够0.1 BNB，就自动购买BNB 并转入合约账户
import time
from binance.client import Client
from datetime import datetime


from key_config import apikey
from key_config import apisecret

# Initialize the Binance client
client = Client(apikey, apisecret)


def get_future_bnb_balance():
    """Get BNB balance from futures account"""
    try:
        futures_balance = client.futures_account_balance()
        for asset in futures_balance:
            if asset['asset'] == 'BNB':
                return float(asset['balance'])
        return 0.0
    except Exception as e:
        print(f"Error getting futures balance: {e}")
        return 0.0

def get_spot_bnb_balance():
    """Get BNB balance from spot account"""
    try:
        account_info = client.get_account()
        for asset in account_info['balances']:
            if asset['asset'] == 'BNB':
                return float(asset['free'])
        return 0.0
    except Exception as e:
        print(f"Error getting spot balance: {e}")
        return 0.0

def transfer_spot_to_futures(amount):
    """Transfer BNB from spot to futures account"""
    try:
        result = client.futures_account_transfer(
            asset='BNB',
            amount=amount,
            type=1  # 1: Spot to Futures
        )
        print(f"Transferred {amount} BNB from spot to futures")
        return True
    except Exception as e:
        print(f"Error transferring funds: {e}")
        return False

def buy_bnb_with_fdusd(amount):
    """Buy BNB using BNB/FDUSD trading pair"""
    try:
        # Get current price
        ticker = client.get_symbol_ticker(symbol='BNBFDUSD')
        price = float(ticker['price'])
        
        # Calculate required FDUSD amount (adding a small buffer)
        fdusd_amount = amount * price * 1.01  # 1% buffer
        
        # Place market buy order
        order = client.order_market_buy(
            symbol='BNBFDUSD',
            quoteOrderQty=fdusd_amount  # Amount in FDUSD
        )
        print(f"Bought {amount} BNB with {fdusd_amount} FDUSD")
        return True
    except Exception as e:
        print(f"Error buying BNB: {e}")
        return False

def main():
    while True:
        try:
            # Check futures BNB balance
            future_balance = get_future_bnb_balance()
            print(f"Current futures BNB balance: {future_balance}")
            
            if future_balance < 0.1:
                print("Futures balance below 0.1 BNB, checking spot account...")
                
                # Check spot BNB balance
                spot_balance = get_spot_bnb_balance()
                print(f"Current spot BNB balance: {spot_balance}")
                
                if spot_balance < 0.2:
                    print("Spot balance below 0.2 BNB, buying BNB...")
                    # Buy 0.2 BNB
                    if buy_bnb_with_fdusd(0.2):
                        # Wait for order to settle
                        time.sleep(5)
                        # Verify new spot balance
                        spot_balance = get_spot_bnb_balance()
                        print(f"New spot balance after purchase: {spot_balance}")
                
                # Transfer from spot to futures if we have enough
                if spot_balance >= 0.2:
                    transfer_spot_to_futures(0.2)
                else:
                    print("Insufficient spot balance after purchase attempt")
            
            else:
                print("Futures balance is sufficient (>= 0.1 BNB)")
            
            # Wait before next check (e.g., 5 minutes)
            time.sleep(300)
            
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(60)  # Wait a minute before retrying on error

if __name__ == "__main__":
    main()
