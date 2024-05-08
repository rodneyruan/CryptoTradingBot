# ********** Symbol Specific Setting START >>>>>>>>>>>>
#  1.5% *5 = 7.5%， 6% start to move,  move 15%
CurrentSymbol='RNDRFDUSD'
QtyPerOrder  = 10
ProfitRate = 0.015
PRICE_PRECISION = 2
QTY_PRECISION = 2


NumberOfInitialBuyGrids = 5
NumberOfInitialSellGrids = 5
NumberOfInitialGrids = NumberOfInitialBuyGrids  + NumberOfInitialSellGrids

NumberOfTrailingDownGrids = 5
NumberOfTrailingUpGrids = 5
NumberOfTotalGrids = NumberOfInitialGrids+NumberOfTrailingUpGrids+NumberOfTrailingDownGrids



TrailDown_start_grids = 4
TrailUp_start_grids = 4

# start from -16%,  18% 21%
BuyingDipStartDropPercent = 0.16
BuyingDipGridDepthPercent = 0.03
NumberOfBuyingDipGrids = 2
# 6*8=48 U,  48*3
BuyingDipQtyPerOrder  = 5

MARKET_SELL_ADDITIONAL_RATE=1.0003
MARKET_BUY_ADDITIONAL_RATE= 0.9997

FIRST_PART_INITIAL_BUY_ORDER_PERCENT=1
SECOND_PART_INITIAL_BUY_ORDER_PRICE_RATE =0.995


# **********Symbol Specific Setting END <<<<<<<<<<<<<<
