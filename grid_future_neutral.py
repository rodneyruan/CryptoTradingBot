import time
from binance.client import Client
#import pandas as pd
#import ta
import sys
import os
from datetime import datetime
import importlib
import math
import traceback
#import random
from key_config import apikey
from key_config import apisecret
#apikey = ''
#apisecret = ''




if len(sys.argv) < 2:
    print("Please provide the name of the module as a command-line argument.")
    module_name = "BTCUSDC__future_neutral_header"
else:
    module_name = sys.argv[1]+"_future_neutral_header"

try:
    imported_module = importlib.import_module(module_name)
    print(f"Module {module_name} imported successfully.")

except ImportError:
        print(f"Failed to import module {module_name}.")


globals().update(vars(imported_module))




io_file = CurrentSymbol+"_future_neutral.log"


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

#CurrentPrice = client.get_symbol_ticker(symbol=CurrentSymbol)
CurrentPrice = client.futures_symbol_ticker(symbol=CurrentSymbol)


initial_price= round(float(CurrentPrice['price']) ,PRICE_PRECISION )

print(CurrentPrice)
print(initial_price)


baseline_price = initial_price
grid_depth = round(initial_price * ProfitRate, PRICE_PRECISION)

ProfitPerGrid= grid_depth * ProfitRate
GridProfit=0
FloatingProfit=0
TotalProfit=0
Position = 0
CostForPosition = 0

n_trail_up_or_down = 0

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
print("\n\n %s *******------------->   Future Trading bot started @ %s " %( datetime.now(), initial_price))

class GridTradeNode:
    def __init__(self):
        self.price_buy = 0
        self.price_sell = 0
        self.order_status = OrderStatus_NotStarted
        self.order_id = 0
        self.node_status = NODE_STATUS_INACTIVE
        self.buy_order_executed_price = 0
        self.sell_order_executed_price = 0


## 1 Initializing Nodes
GridTradeNodeList = []

for i in range(NumberOfTotalGrids):
    node = GridTradeNode()
    node.price_buy = round( initial_price-grid_depth* (NumberOfTrailingDownGrids+NumberOfInitialBuyGrids)+ i*grid_depth, PRICE_PRECISION)
    node.price_sell = round( node.price_buy + grid_depth, PRICE_PRECISION)

    GridTradeNodeList.append(node)


###

#client.futures_change_leverage(symbol=CurrentSymbol, leverage=LEVERAGE)
#client.futures_change_margin_type(symbol=CurrentSymbol, marginType=MARGIN_TYPE)

#order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT', quantity=0.002, price=62000, timeInForce="GTC")
#order_id = order['orderId']

#order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
#time.sleep(20)
#if (order['status'] == 'NEW'):
#    client.futures_cancel_order(symbol=CurrentSymbol, orderId=order_id)



### 2 Placing Initial SELL Order
#2.1
"""price_to_sell = round(initial_price*MARKET_SELL_ADDITIONAL_RATE,PRICE_PRECISION)
quantity_to_sell= round( NumberOfInitialBuyGrids * QtyPerOrder*FIRST_PART_INITIAL_SELL_ORDER_PERCENT ,QTY_PRECISION)
#print("price_to_buy and quantity_to_buy: ",price_to_buy," ",quantity_to_buy)

order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT', quantity=quantity_to_sell, price=price_to_sell, timeInForce="GTC")
order_id = order['orderId']

for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids):

    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE



while (True):
    time.sleep(20)
    try:
        order = client.futures_get_order(symbol=CurrentSymbol,orderId=order_id)
        if (order['status'] == 'FILLED'):
            print("First part of %.4f @ %.4f Initial SELL order is filled" % (quantity_to_sell, price_to_sell))
            for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids):
                GridTradeNodeList[i].order_status = OrderStatus_SellOrderFilled
                GridTradeNodeList[i].sell_order_executed_price = initial_price
                Position -= QtyPerOrder
                CostForPosition += QtyPerOrder*initial_price
            break
        else:
            print("Order Status is ",order['status'])
    except:
        print("Exception!!! Exception occured while getting buy order status")


#2.2
price_to_sell = round(initial_price*SECOND_PART_INITIAL_SELL_PRICE_RATE,PRICE_PRECISION)
quantity_to_sell= round( NumberOfInitialBuyGrids * QtyPerOrder*(1-FIRST_PART_INITIAL_SELL_ORDER_PERCENT) ,QTY_PRECISION)

order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT', quantity=quantity_to_sell, price=price_to_sell, timeInForce="GTC")
order_id = order['orderId']
print("Second part of Initial sell order of %.4f  @ %.4f is placed" % (quantity_to_sell, price_to_sell))

"""


#2.3
for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids+NumberOfInitialSellGrids):
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE
    price_to_sell= GridTradeNodeList[i].price_sell
    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL,
            type='LIMIT', quantity=QtyPerOrder, price=price_to_sell, timeInForce="GTC")

    GridTradeNodeList[i].order_id = order['orderId']
    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
    time.sleep(1)

###


### 3 Placing Initial Buy Order
for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids + NumberOfInitialBuyGrids ):
    price_to_buy = GridTradeNodeList[i].price_buy

    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT',
            quantity=QtyPerOrder, price=price_to_buy, timeInForce="GTC")

    GridTradeNodeList[i].order_id = order['orderId']
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE

    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
    time.sleep(2)


for i in range(NumberOfTotalGrids):
    print("%d(%d) - node state %d - BUY %.2f - SELL %.2f order id %d - order state %d" %
       ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
       GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id, GridTradeNodeList[i].order_status ))




###

ticks= 0
matched_number=0
new_matched=0

trail_up_counter  = 0
trail_down_counter  = 0


### Main Loop
while (True):
    time.sleep(120)
    ticks+=1
    new_matched=0
    current_time = datetime.now()


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
            try:
                CurrentPrice = client.futures_symbol_ticker(symbol=CurrentSymbol)
                current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )
            except:
                print(traceback.format_exc())
                print("Faled to get current price ...")
            if (GridTradeNodeList[i].order_status == OrderStatus_SellOrderPlaced):
                GridTradeNodeList[i].sell_order_executed_price =  GridTradeNodeList[i].price_sell

                if (Position > 0.00001 ) :
                    CostForPosition -= QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price
                    print("position --, cost for postion -= %.4f, CostForPosition=%.4f " %(QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price,CostForPosition ))
                    GridProfit = GridProfit + (GridTradeNodeList[i].sell_order_executed_price - GridTradeNodeList[i].buy_order_executed_price)* QtyPerOrder
                    new_matched=1
                    matched_number +=1
                else:
                    CostForPosition += QtyPerOrder * GridTradeNodeList[i].sell_order_executed_price
                    print("position ++, cost for postion ++ %.4f, CostForPosition=%.4f " %(QtyPerOrder * GridTradeNodeList[i].sell_order_executed_price,CostForPosition ))

                Position -= QtyPerOrder


                price_to_buy = GridTradeNodeList[i].price_buy
                if( price_to_buy > current_price):
                    price_to_buy = current_price
                    print(" ############## >>>>>>> Special case, that price_to_buy is higher than current price, using %.4f" 
                          %(current_price)) 

                print("%s %d (%d):   SELL Order Filled at %.2f,  placing a new BUY at --> %.2f  TotalMatched :%d  "
                % ( current_time,i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids ,
                    GridTradeNodeList[i].sell_order_executed_price, price_to_buy, matched_number))


                try:
                    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_BUY, type='LIMIT',
                                 quantity=QtyPerOrder, price=price_to_buy, timeInForce="GTC")
                    GridTradeNodeList[i].order_id = order['orderId']
                    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
                except:
                    print(traceback.format_exc())
                    print("%s  %d (%d) Failed to place a new BUY order at price %.4f .\n"
                            %(current_time, i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids,price_to_buy))

                # print the profits
                try:
                    if(Position>0):
                        FloatingProfit = current_price * Position - CostForPosition
                        TotalProfit = FloatingProfit + GridProfit

                    else:
                        FloatingProfit = CostForPosition -current_price * (-1*Position)
                        TotalProfit = FloatingProfit + GridProfit

                    current_value = round(Position * current_price, PRICE_PRECISION)
                    print("%s Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f, current_value =%.4f \n"
                            % (current_time,matched_number, GridProfit, FloatingProfit, TotalProfit, Position, current_value))
                except:
                    print("%s Failed to get current price" %(current_time) );







            elif (GridTradeNodeList[i].order_status == OrderStatus_BuyOrderPlaced):

                GridTradeNodeList[i].buy_order_executed_price =  GridTradeNodeList[i].price_buy


                if   (Position > -0.00001 ):
                    CostForPosition += QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price
                    print("position ++, cost for postion ++ %.4f, CostForPosition=%.4f " %(QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price,CostForPosition ))

                else:
                    CostForPosition -= QtyPerOrder * GridTradeNodeList[i].sell_order_executed_price
                    print("position --, cost for postion -- %.4f, CostForPosition=%.4f " %(QtyPerOrder * GridTradeNodeList[i].sell_order_executed_price,CostForPosition ))
                    GridProfit = GridProfit + (GridTradeNodeList[i].sell_order_executed_price - GridTradeNodeList[i].buy_order_executed_price)* QtyPerOrder
                    new_matched=1
                    matched_number +=1


                Position += QtyPerOrder



                price_to_sell = GridTradeNodeList[i].price_sell
                if( price_to_sell < current_price):
                    price_to_sell = current_price
                    print(" ############## >>>>>>> Special case, that price_to_sell is lower than current price, using %.4f" 
                          %(current_price)) 

                print("%s %d (%d):  BUY Order Filled at %.2f, placing a new SELL at --> %.2f ."
                    % (current_time,  i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids , GridTradeNodeList[i].buy_order_executed_price, price_to_sell))

                try:
                    order = client.futures_create_order(symbol=CurrentSymbol, side=client.SIDE_SELL, type='LIMIT',
                                 quantity=QtyPerOrder, price=price_to_sell, timeInForce="GTC")

                    GridTradeNodeList[i].order_id = order['orderId']
                    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
                except:
                    print(traceback.format_exc())
                    print("%s  %d (%d) Failed to place a new SELL order at price %.4f \n"
                           %(current_time, i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids,price_to_sell))

                # print the profits
                try:
                    CurrentPrice = client.futures_symbol_ticker(symbol=CurrentSymbol)
                    current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )

                    if(Position>0):
                        FloatingProfit = current_price * Position - CostForPosition
                        TotalProfit = FloatingProfit + GridProfit

                    else:
                        FloatingProfit = CostForPosition -current_price * (-1*Position)
                        TotalProfit = FloatingProfit + GridProfit

                    current_value = round(Position * current_price, PRICE_PRECISION)
                    print("%s Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f, current_value =%.4f \n"
                            % (current_time,matched_number, GridProfit, FloatingProfit, TotalProfit, Position, current_value))

                except:
                    print(traceback.format_exc())
                    print("%s Failed to get current price" %(current_time) );




    try:
        CurrentPrice = client.futures_symbol_ticker(symbol=CurrentSymbol)
    except:
        print("Failed to get current price")
        continue
    current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )


    #Need to trail up or down?
    trail_down_trigger_price = baseline_price - TrailDown_start_grids * grid_depth
    trail_up_trigger_price = baseline_price + TrailUp_start_grids * grid_depth

    if ( current_price <  trail_down_trigger_price  and  (NumberOfTrailingDownGrids + n_trail_up_or_down) > 0 ):
        print("<<<<------- Trailing down! current_price is %.4f, trail_down_trigger_price is %.4f " % (current_price, trail_down_trigger_price) )



        highest_index= NumberOfTrailingDownGrids+ NumberOfInitialBuyGrids + NumberOfInitialSellGrids + n_trail_up_or_down -1
        print("Before trailing Down,  highest_index is %d, Node states:" % (highest_index))
        for i in range(NumberOfTotalGrids):
            print("%d (%d): note state %d - BUY %.2f - SELL %.2f - order id %d - order state %d - last buy %f - last sell %f \n" %
                   ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
                   GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
                   GridTradeNodeList[i].order_status, GridTradeNodeList[i].buy_order_executed_price, GridTradeNodeList[i].sell_order_executed_price ))


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

        time.sleep(7)


        #Second step is to sell QtyPerOrder
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
                        print("%s Trailing down, the SELL order is not executed after 3 minites, need to check manually, price to sell is %.4f"
                        %(datetime.now(), price_to_sell))
                    retry_counter -=1
            except:
                print(traceback.format_exc())
                print("Trailing down, failed to sell at current price for trailing down ")

        # TODO  check if it is really sold?
        #The market sell is counted to the -1, -2, -3 grid, and as so on ...
        trail_down_index= NumberOfTrailingDownGrids + NumberOfInitialBuyGrids  + n_trail_up_or_down -1

        # Long
        if( Position > 0.00001 ):
            CostForPosition -= QtyPerOrder * GridTradeNodeList[trail_down_index].buy_order_executed_price
            print("trail down, position --, cost for postion -= %.4f, CostForPosition=%.4f " %(QtyPerOrder * GridTradeNodeList[trail_down_index].buy_order_executed_price,CostForPosition ))
            GridProfit = GridProfit + (current_price - GridTradeNodeList[trail_down_index].buy_order_executed_price)* QtyPerOrder
            print("trail down, position --, GridProfit += %.4f " %((current_price - GridTradeNodeList[trail_down_index].buy_order_executed_price)* QtyPerOrder ))

            Position -= QtyPerOrder 
            FloatingProfit =  current_price *Position -CostForPosition
            TotalProfit = FloatingProfit + GridProfit
        #short
        else:
            CostForPosition += QtyPerOrder * current_price
            print("trail down, position ++, cost for postion -= %.4f, CostForPosition=%.4f " %(QtyPerOrder * current_price,CostForPosition ))

            Position -= QtyPerOrder 
            FloatingProfit =   CostForPosition - current_price * (-1) *Position
            TotalProfit = FloatingProfit + GridProfit

        print("%s Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f \n"
                              % (datetime.now(),matched_number, GridProfit, FloatingProfit, TotalProfit, Position))


        GridTradeNodeList[highest_index].node_status = NODE_STATUS_INACTIVE
        time.sleep(5)

        # Third Step is to add a lowest BUY order
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



        print("After trailing Down, new baseline_price is %.4f" % (baseline_price))
        for i in range(NumberOfTotalGrids):
            print("%d (%d): note state %d - BUY %.2f - SELL %.2f - order id %d - order state %d - last buy %f - last sell %f \n" %
                   ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
                   GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
                   GridTradeNodeList[i].order_status, GridTradeNodeList[i].buy_order_executed_price, GridTradeNodeList[i].sell_order_executed_price ))


    elif ( current_price >  trail_up_trigger_price and n_trail_up_or_down < NumberOfTrailingUpGrids):
        print("------->>> Trailing UP ! current_price is %.4f, trail_up_trigger_price is %.4f " % (current_price, trail_up_trigger_price) )



        lowest_index = NumberOfTrailingDownGrids + n_trail_up_or_down
        print("Before trailing UP,  lowest_index is %d, Node states:" % (lowest_index))

        for i in range(NumberOfTotalGrids):
            print("%d (%d) - node state %d - BUY %.2f - SELL %.2f - order id %d - order state %d, last buy %f - last sell %f" %
              ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
               GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
               GridTradeNodeList[i].order_status, GridTradeNodeList[i].buy_order_executed_price, GridTradeNodeList[i].sell_order_executed_price ))

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


        trail_up_index= NumberOfTrailingDownGrids + NumberOfInitialBuyGrids  + n_trail_up_or_down

        # Short
        if (Position< -0.00001 ):
            CostForPosition -= QtyPerOrder * GridTradeNodeList[trail_up_index].sell_order_executed_price
            print("trail UP, position --, cost for postion -= %.4f, CostForPosition=%.4f " %(QtyPerOrder * GridTradeNodeList[trail_up_index].sell_order_executed_price,CostForPosition ))
            GridProfit = GridProfit + ( GridTradeNodeList[trail_up_index].sell_order_executed_price - current_price)* QtyPerOrder
            print("trail UP, position --, GridProfit += %.4f " %((current_price - GridTradeNodeList[trail_up_index].buy_order_executed_price)* QtyPerOrder ))

            Position += QtyPerOrder 
            FloatingProfit =   CostForPosition - current_price * (-1) *Position
            TotalProfit = FloatingProfit + GridProfit
        #Long
        else:
            CostForPosition += QtyPerOrder * current_price
            print("trail UP, position ++, cost for postion += %.4f, CostForPosition=%.4f " %(QtyPerOrder * current_price,CostForPosition ))

            Position += QtyPerOrder 
            FloatingProfit =   current_price *Position -CostForPosition
            TotalProfit = FloatingProfit + GridProfit


        print("%s Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f \n"
                              % (datetime.now(),matched_number, GridProfit, FloatingProfit, TotalProfit, Position))


        # TODO  check if the Market_BUY is successful
        # We suppose the Market_BUY is successful, so the price_executed is current price,i it is counted to the new Node
        highest_index= NumberOfTrailingDownGrids + NumberOfInitialBuyGrids +NumberOfInitialSellGrids+ n_trail_up_or_down -1
        GridTradeNodeList[highest_index+1].node_status = NODE_STATUS_ACTIVE



        time.sleep(7)


        # Third Step is to add a highest SELl order

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

        print("After trailing Up, new baseline_price is, node states:", baseline_price)
        for i in range(NumberOfTotalGrids):
            print("%d (%d) - node state %d - BUY %.2f - SELL %.2f - order id %d - order state %d, last buy %f - last sell %f" %
              ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
               GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id,
               GridTradeNodeList[i].order_status, GridTradeNodeList[i].buy_order_executed_price, GridTradeNodeList[i].sell_order_executed_price ))

    else:
        if( n_trail_up_or_down > NumberOfTrailingUpGrids ):
            if(ticks %10 == 1):
                print("We have hit the Trail Up limt.  n_trail_up_or_down is %d" % (n_trail_up_or_down))
        elif( (NumberOfTrailingDownGrids + n_trail_up_or_down) < 0 ):
            if(ticks %10 == 1):
                print("We have hit the Trail Down limt.  n_trail_up_or_down is %d" % (n_trail_up_or_down))



### Buying the dip






