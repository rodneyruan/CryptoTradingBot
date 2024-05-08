# ********** Symbol Specific Setting START >>>>>>>>>>>>
#  1.5% *5 = 7.5%， 6% start to move,  move 15%
# init 5 grid, 1%， move down 8 grids， most -13%， -15% buy dip
CurrentSymbol='ETHFDUSD'
QtyPerOrder  = 0.02
ProfitRate = 0.01
PRICE_PRECISION = 2
QTY_PRECISION = 4


NumberOfInitialBuyGrids = 6
NumberOfInitialSellGrids = 4
NumberOfInitialGrids = NumberOfInitialBuyGrids  + NumberOfInitialSellGrids

NumberOfTrailingDownGrids = 8
NumberOfTrailingUpGrids = 8
NumberOfTotalGrids = NumberOfInitialGrids+NumberOfTrailingUpGrids+NumberOfTrailingDownGrids


TrailDown_start_grids = 7
TrailUp_start_grids = 5

# start from -15%, -18%, -21% 0.02 ETH,~ 63U/grid, 3 grids, total around 200U
BuyingDipStartDropPercent = 0.15
BuyingDipGridDepthPercent = 0.03
NumberOfBuyingDipGrids = 3
BuyingDipQtyPerOrder  = 0.02


MARKET_SELL_ADDITIONAL_RATE=1.0003
MARKET_BUY_ADDITIONAL_RATE= 0.9997

FIRST_PART_INITIAL_BUY_ORDER_PERCENT=1
SECOND_PART_INITIAL_BUY_ORDER_PRICE_RATE =0.995


# **********Symbol Specific Setting END <<<<<<<<<<<<<<
