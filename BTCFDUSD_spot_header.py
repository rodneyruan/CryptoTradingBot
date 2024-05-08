CurrentSymbol='BTCFDUSD'
QtyPerOrder  = 0.002
ProfitRate = 0.005
PRICE_PRECISION = 2
QTY_PRECISION = 4


NumberOfInitialBuyGrids = 5
NumberOfInitialSellGrids = 3
NumberOfInitialGrids = NumberOfInitialBuyGrids  + NumberOfInitialSellGrids

NumberOfTrailingDownGrids = 10
NumberOfTrailingUpGrids = 10
NumberOfTotalGrids = NumberOfInitialGrids+NumberOfTrailingUpGrids+NumberOfTrailingDownGrids


TrailDown_start_grids = 6
TrailUp_start_grids = 4

# start from -7%, -
BuyingDipStartDropPercent = 0.07
BuyingDipGridDepthPercent = 0.02
NumberOfBuyingDipGrids = 2
BuyingDipQtyPerOrder  = 0.002



MARKET_SELL_ADDITIONAL_RATE=1.0003
MARKET_BUY_ADDITIONAL_RATE= 0.9997

FIRST_PART_INITIAL_BUY_ORDER_PERCENT=1
SECOND_PART_INITIAL_BUY_ORDER_PRICE_RATE =0.995


# **********Symbol Specific Setting END <<<<<<<<<<<<<<

