import time
from binance.client import Client
#import pandas as pd
#import ta
import sys
import os
from datetime import datetime
import importlib
import traceback
from key_config import apikey
from key_config import apisecret
#apikey = ''
#apisecret = ''



if len(sys.argv) < 2:
    print("Please provide the name of the module as a command-line argument.")
    module_name = "BTCFDUSD_spot_header"
else:
    module_name = sys.argv[1]+"_spot_header"

try:
    imported_module = importlib.import_module(module_name)
    print(f"Module {module_name} imported successfully.")

except ImportError:
        print(f"Failed to import module {module_name}.")


globals().update(vars(imported_module))



io_file = CurrentSymbol+"_spot.log"


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

CurrentPrice = client.get_symbol_ticker(symbol=CurrentSymbol)
initial_price= round(float(CurrentPrice['price']) ,PRICE_PRECISION )

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
print("\n\n %s *******------------->    Trading bot started @ %.4f" %( datetime.now(), initial_price))

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

### 2 Placing Initial BUY Order

#2.1
price_to_buy = round(initial_price* MARKET_BUY_ADDITIONAL_RATE, PRICE_PRECISION)
quantity_to_buy= round(NumberOfInitialSellGrids * QtyPerOrder * FIRST_PART_INITIAL_BUY_ORDER_PERCENT,QTY_PRECISION)


order = client.order_limit_buy(symbol=CurrentSymbol, quantity=quantity_to_buy, price=price_to_buy)
order_id = order['orderId']

for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids+NumberOfInitialSellGrids):
    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE



while (True):
    time.sleep(20)
    try:
        order = client.get_order(symbol=CurrentSymbol,orderId=order_id)
        if (order['status'] == 'FILLED'):
            for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids+NumberOfInitialSellGrids):
                GridTradeNodeList[i].order_status = OrderStatus_BuyOrderFilled
                GridTradeNodeList[i].buy_order_executed_price = initial_price
                Position += QtyPerOrder
                CostForPosition += QtyPerOrder*price_to_buy
            print("First part of Initial BUY order of %.4f  @ %.4f is filled, CostForPosition=%.4f" %(quantity_to_buy, price_to_buy, CostForPosition) )

            break
        else:
            print("Order Status is ",order['status'])
    except:
        print("Exception!!! Exception occured while getting buy order status")

#2.2 
#price_to_buy = round(initial_price* SECOND_PART_INITIAL_BUY_ORDER_PRICE_RATE, PRICE_PRECISION)
#quantity_to_buy= round(NumberOfInitialSellGrids * QtyPerOrder * (1-FIRST_PART_INITIAL_BUY_ORDER_PERCENT),QTY_PRECISION)

#order = client.order_limit_buy(symbol=CurrentSymbol, quantity=quantity_to_buy, price=price_to_buy)
#order_id = order['orderId']
#print("Second part of Initial BUY order of %.4f  @ %.4f is placed", quantity_to_buy, price_to_buy)



#2.3
for i in range(NumberOfTrailingDownGrids, NumberOfTrailingDownGrids+NumberOfInitialBuyGrids):
    GridTradeNodeList[i].node_status = NODE_STATUS_ACTIVE
    price_to_buy= GridTradeNodeList[i].price_buy
    order = client.order_limit_buy(symbol=CurrentSymbol, quantity=QtyPerOrder, price=price_to_buy)
    GridTradeNodeList[i].order_id = order['orderId']
    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
    time.sleep(1)

###


### 3 Placing Initial Sell Order
for i in range(NumberOfTrailingDownGrids+NumberOfInitialBuyGrids, NumberOfTrailingDownGrids + NumberOfInitialBuyGrids + NumberOfInitialSellGrids):
    price_to_sell = GridTradeNodeList[i].price_sell
    order = client.order_limit_sell(symbol=CurrentSymbol, quantity=QtyPerOrder, price=price_to_sell)
    GridTradeNodeList[i].order_id = order['orderId']
    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
    time.sleep(2)


for i in range(NumberOfTotalGrids):
    print("%d(%d) - node state %d - BUY %.2f - SELL %.2f order id %d - order state %d" %
       ( i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids, GridTradeNodeList[i].node_status,
       GridTradeNodeList[i].price_buy,GridTradeNodeList[i].price_sell, GridTradeNodeList[i].order_id, GridTradeNodeList[i].order_status ))


### 3 Placing Initial Buying Dip Orders


for i in range(NumberOfBuyingDipGrids):
    price_to_buy = round( initial_price * (1 - BuyingDipStartDropPercent - BuyingDipGridDepthPercent* i),PRICE_PRECISION  )
    order = client.order_limit_buy(symbol=CurrentSymbol, quantity=BuyingDipQtyPerOrder, price=price_to_buy)
    percent_rate = ((price_to_buy- initial_price)/initial_price )*100
    print("Placing a buying dip order, %.4f  price_to_buy %.2f%% " % ( price_to_buy, percent_rate) )

    time.sleep(1)



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
            order = client.get_order(symbol=CurrentSymbol,orderId=order_id)
        except:
            print("%d : exception occured while getting order status, order_id =%d  " % (i,order_id))
            print(traceback.format_exc())
            continue

        if (order['status'] == 'FILLED'):
            try:
                CurrentPrice = client.get_symbol_ticker(symbol=CurrentSymbol)
                current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )
            except:
                print(traceback.format_exc())
                print("Failed to get current price")

            if (GridTradeNodeList[i].order_status == OrderStatus_SellOrderPlaced):
                GridTradeNodeList[i].sell_order_executed_price =  GridTradeNodeList[i].price_sell
                Position -= QtyPerOrder
                print("Before,, CostForPosition is %.4f, GridProfit is %.4f" %(CostForPosition, GridProfit))
                CostForPosition -= QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price
                GridProfit = GridProfit + (GridTradeNodeList[i].sell_order_executed_price - GridTradeNodeList[i].buy_order_executed_price)* QtyPerOrder
                print("After, CostForPosition is %.4f, GridProfit is %.4f " %(CostForPosition, GridProfit))

                new_matched=1
                matched_number +=1

                price_to_buy = GridTradeNodeList[i].price_buy
                if( price_to_buy > current_price):
                    price_to_buy = current_price
                    print(" ############## >>>>>>> Special case, that price_to_buy is higher than current price, using %.4f" 
                          %(current_price)) 

                print("%s %d (%d):   SELL Order Filled at %.2f,  Profit +++++++++++++++   placing a new BUY at --> %.2f  TotalMatched :%d  "
                % ( current_time,i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids ,
                    GridTradeNodeList[i].sell_order_executed_price, price_to_buy, matched_number))


                try:
                    order = client.order_limit_buy(symbol=CurrentSymbol, quantity=QtyPerOrder, price=price_to_buy)
                    GridTradeNodeList[i].order_id = order['orderId']
                    GridTradeNodeList[i].order_status = OrderStatus_BuyOrderPlaced
                except:
                    rint(traceback.format_exc())
                    print("%s  %d (%d) Failed to place a new BUY order at price %.4f .\n"
                            %(current_time, i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids,price_to_buy))

                # print the profits
                try:
                    FloatingProfit = current_price * Position - CostForPosition
                    TotalProfit = FloatingProfit + GridProfit

                    print(" CostForPosition -- %.4f, CostForPosition =%.4f, Position=%.4f, current_price=%.4f, current_position_value =%.4f \n"
                                        % (QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price, CostForPosition,Position,current_price,Position*current_price))

                    print("Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f \n"
                        % (matched_number, GridProfit, FloatingProfit, TotalProfit, Position))


                        
                except:
                    print("Failed to get print profits" )



            elif (GridTradeNodeList[i].order_status == OrderStatus_BuyOrderPlaced):

                GridTradeNodeList[i].buy_order_executed_price =  GridTradeNodeList[i].price_buy
                Position += QtyPerOrder
                print("Before++, CostForPosition is %.4f" %(CostForPosition))
                CostForPosition += QtyPerOrder*GridTradeNodeList[i].price_buy
                print("After++, CostForPosition is %.4f" %(CostForPosition))


                price_to_sell = GridTradeNodeList[i].price_sell
                if( price_to_sell < current_price):
                    price_to_sell = current_price
                    print(" ############## >>>>>>> Special case, that price_to_sell is lower than current price, using %.4f" 
                          %(current_price)) 

                print("%s %d (%d):  BUY Order Filled at %.2f, placing a new SELL at --> %.2f ."
                    % (current_time,  i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids , GridTradeNodeList[i].buy_order_executed_price, price_to_sell))

                try:
                    order = client.order_limit_sell(symbol=CurrentSymbol, quantity=QtyPerOrder, price=price_to_sell)
                    GridTradeNodeList[i].order_id = order['orderId']
                    GridTradeNodeList[i].order_status = OrderStatus_SellOrderPlaced
                except:
                    print(traceback.format_exc())
                    print("%s  %d (%d) Failed to place a new SELL order at price %.4f \n"
                           %(current_time, i, i-NumberOfTrailingDownGrids-NumberOfInitialBuyGrids,price_to_sell))

                # print the profits
                try:
                    CurrentPrice = client.get_symbol_ticker(symbol=CurrentSymbol)
                    current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )
                    FloatingProfit = current_price * Position - CostForPosition
                    TotalProfit = FloatingProfit + GridProfit

                    print(" CostForPosition ++ %.4f, CostForPosition =%.4f, Position=%.4f, current_price=%.4f, current_position_value =%.4f \n"
                                        % (QtyPerOrder * GridTradeNodeList[i].buy_order_executed_price, CostForPosition,Position,current_price,Position*current_price))

                    print("Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f \n"
                        % (matched_number, GridProfit, FloatingProfit, TotalProfit, Position))

                except:
                    print(traceback.format_exc())
                    print("%s Failed to get current price" %(current_time) );

 
    try:
        CurrentPrice = client.get_symbol_ticker(symbol=CurrentSymbol)
    except:
        print(traceback.format_exc())
        continue
    current_price = round( float(CurrentPrice['price']),PRICE_PRECISION )



    #Need to trail up or down?
    trail_down_trigger_price = baseline_price - TrailDown_start_grids * grid_depth
    trail_up_trigger_price = baseline_price + TrailUp_start_grids * grid_depth

    if ( current_price <  trail_down_trigger_price  and  (NumberOfTrailingDownGrids + n_trail_up_or_down) > 0 ):
        print("%s, <<<<------- Trailing down! current_price is %.4f, trail_down_trigger_price is %.4f " % (datetime.now(),current_price, trail_down_trigger_price) )



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
                order = client.get_order(symbol=CurrentSymbol,orderId=order_id)
                print("Trailing down, highest order status is ", order['status']  )

                if( order['status'] == 'NEW'):
                    client.cancel_order(symbol=CurrentSymbol, orderId=order_id)
                    need_to_sell_for_trail_down = 1
            except:
                print(traceback.format_exc())
                print("Fail to cancel the highest SELL Order,  highest_index = %d, order_id = %d " % (highest_index,order_id))

        time.sleep(60)



        # 4.2 Second step is to sell QtyPerOrder
        retry_counter = 3

        if( need_to_sell_for_trail_down == 1 ):
            try:
                price_to_sell = round(current_price * MARKET_SELL_ADDITIONAL_RATE, PRICE_PRECISION)
                order = client.order_limit_sell(symbol=CurrentSymbol, quantity=QtyPerOrder, price=price_to_sell)

                order_id = order['orderId']

                while( retry_counter > 0 ):
                    time.sleep(60)
                    order = client.get_order(symbol=CurrentSymbol,orderId=order_id)
                    if( order['status'] == 'FILLED'):
                        break
                    elif(retry_counter == 1):
                        #client.order_market_sell(symbol=CurrentSymbol, quantity=QtyPerOrder)
                        print("%s Trailing down, the LIMIT-SELL order is not executed after 3 minites, price @ %.4f" 
                              %(datetime.now(), price_to_sell))
                    retry_counter -=1
            except:
                print(traceback.format_exc())
                print("Trailing down, failed to sell at current price for trailing down ")



        # TODO  check if it is really sold?
        #The market sell is counted to the highest active node., we suppose it is sold at current_price
        GridTradeNodeList[highest_index].sell_order_executed_price = current_price
        Position -= QtyPerOrder
        print("Before trail down, CostForPosition is %.4f, GridProfit is %.4f"
             % (CostForPosition, GridProfit))
        CostForPosition -= QtyPerOrder * GridTradeNodeList[highest_index].buy_order_executed_price
        profit_for_market_sell = (GridTradeNodeList[highest_index].sell_order_executed_price - GridTradeNodeList[highest_index].buy_order_executed_price)* QtyPerOrder
        GridProfit = GridProfit + profit_for_market_sell
        FloatingProfit = current_price * Position - CostForPosition
        TotalProfit = FloatingProfit + GridProfit


        print("TrailDown,the canceld grid is sold at %.4f  single_grid_profit = %.4f" 
        %(GridTradeNodeList[highest_index].sell_order_executed_price, profit_for_market_sell ))

        print("After TrailDown-> costForPosition-= %.4f  costForPosition=  %.4f, Position = %.4f current_price=%.4f, current_position_value =%.4f " 
        %(QtyPerOrder * GridTradeNodeList[highest_index].buy_order_executed_price,CostForPosition, Position,current_price,Position*current_price ))


        
        print("After TrailDown-> Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f \n"
            % (matched_number, GridProfit, FloatingProfit, TotalProfit, Position))



        GridTradeNodeList[highest_index].node_status = NODE_STATUS_INACTIVE
        time.sleep(5)



        # 4.3  Third Step is to add a lowest BUY order
        lowest_index= NumberOfTrailingDownGrids + n_trail_up_or_down
        price_to_buy = GridTradeNodeList[lowest_index-1].price_buy

        try:
            order = client.order_limit_buy(symbol=CurrentSymbol, quantity=QtyPerOrder, price=round(price_to_buy,PRICE_PRECISION))
            order_id = order['orderId']
            GridTradeNodeList[lowest_index-1].order_id = order_id
            GridTradeNodeList[lowest_index-1].order_status = OrderStatus_BuyOrderPlaced
            GridTradeNodeList[lowest_index-1].node_status = NODE_STATUS_ACTIVE

        except:
            print(traceback.format_exc())
            print("%s Trailing down, Failed to place a new lowest order at %.4f, index is %d .\n"
                 %(current_time,price_to_buy, lowest_index-1))

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

        time.sleep(2)

        # 5.1 First Step for trailing UP, is to cancel the lowest BUY Order
        need_to_buy_for_trail_up = 0

        if( GridTradeNodeList[lowest_index].order_status == OrderStatus_BuyOrderPlaced):
            order_id = GridTradeNodeList[lowest_index].order_id
            try:
                order = client.get_order(symbol=CurrentSymbol,orderId=order_id)
                print("Trailing UP, lowest order status is ", order['status']  )

                if( order['status'] == 'NEW'):
                    client.cancel_order(symbol=CurrentSymbol, orderId=order_id)
                    need_to_buy_for_trail_up = 1
            except:
                print(traceback.format_exc())
                print("Fail to cancel the lowest BUY Order,  highest_index = %d, order_id = %d " % (lowest_index,order_id))

        GridTradeNodeList[lowest_index].node_status = NODE_STATUS_INACTIVE

        time.sleep(15)


        #5.2 Second step is to sell QtyPerOrder
        retry_counter = 3

        if( need_to_buy_for_trail_up == 1 ):
            try:
                price_to_buy = round(current_price*MARKET_BUY_ADDITIONAL_RATE, PRICE_PRECISION)
                order = client.order_limit_buy(symbol=CurrentSymbol, price=price_to_buy, quantity=QtyPerOrder)

                order_id = order['orderId']

                while( retry_counter > 0 ):
                    time.sleep(60)
                    order = client.get_order(symbol=CurrentSymbol,orderId=order_id)
                    if( order['status'] == 'FILLED'):
                        break
                    elif(retry_counter == 1):
                        #client.order_market_buy(symbol=CurrentSymbol, quantity=QtyPerOrder)
                        print("%s Trailing UP, the LIMIT-BUY order is not executed after 3 minites @ %.4f" 
                              %(datetime.now(), price_to_buy))
                    retry_counter -=1

            except:
                print(traceback.format_exc())
                print("Trailing down, failed to sell at current price for trailing down ")



        # TODO  check if the Market_BUY is successful
        # We suppose the Market_BUY is successful, so the price_executed is current price,i it is counted to the new Node
        highest_index= NumberOfTrailingDownGrids + NumberOfInitialBuyGrids +NumberOfInitialSellGrids+ n_trail_up_or_down -1
        GridTradeNodeList[highest_index+1].node_status = NODE_STATUS_ACTIVE
        GridTradeNodeList[highest_index+1].buy_order_executed_price = current_price

        Position += QtyPerOrder
        print("Before trail UP, CostForPosition is %.4f"    % (CostForPosition))
        CostForPosition += QtyPerOrder * GridTradeNodeList[highest_index+1].buy_order_executed_price


        FloatingProfit = current_price * Position - CostForPosition
        TotalProfit = FloatingProfit + GridProfit

        print("Trail UP,the new grid: buy price %.4f " 
        %(GridTradeNodeList[highest_index+1].buy_order_executed_price ))
        print("After TrailUP-> CostForPosition +=%.4f, CostForPosition =%.4f, Position=%.4f, current_price=%.4f, current_position_value =%.4f \n"
                            % (QtyPerOrder * GridTradeNodeList[highest_index+1].buy_order_executed_price, 
                               CostForPosition,Position,current_price,Position*current_price))
        
        print("After TrailUP-> Matched times:%d  Grid Profit is %.4f, FloatingProfit is %.4f, Total Profit is %.4f, position is %.4f \n"
            % (matched_number, GridProfit, FloatingProfit, TotalProfit, Position))


        time.sleep(7)


        #5.3  Third Step is to add a highest SELl order

        price_to_sell = GridTradeNodeList[highest_index+1].price_sell

        try:
            order=client.order_limit_sell(symbol=CurrentSymbol, quantity=QtyPerOrder, price=round(price_to_sell,PRICE_PRECISION) )
            order_id = order['orderId']
            GridTradeNodeList[highest_index+1].order_id = order_id
            GridTradeNodeList[highest_index+1].order_status = OrderStatus_SellOrderPlaced

        except:
            print(traceback.format_exc())
            print("%s Trailing up, Failed to place a new highest SELL order at %.4f, index is %d .\n"
                           %(current_time,price_to_sell, highest_index+1))


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




