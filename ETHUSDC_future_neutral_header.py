# ********** Symbol Specific Setting START >>>>>>>>>>>>

# init 5 grid, 1%， move down 8 grids， most -13%， -15% buy dip
CurrentSymbol='ETHUSDC'

LEVERAGE = 3


MARGIN_TYPE= 'ISOLATED'


QtyPerOrder  = 0.02
ProfitRate = 0.005
PRICE_PRECISION = 2
QTY_PRECISION = 3


NumberOfInitialBuyGrids = 5
NumberOfInitialSellGrids = 5
NumberOfInitialGrids = NumberOfInitialBuyGrids  + NumberOfInitialSellGrids

NumberOfTrailingDownGrids = 8
NumberOfTrailingUpGrids = 8
NumberOfTotalGrids = NumberOfInitialGrids+NumberOfTrailingUpGrids+NumberOfTrailingDownGrids


TrailDown_start_grids = 5
TrailUp_start_grids = 5

# start from -7%, -
BuyingDipStartDropPercent = 0.07
BuyingDipGridDepthPercent = 0.02
NumberOfBuyingDipGrids = 2
BuyingDipQtyPerOrder  = 0.002
MARKET_SELL_ADDITIONAL_RATE=1.0003
MARKET_BUY_ADDITIONAL_RATE= 0.9997

FIRST_PART_INITIAL_SELL_ORDER_PERCENT=0.6
SECOND_PART_INITIAL_SELL_PRICE_RATE=1.005

# **********Symbol Specific Setting END <<<<<<<<<<<<<<
