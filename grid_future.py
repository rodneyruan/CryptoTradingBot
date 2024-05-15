import time
from binance.client import Client
#import pandas as pd
#import ta
import sys
import os
from datetime import datetime
#import importlib
import traceback
import json

from key_config import apikey
from key_config import apisecret

# ********** Symbol Specific Setting START >>>>>>>>>>>>
#  -0.5% *6 = -3%, +0.5%*4 = +2%, range and trigger trail up/down
#  -0.5% *16 = -8% +0.5%*4 = +7%
#  start from -7%, -10%, -13% 0.002 BTC,~ 120U/grid, 2 grids, totally around 240 U
CurrentSymbol='BTCUSDC'
PRICE_PRECISION = 2
QTY_PRECISION = 4

QtyPerOrder  = 0.001
ProfitRate = 0.01

NumberOfInitialBuyGrids = 6
NumberOfInitialSellGrids = 4
NumberOfTrailingDownGrids = 10
NumberOfTrailingUpGrids = 10

TrailDown_start_grids = 6
TrailUp_start_grids = 4

BuyingDipStartDropPercent = 0.07
BuyingDipGridDepthPercent = 0.03
NumberOfBuyingDipGrids = 2
BuyingDipQtyPerOrder  = 0.002

MARKET_SELL_ADDITIONAL_RATE=1.0003
MARKET_BUY_ADDITIONAL_RATE= 0.9997

FIRST_INITIAL_BUY_PERCENTAGE=0.6
SECOND_INITIAL_BUY_PRICE_RATE=0.995

FIRST_INITIAL_SELL_PERCENTAGE=0.6
SECOND_INITIAL_SELL_PRICE_RATE=1.005


# **********Symbol Specific Setting END <<<<<<<<<<<<<<

#  Configurtion file parser 
def read_config_file(file_path):
    with open(file_path, 'r') as file:
        config = json.load(file)
    return config

config_file = "BTCUSDCNeutral.json"
Direction="Neutral"

if len(sys.argv) > 2:
    CurrentSymbol =  sys.argv[1]
    Direction = sys.argv[2]

config_file = CurrentSymbol+Direction+".json"

config = read_config_file(config_file)

CurrentSymbol = config["CurrentSymbol"]
QtyPerOrder = config["QtyPerOrder"]
ProfitRate = config["ProfitRate"]
PRICE_PRECISION = config["PRICE_PRECISION"]
QTY_PRECISION = config["QTY_PRECISION"]
NumberOfInitialBuyGrids = config["NumberOfInitialBuyGrids"]
NumberOfInitialSellGrids = config["NumberOfInitialSellGrids"]
NumberOfTrailingDownGrids = config["NumberOfTrailingDownGrids"]
NumberOfTrailingUpGrids = config["NumberOfTrailingUpGrids"]
TrailDown_start_grids = config["TrailDown_start_grids"]
TrailUp_start_grids = config["TrailUp_start_grids"]
BuyingDipStartDropPercent = config["BuyingDipStartDropPercent"]
BuyingDipGridDepthPercent = config["BuyingDipGridDepthPercent"]
NumberOfBuyingDipGrids = config["NumberOfBuyingDipGrids"]
BuyingDipQtyPerOrder = config["BuyingDipQtyPerOrder"]



NumberOfTotalGrids = NumberOfInitialBuyGrids  + NumberOfInitialSellGrids +NumberOfTrailingUpGrids+NumberOfTrailingDownGrids


io_file = CurrentSymbol+Direction+".txt"


trail_down_trigger_price = 0
trail_up_trigger_price = float("inf")


Buying_dip_price = float("inf")

### Status 0=NotStarted,  1=BuyOrderPlaced, 2=BuyOrderFilled 3=SellOrderPlaced, 4=SellOrderFilled
OrderStatus_NotStarted = 0
OrderStatus_BuyOrderPlaced = 1
OrderStatus_BuyOrderFilled = 2
OrderStatus_SellOrderPlaced = 3
OrderStatus_SellOrderFilled = 4

NODE_STATUS_INACTIVE = 0
NODE_STATUS_ACTIVE = 1



client = Client(apikey, apisecret)
CurrentPrice = client.futures_symbol_ticker(symbol=CurrentSymbol)
initial_price= round(float(CurrentPrice['price']) ,PRICE_PRECISION )

baseline_price = initial_price
grid_depth = round(initial_price * ProfitRate, PRICE_PRECISION)

ProfitPerGrid= grid_depth * ProfitRate


n_trail_up_or_down = 0

SumBuyAmount=0
SumSellAmount=0
SumBuyValue=0
SumSellValue=0
current_price=0
price_to_buy=0
price_to_sell=0


class Logger(object):
    def __init__(self, filename="Default.log"):
        self.terminal = sys.stdout
        self.output_file = filename

    def write(self, message):
        #IF you want std out, uncomment this line
        #self.terminal.write(message)
        with open(self.output_file, "a") as file:
            file.write(message)


sys.stdout = Logger(io_file)
print("\n\n %s ======>    Trading bot started @%.4f" %( datetime.now(), initial_price))

class GridTradeNode:
    def __init__(self):
        self.price_buy = 0
        self.price_sell = 0
        self.order_status = OrderStatus_NotStarted
        self.order_id = 0
        self.node_status = NODE_STATUS_INACTIVE


## 1 Initializing Nodes
GridTradeNodeList = []

for i in range(NumberOfTotalGrids):
    node = GridTradeNode()
    node.price_buy = round( initial_price-grid_depth* (NumberOfTrailingDownGrids+NumberOfInitialBuyGrids)+ i*grid_depth, PRICE_PRECISION)
    node.price_sell = round( node.price_buy + grid_depth, PRICE_PRECISION)

    GridTradeNodeList.append(node)



### 2 Initial POSITION

if( Direction == "Long" ):
    price_to_buy = round(initial_price* MARKET_BUY_ADDITIONAL_RATE, PRICE_PRECISION)
    quantity_to_buy= round(NumberOfInitialSellGrids * QtyPerOrder * FIRST_INITIAL_BUY_PERCENTAGE,QTY_PRECISION)

    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT', quantity=quantity_to_buy, price=price_to_buy, timeInForce="GTC")
    order_id = order['orderId']

    for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids+NumberOfInitialSellGrids):
        GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
        GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE


    while (True):
        time.sleep(30)
        try:
            order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
            if (order['status'] == 'FILLED'):
                for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids+NumberOfInitialSellGrids):
                    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderFilled
                    SumBuyAmount +=QtyPerOrder
                    SumBuyValue+=QtyPerOrder*price_to_buy
                print("First Part initial BUY order filled.    Price=%.2f     amount=%.4f    SumBuyValue=%.4f" %(price_to_buy,quantity_to_buy,SumBuyValue) )

                break
            else:
                print("Order Status is ",order['status'])
        except:
            print("Exception!!! Exception occured while getting buy order status")
            print(traceback.format_exc())


    price_to_buy = round(initial_price*SECOND_INITIAL_BUY_PRICE_RATE,PRICE_PRECISION)
    quantity_to_buy= round( NumberOfInitialSellGrids * QtyPerOrder*(1-FIRST_INITIAL_BUY_PERCENTAGE) ,QTY_PRECISION)

    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT', quantity=quantity_to_buy, price=price_to_buy, timeInForce="GTC")
    order_id = order['orderId']
    print("Second part of initial BUY order is placed. quantity = %.4f  price =  %.4f " % (quantity_to_buy, price_to_buy))


elif( Direction == "Short" ):
    price_to_sell = round(initial_price*MARKET_SELL_ADDITIONAL_RATE,PRICE_PRECISION)
    quantity_to_sell= round( NumberOfInitialBuyGrids * QtyPerOrder*FIRST_INITIAL_SELL_PERCENTAGE ,QTY_PRECISION)

    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT', quantity=quantity_to_sell, price=price_to_sell, timeInForce="GTC")
    order_id = order['orderId']


    for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids):
        GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
        GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE

    while (True):
        time.sleep(30)
        try:
            order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
            if (order['status'] == 'FILLED'):
                for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids):
                    GridTradeNodeList[i].order_status = OrderStatus_SellOrderFilled
                    SumSellAmount +=QtyPerOrder
                    SumSellValue+=QtyPerOrder*price_to_sell

                print("First Part initial SELL order filled, qty=%.4f, price=%.4f, SumSellValue=%.4f" %(quantity_to_sell, price_to_sell, SumSellValue) )
                break
            else:
                print("Order Status is ",order['status'])
        except:
            print("Exception!!! Exception occured while getting buy order status")
            print(traceback.format_exc())

    
        price_to_sell = round(initial_price*SECOND_INITIAL_SELL_PRICE_RATE,PRICE_PRECISION)
        quantity_to_sell= round( NumberOfInitialBuyGrids * QtyPerOrder*(1-FIRST_INITIAL_SELL_PERCENTAGE) ,QTY_PRECISION)

        order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT', quantity=quantity_to_sell, price=price_to_sell, timeInForce="GTC")
        order_id = order['orderId']
        print("Second part of initial SELL order is placed. quantity = %.4f  price =  %.4f " % (quantity_to_sell, price_to_sell))


## 3 Initial Orders
### 3.1 Placing Initial BUY Orders
for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids):
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE
    price_to_buy= GridTradeNodeList[i].price_buy
    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT',
            quantity=QtyPerOrder, price=price_to_buy, timeInForce="GTC")
    GridTradeNodeList[i].order_id = order['orderId']
    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
    time.sleep(1)

### 3.2 Placing Initial SELL Orders
for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids + NumberOfInitialBuyGrids + NumberOfInitialSellGrids):
    price_to_sell = GridTradeNodeList[i].price_sell
    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL,
            type='LIMIT', quantity=QtyPerOrder, price=price_to_sell, timeInForce="GTC")
    GridTradeNodeList[i].order_id = order['orderId']
    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE
    time.sleep(1)

for i in range(NumberOfTotalGrids):
    print("%d(%d) - node state %d - BUY %.2f - SELL %.2f order id %d - order state %d" %
       ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
       GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id, GridTradeNodeList[i].order_status ))


ticks= 0


trail_up_counter  = 0
trail_down_counter  = 0


def print_profit():
    global SumBuyAmount
    global SumSellAmount
    global SumBuyValue
    global SumSellValue
    global current_price
    global trail_up_counter
    global trail_down_counter

    average_sell_price=0
    average_buy_price=0

    if SumBuyAmount != 0 :
        average_buy_price =SumBuyValue/SumBuyAmount
    if SumSellAmount != 0 :
        average_sell_price =SumSellValue/SumSellAmount

    matched_number = min(SumSellAmount,SumBuyAmount) // QtyPerOrder - trail_down_counter - trail_up_counter

    position = SumBuyAmount- SumSellAmount
    position_value=position*current_price

    if(SumBuyAmount > SumSellAmount):
        RealizedPNL = (average_sell_price - average_buy_price)*SumSellAmount
        UnrealizedPNL = position * (current_price - average_buy_price)
    else:
        RealizedPNL = (average_sell_price - average_buy_price)*SumBuyAmount
        UnrealizedPNL = position * (current_price - average_sell_price)   

    print("current_price=%.4f   RealizedPNL=%.4f UnrealizedPNL=%.4f matched_number=%.2f" %(current_price,RealizedPNL,UnrealizedPNL,matched_number)  )
    print("SumBuyAmount=%.4f   SumBuyValue=%.4f    average_buy_price=%.4f    SumSellAmount=%.4f   SumSellValue=%.4f    average_sell_price=%.4f  position=%.4f   current_position_value =%.4f"
         % ( SumBuyAmount,SumBuyValue,average_buy_price,SumSellAmount,SumSellValue,average_sell_price,position,position_value))


### Main Loop
while (True):
    time.sleep(120)
    ticks+=1
    current_time = datetime.now()

    try:
        CurrentPrice = client.futures_symbol_ticker(symbol=CurrentSymbol)
        current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )
    except:
        print(traceback.format_exc())
        print("Failed to get current price, sleep for 120s.")
        continue

    for i in range(NumberOfTotalGrids):
        if ( GridTradeNodeList[i].node_status == NODE_STATUS_INACTIVE):
            continue

        order_id = GridTradeNodeList[i].order_id
        try:
            time.sleep(0.1)
            order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
        except:
            print("%d : exception occured while getting order status, order_id =%d  " % (i,order_id))
            print(traceback.format_exc())
            continue

        if (order['status'] == 'FILLED'):
            if (GridTradeNodeList[i].order_status == OrderStatus_SellOrderPlaced):

                price_to_buy = GridTradeNodeList[i].price_buy
                if( price_to_buy > current_price):
                    price_to_buy = current_price
                    print(" ############## >>>>>>> Special case, that price_to_buy is higher than current price, using %.4f" 
                          %(current_price)) 

                print("%s %d (%d):   SELL Order Filled at %.2f,  MatchedNumber++    placing a new BUY at --> %.2f   "
                % ( current_time,i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids ,
                    GridTradeNodeList[i].price_sell, price_to_buy))

                try:
                    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT',
                                 quantity=QtyPerOrder, price=price_to_buy, timeInForce="GTC")
                    GridTradeNodeList[i].order_id = order['orderId']
                    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
                except:
                    print(traceback.format_exc())
                    print("%s  %d (%d) Failed to place a new BUY order at price %.4f .\n"
                            %(current_time, i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids,price_to_buy))

                SumSellAmount+=QtyPerOrder
                SumSellValue += QtyPerOrder * GridTradeNodeList[i].price_sell

                print("SumSellAmount+=%.4f   SumSellValue+=%.4f MatchedNumber+=1" %(QtyPerOrder, QtyPerOrder * GridTradeNodeList[i].price_sell))
                print_profit()


            elif (GridTradeNodeList[i].order_status == OrderStatus_BuyOrderPlaced):

                SumBuyAmount+=QtyPerOrder
                SumBuyValue += QtyPerOrder * GridTradeNodeList[i].price_buy



                price_to_sell = GridTradeNodeList[i].price_sell
                if( price_to_sell < current_price):
                    price_to_sell = current_price
                    print(" ############## >>>>>>> Special case, that price_to_sell is lower than current price, using %.4f" 
                          %(current_price)) 

                print("%s %d (%d):  BUY Order Filled at %.2f, placing a new SELL at --> %.2f ."
                    % (current_time,  i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids , GridTradeNodeList[i].price_buy, price_to_sell))

                try:
                    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT',
                                 quantity=QtyPerOrder, price=price_to_sell, timeInForce="GTC")

                    GridTradeNodeList[i].order_id = order['orderId']
                    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
                except:
                    print(traceback.format_exc())
                    print("%s  %d (%d) Failed to place a new SELL order at price %.4f \n"
                           %(current_time, i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids,price_to_sell))

                print("SumBuyAmount+=%.4f   SumBuyValue+=%.4f" %(SumBuyAmount,SumBuyValue))
                print_profit()


    #Need to trail up or down?
    trail_down_trigger_price = baseline_price - TrailDown_start_grids * grid_depth
    trail_up_trigger_price = baseline_price + TrailUp_start_grids * grid_depth

    if ( current_price <  trail_down_trigger_price  and  (NumberOfTrailingDownGrids + n_trail_up_or_down) > 0 ):
        print("%s, <<<<------- Trailing down! current_price is %.4f, trail_down_trigger_price is %.4f " % (datetime.now(),current_price, trail_down_trigger_price) )


        highest_index= NumberOfTrailingDownGrids+ NumberOfInitialBuyGrids + NumberOfInitialSellGrids + n_trail_up_or_down -1
        print("Before trailing Down,  highest_index is %d, Node states:" % (highest_index))
        for i in range(NumberOfTotalGrids):
            print("%d (%d): note state %d - BUY %.2f - SELL %.2f - order id %d - order state %d\n" %
                   ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
                   GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
                   GridTradeNodeList[i].order_status ))


        # First Step for trailing down, is to cancel the highest SELL Order
        need_to_sell_for_trail_down = 0

        if( GridTradeNodeList[highest_index].order_status == OrderStatus_SellOrderPlaced):
            order_id = GridTradeNodeList[highest_index].order_id
            try:
                order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
                print("Trailing down, highest order status is ", order['status']  )

                if( order['status'] == 'NEW'):
                    client.futures_cancel_order(symbol=CurrentSymbol, orderId=order_id)
                    need_to_sell_for_trail_down = 1
            except:
                print(traceback.format_exc())
                print("Fail to cancel the highest SELL Order,  highest_index = %d, order_id = %d " % (highest_index,order_id))

        time.sleep(60)



        # 4.2 Second step is to sell QtyPerOrder
        retry_counter = 3

        if( need_to_sell_for_trail_down == 1 ):
            try:
                price_to_sell = round(current_price*MARKET_SELL_ADDITIONAL_RATE, PRICE_PRECISION)
                order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT',
                          quantity=QtyPerOrder, price = price_to_sell,timeInForce="GTC")
                order_id = order['orderId']

                while( retry_counter > 0 ):
                    time.sleep(60)
                    order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
                    if( order['status'] == 'FILLED'):
                        break
                    elif(retry_counter == 1):
 
                        print("%s Trailing down, the LIMIT-SELL order is not executed after 3 minites, price @ %.4f" 
                              %(datetime.now(), price_to_sell))
                    retry_counter -=1
            except:
                print(traceback.format_exc())
                print("Trailing down, failed to sell at current price for trailing down ")

        # TODO  check if it is really sold?

        SumSellAmount+=QtyPerOrder
        SumSellValue+= QtyPerOrder*price_to_sell

        print("SumSellAmount+=%.4f   SumSellValue+=%.4f MatchedNumber+=1" %(QtyPerOrder, QtyPerOrder * price_to_sell))
        print_profit()

        print("After trailing down->")
        print_profit()


        GridTradeNodeList[highest_index].node_status = NODE_STATUS_INACTIVE
        time.sleep(5)



        # 4.3  Third Step is to add a lowest BUY order
        lowest_index= NumberOfTrailingDownGrids + n_trail_up_or_down
        price_to_buy = round( GridTradeNodeList[lowest_index-1].price_buy, PRICE_PRECISION)

        try:
            order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT',
                                 quantity=QtyPerOrder, price= price_to_buy, timeInForce="GTC")
            order_id = order['orderId']
            GridTradeNodeList[lowest_index-1].order_id = order_id
            GridTradeNodeList[lowest_index-1].order_status = OrderStatus_BuyOrderPlaced
            GridTradeNodeList[lowest_index-1].node_status = NODE_STATUS_ACTIVE

        except:
            print(traceback.format_exc())
            print("%s Trailing down, Failed to place a new lowest order at %.4f, index is %d .\n"
                 %(datetime.now(),price_to_buy, lowest_index-1))

        baseline_price -= grid_depth
        n_trail_up_or_down -= 1
        trail_down_counter += 1


        print("After trailing Down, new baseline_price is %.4f, trail_down_counter is %d" % (baseline_price,trail_down_counter))
        for i in range(NumberOfTotalGrids):
            print("%d (%d): note state %d - BUY %.2f - SELL %.2f - order id %d - order state %d" %
                   ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
                   GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
                   GridTradeNodeList[i].order_status ))


    elif ( current_price >  trail_up_trigger_price and n_trail_up_or_down < NumberOfTrailingUpGrids):
        print("------->>> Trailing UP ! current_price is %.4f, trail_up_trigger_price is %.4f " % (current_price, trail_up_trigger_price) )



        lowest_index = NumberOfTrailingDownGrids + n_trail_up_or_down
        print("Before trailing UP,  lowest_index is %d, Node states:" % (lowest_index))

        for i in range(NumberOfTotalGrids):
            print("%d (%d) - node state %d - BUY %.2f - SELL %.2f - order id %d - order state %d" %
              ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
               GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
               GridTradeNodeList[i].order_status ))

        time.sleep(5)

        # First Step for trailing UP, is to cancel the lowest BUY Order
        need_to_buy_for_trail_up = 0

        if( GridTradeNodeList[lowest_index].order_status == OrderStatus_BuyOrderPlaced):
            order_id = GridTradeNodeList[lowest_index].order_id
            try:
                order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
                print("Trailing UP, lowest order status is ", order['status']  )

                if( order['status'] == 'NEW'):
                    client.futures_cancel_order(symbol=CurrentSymbol, orderId=order_id)
                    need_to_buy_for_trail_up = 1
            except:
                print(traceback.format_exc())
                print("Fail to cancel the lowest BUY Order,  highest_index = %d, order_id = %d " % (lowest_index,order_id))

        GridTradeNodeList[lowest_index].node_status = NODE_STATUS_INACTIVE

        time.sleep(7)



        #Second step is to sell QtyPerOrder
        retry_counter = 3

        if( need_to_buy_for_trail_up == 1 ):
            try:
                price_to_buy = round(current_price*MARKET_BUY_ADDITIONAL_RATE, PRICE_PRECISION)
                order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT',
                          quantity=QtyPerOrder, price = price_to_buy,timeInForce="GTC")
                order_id = order['orderId']

                while( retry_counter > 0 ):
                    time.sleep(60)
                    order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
                    if( order['status'] == 'FILLED'):
                        break
                    elif(retry_counter == 1):
                        print("%s Trailing UP, the BUY order is not executed after 3 minites, need to check manually, price to buy is %.4f"
                        %(datetime.now(), price_to_buy))
                    retry_counter -=1

            except:
                print(traceback.format_exc())
                print("Trailing up, failed to buy at current price for trailing up  ")



        # TODO  check if the Market_BUY is successful

        SumBuyAmount+=QtyPerOrder
        SumBuyValue+= QtyPerOrder*price_to_buy
        print("TrailUP: SumBuyAmount+=%.4f   SumBuyValue+=%.4f" %(QtyPerOrder,QtyPerOrder*price_to_buy))
        print_profit()


        time.sleep(7)



        #5.3  Third Step is to add a highest SELl order
        highest_index= NumberOfTrailingDownGrids + NumberOfInitialBuyGrids +NumberOfInitialSellGrids+ n_trail_up_or_down -1
        GridTradeNodeList[highest_index+1].node_status = NODE_STATUS_ACTIVE




        price_to_sell = round( GridTradeNodeList[highest_index+1].price_sell, PRICE_PRECISION)

        try:
            order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT',
                                 quantity=QtyPerOrder, price= price_to_sell, timeInForce="GTC")
            order_id = order['orderId']
            GridTradeNodeList[highest_index+1].order_id = order_id
            GridTradeNodeList[highest_index+1].order_status = OrderStatus_SellOrderPlaced

        except:
            print(traceback.format_exc())
            print("%s Trailing UP, Failed to place a new highest SELL order at %.4f, index is %d .\n"
                           %(datetime.now(),price_to_sell, highest_index+1))


        baseline_price += grid_depth
        n_trail_up_or_down += 1
        trail_up_counter += 1

        print("After trailing Up, new baseline_price is %.4f trail_up_counter is %d" % (baseline_price, trail_up_counter))
        for i in range(NumberOfTotalGrids):
            print("%d (%d) - node state %d - BUY %.2f - SELL %.2f - order id %d - order state %d" %
              ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
               GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
               GridTradeNodeList[i].order_status))

    else:
        if( n_trail_up_or_down > NumberOfTrailingUpGrids ):
            if(ticks %10 == 1):
                print("We have hit the Trail Up limt.  n_trail_up_or_down is %d" % (n_trail_up_or_down))
        elif( (NumberOfTrailingDownGrids + n_trail_up_or_down) < 0 ):
            if(ticks %10 == 1):
                print("We have hit the Trail Down limt.  n_trail_up_or_down is %d" % (n_trail_up_or_down))



