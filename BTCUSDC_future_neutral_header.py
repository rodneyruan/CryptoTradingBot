# ********** Symbol Specific Setting START >>>>>>>>>>>>

# init 5 grid, 1%， move down 8 grids， most -13%， -15% buy dip
CurrentSymbol='BTCUSDC'

LEVERAGE = 3


MARGIN_TYPE= 'ISOLATED'


QtyPerOrder  = 0.003
ProfitRate = 0.005
PRICE_PRECISION = 1
QTY_PRECISION = 3


NumberOfInitialBuyGrids = 4
NumberOfInitialSellGrids = 4
NumberOfInitialGrids = NumberOfInitialBuyGrids  + NumberOfInitialSellGrids

NumberOfTrailingDownGrids = 10
NumberOfTrailingUpGrids = 10
NumberOfTotalGrids = NumberOfInitialGrids+NumberOfTrailingUpGrids+NumberOfTrailingDownGrids


TrailDown_start_grids = 4
TrailUp_start_grids = 4

# start from -7%, -
MARKET_SELL_ADDITIONAL_RATE=1.0003
MARKET_BUY_ADDITIONAL_RATE= 0.9997


# **********Symbol Specific Setting END <<<<<<<<<<<<<<
