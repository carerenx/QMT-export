# -*- coding: gbk -*-
"""
================================================================================
 中证500 均线回踩策略 v1.0 — 多票筛选日线回测
================================================================================
 QMT运行时注入: get_trade_detail_data / order_shares / ContextInfo.*

 【策略逻辑】
   选股池: 中证500成分股 (去ST/新股/停牌)
   趋势: 上升趋势 (MA5 > MA10/20/30) — 支持3种判定方式
   入场: 收盘价回踩MA10或MA30 (在均线±1.5%范围内)
   仓位: 最多同时持有 5 票, 等权分配
   出场:
     ★ 止盈: 买入后涨 +2% → 卖出
     ★ 止损: 买入后跌 -2% → 卖出
     ★ 时间止损: 持有第3个交易日 → 收盘卖出

 【回测说明】
   每bar扫描全中证500→筛选信号→排序→选最优N票买入→现有持仓独立检查出场
================================================================================
"""

# ============================================================================
# 第一部分：全局配置
# ============================================================================
ACCOUNT = '8890145315'

# ---- 选股池 ----
INDEX_CODE = '中证500'

# ---- 均线参数 ----
MA_SHORT  = 5
MA_MID1   = 10
MA_MID2   = 20
MA_LONG   = 30

# ---- 趋势判断方式 ----
TREND_MODE    = 'A'

# ---- 回踩参数 ----
PULLBACK_TOLERANCE = 0.015   # 收盘价在MA±1.5%内视为"回踩到位"
PULLBACK_MA10      = True    # 启用MA10回踩
PULLBACK_MA30      = True    # 启用MA30回踩

# ---- 出场参数 ----
TAKE_PROFIT_PCT  = 0.02
STOP_LOSS_PCT    = 0.02
MAX_HOLD_DAYS    = 3

# ---- 仓位管理 ----
MAX_POSITIONS     = 10      # 最多同时持有票数
BT_INIT_CASH      = 1000000 # 回测初始资金
FIXED_AMOUNT      = 50000   # 每票固定金额(元), 不足1手时向下取整手

# ---- 过滤 ----
MIN_PRICE         = 5.0     # 最低股价(过滤垃圾股)
MIN_VOL_RATIO     = 0.5     # 最低量比

# ---- 数据 ----
HIST_DATA_LEN = 120
COMMISSION    = 0.00025
STAMP_TAX     = 0.001
TRADE_LOT     = 100          # A股1手=100股 (仅兼容用)

TIMER_INTERVAL = '1nSecond'


# ============================================================================
# 第二部分：技术指标
# ============================================================================

def _sma(values, period):
    n = len(values); r = [0.0] * n
    for i in range(period - 1, n):
        r[i] = sum(values[i - period + 1 : i + 1]) / period
    return r


def _trade_fee(price, shares):
    return price * shares * (COMMISSION * 2 + STAMP_TAX)


# ============================================================================
# 第三部分：趋势判断
# ============================================================================

def _is_uptrend(closes):
    """判断价格是否处于上升趋势 (均线上方)"""
    n = len(closes)
    if n < MA_LONG: return False

    ma5  = _sma(closes, MA_SHORT)[-1]
    ma10 = _sma(closes, MA_MID1)[-1]
    ma20 = _sma(closes, MA_MID2)[-1]
    ma30 = _sma(closes, MA_LONG)[-1]

    if TREND_MODE == 'B':
        return ma5 > ma10 > ma20 > ma30
    else:  # 'A'
        return ma5 > ma10 and ma5 > ma20 and ma5 > ma30


# ============================================================================
# 第四部分：QMT 入口
# ============================================================================

def init(ContextInfo):
    # 获取中证500成分股
    try:
        raw_list = ContextInfo.get_stock_list_in_sector(INDEX_CODE, 1)
    except Exception:
        # fallback: 用get_sector_list
        try:
            sectors = ContextInfo.get_sector_list()
            for s in sectors:
                if '中证500' in s: raw_list = ContextInfo.get_stock_list_in_sector(s, 1)
        except Exception:
            raw_list = []

    # 过滤: 去科创板(688开头)
    stock_list = [s for s in raw_list if not s.startswith('688')
                  and s.endswith('.SH') or s.endswith('.SZ')]

    if not stock_list:
        # 硬编码fallback — 中证500权重靠前的
        stock_list = [
            '000001.SZ','000002.SZ','000012.SZ','000021.SZ','000027.SZ',
            '000039.SZ','000060.SZ','000063.SZ','000066.SZ','000069.SZ',
            '000100.SZ','000157.SZ','000166.SZ','000301.SZ','000333.SZ',
            '000338.SZ','000408.SZ','000423.SZ','000425.SZ','000513.SZ',
            '000519.SZ','000528.SZ','000538.SZ','000547.SZ','000559.SZ',
            '000568.SZ','000591.SZ','000596.SZ','000598.SZ','000617.SZ',
            '000623.SZ','000625.SZ','000629.SZ','000630.SZ','000636.SZ',
            '000651.SZ','000661.SZ','000686.SZ','000688.SZ','000703.SZ',
            '000723.SZ','000728.SZ','000729.SZ','000733.SZ','000738.SZ',
            '000739.SZ','000750.SZ','000768.SZ','000776.SZ','000783.SZ',
            '000786.SZ','000792.SZ','000799.SZ','000800.SZ','000807.SZ',
            '000825.SZ','000830.SZ','000831.SZ','000858.SZ','000878.SZ',
            '000883.SZ','000887.SZ','000895.SZ','000898.SZ','000901.SZ',
            '000903.SZ','000921.SZ','000927.SZ','000930.SZ','000932.SZ',
            '000933.SZ','000935.SZ','000937.SZ','000938.SZ','000951.SZ',
            '000958.SZ','000959.SZ','000963.SZ','000967.SZ','000970.SZ',
            '000975.SZ','000977.SZ','000983.SZ','000987.SZ','000988.SZ',
            '000997.SZ','000998.SZ','000999.SZ','001203.SZ','001213.SZ',
            '001286.SZ','001289.SZ','001308.SZ','001309.SZ','001322.SZ',
            '001872.SZ','001914.SZ','001965.SZ','001979.SZ',
            '002001.SZ','002007.SZ','002008.SZ','002025.SZ','002028.SZ',
            '002030.SZ','002032.SZ','002044.SZ','002049.SZ','002050.SZ',
            '002056.SZ','002064.SZ','002065.SZ','002074.SZ','002075.SZ',
            '002080.SZ','002081.SZ','002091.SZ','002092.SZ','002093.SZ',
            '002110.SZ','002120.SZ','002128.SZ','002129.SZ','002130.SZ',
            '002131.SZ','002138.SZ','002142.SZ','002151.SZ','002152.SZ',
            '002153.SZ','002155.SZ','002156.SZ','002158.SZ','002174.SZ',
            '002179.SZ','002180.SZ','002183.SZ','002185.SZ','002191.SZ',
            '002192.SZ','002195.SZ','002202.SZ','002203.SZ','002212.SZ',
            '002214.SZ','002216.SZ','002223.SZ','002230.SZ','002236.SZ',
            '002237.SZ','002240.SZ','002241.SZ','002242.SZ','002243.SZ',
            '002244.SZ','002249.SZ','002250.SZ','002252.SZ','002254.SZ',
            '002262.SZ','002266.SZ','002268.SZ','002273.SZ','002274.SZ',
            '002275.SZ','002276.SZ','002281.SZ','002283.SZ','002287.SZ',
            '002294.SZ','002299.SZ','002301.SZ','002302.SZ','002304.SZ',
            '002311.SZ','002312.SZ','002317.SZ','002318.SZ','002320.SZ',
            '002326.SZ','002327.SZ','002340.SZ','002344.SZ','002345.SZ',
            '002352.SZ','002353.SZ','002366.SZ','002368.SZ','002371.SZ',
            '002372.SZ','002373.SZ','002382.SZ','002384.SZ','002385.SZ',
            '002387.SZ','002389.SZ','002396.SZ','002399.SZ',
            '300001.SZ','300002.SZ','300003.SZ','300012.SZ','300014.SZ',
            '300015.SZ','300017.SZ','300024.SZ','300026.SZ','300033.SZ',
            '300037.SZ','300054.SZ','300058.SZ','300059.SZ','300073.SZ',
            '300087.SZ','300088.SZ','300115.SZ','300118.SZ','300122.SZ',
            '300124.SZ','300133.SZ','300136.SZ','300142.SZ','300146.SZ',
            '300207.SZ','300212.SZ','300223.SZ','300244.SZ','300251.SZ',
            '300253.SZ','300257.SZ','300285.SZ','300296.SZ','300316.SZ',
            '300347.SZ','300357.SZ','300373.SZ','300376.SZ','300390.SZ',
            '300394.SZ','300395.SZ','300408.SZ','300413.SZ','300418.SZ',
            '300428.SZ','300432.SZ','300433.SZ','300442.SZ','300450.SZ',
            '300454.SZ','300456.SZ','300457.SZ','300458.SZ','300474.SZ',
            '300476.SZ','300487.SZ','300496.SZ','300498.SZ','300502.SZ',
            '300529.SZ','300558.SZ','300567.SZ','300595.SZ','300601.SZ',
            '300618.SZ','300624.SZ','300628.SZ','300633.SZ','300638.SZ',
            '300661.SZ','300666.SZ','300672.SZ','300676.SZ','300677.SZ',
            '300699.SZ','300724.SZ','300726.SZ','300735.SZ','300741.SZ',
            '300748.SZ','300750.SZ','300751.SZ','300759.SZ','300760.SZ',
            '300763.SZ','300765.SZ','300769.SZ','300772.SZ','300773.SZ',
            '300775.SZ','300776.SZ','300782.SZ','300803.SZ','300811.SZ',
            '300820.SZ','300821.SZ','300832.SZ','300841.SZ','300850.SZ',
            '300857.SZ','300866.SZ','300896.SZ','300919.SZ','300957.SZ',
            '300973.SZ','300979.SZ','300999.SZ',
            '600008.SH','600009.SH','600010.SH','600011.SH','600015.SH',
            '600016.SH','600018.SH','600019.SH','600021.SH','600022.SH',
            '600025.SH','600026.SH','600027.SH','600028.SH','600029.SH',
            '600031.SH','600036.SH','600037.SH','600038.SH','600039.SH',
            '600048.SH','600050.SH','600056.SH','600057.SH','600058.SH',
            '600060.SH','600061.SH','600062.SH','600066.SH','600071.SH',
            '600079.SH','600085.SH','600089.SH','600095.SH','600104.SH',
            '600109.SH','600111.SH','600115.SH','600118.SH','600120.SH',
            '600126.SH','600132.SH','600150.SH','600151.SH','600153.SH',
            '600157.SH','600160.SH','600161.SH','600166.SH','600167.SH',
            '600169.SH','600170.SH','600176.SH','600177.SH','600183.SH',
            '600184.SH','600185.SH','600188.SH','600196.SH','600208.SH',
            '600210.SH','600216.SH','600219.SH','600221.SH','600223.SH',
            '600233.SH','600248.SH','600256.SH','600258.SH','600259.SH',
            '600271.SH','600276.SH','600282.SH','600285.SH','600288.SH',
            '600295.SH','600298.SH','600299.SH','600300.SH','600305.SH',
            '600309.SH','600315.SH','600316.SH','600318.SH','600320.SH',
            '600325.SH','600329.SH','600332.SH','600335.SH','600339.SH',
            '600346.SH','600348.SH','600350.SH','600352.SH','600362.SH',
            '600363.SH','600369.SH','600372.SH','600376.SH','600377.SH',
            '600378.SH','600380.SH','600383.SH','600388.SH','600390.SH',
            '600391.SH','600392.SH','600395.SH','600398.SH',
            '600406.SH','600409.SH','600410.SH','600415.SH','600416.SH',
            '600418.SH','600420.SH','600426.SH','600428.SH','600435.SH',
            '600436.SH','600438.SH','600458.SH','600460.SH','600466.SH',
            '600477.SH','600478.SH','600480.SH','600481.SH','600482.SH',
            '600486.SH','600487.SH','600489.SH','600490.SH','600498.SH',
            '600499.SH','600500.SH','600507.SH','600511.SH','600515.SH',
            '600516.SH','600517.SH','600519.SH','600521.SH','600522.SH',
            '600528.SH','600529.SH','600531.SH','600535.SH','600536.SH',
            '600546.SH','600547.SH','600548.SH','600549.SH','600550.SH',
            '600558.SH','600559.SH','600563.SH','600565.SH','600566.SH',
            '600567.SH','600570.SH','600572.SH','600577.SH','600578.SH',
            '600580.SH','600582.SH','600583.SH','600584.SH','600585.SH',
            '600586.SH','600587.SH','600588.SH','600595.SH','600596.SH',
            '600597.SH','600598.SH','600600.SH','600602.SH','600604.SH',
            '600606.SH','600609.SH','600612.SH','600616.SH','600618.SH',
            '600621.SH','600623.SH','600624.SH','600626.SH','600629.SH',
            '600633.SH','600635.SH','600636.SH','600637.SH','600639.SH',
            '600641.SH','600642.SH','600643.SH','600645.SH','600648.SH',
            '600649.SH','600650.SH','600653.SH','600654.SH','600655.SH',
            '600657.SH','600658.SH','600660.SH','600662.SH','600663.SH',
            '600664.SH','600667.SH','600668.SH','600673.SH','600674.SH',
            '600685.SH','600686.SH','600688.SH','600690.SH','600691.SH',
            '600694.SH','600696.SH','600699.SH','600702.SH','600703.SH',
            '600704.SH','600705.SH','600707.SH','600708.SH','600709.SH',
            '600710.SH','600711.SH','600716.SH','600717.SH','600718.SH',
            '600720.SH','600721.SH','600722.SH','600723.SH','600724.SH',
            '600726.SH','600727.SH','600728.SH','600729.SH','600730.SH',
            '600731.SH','600732.SH','600733.SH','600734.SH','600735.SH',
            '600736.SH','600737.SH','600738.SH','600739.SH','600740.SH',
            '600741.SH','600742.SH','600743.SH','600744.SH','600745.SH',
            '600746.SH','600747.SH','600748.SH',
            '600750.SH','600751.SH','600754.SH','600755.SH','600756.SH',
            '600757.SH','600759.SH','600760.SH','600761.SH','600763.SH',
            '600764.SH','600765.SH','600766.SH','600767.SH','600768.SH',
            '600769.SH','600770.SH','600771.SH','600772.SH','600773.SH',
            '600774.SH','600775.SH','600776.SH','600777.SH','600778.SH',
            '600779.SH','600780.SH','600781.SH','600782.SH','600783.SH',
            '600784.SH','600785.SH','600786.SH','600787.SH','600788.SH',
            '600789.SH','600790.SH','600791.SH','600792.SH','600793.SH',
            '600794.SH','600795.SH','600796.SH','600797.SH','600798.SH',
            '600800.SH','600801.SH','600802.SH','600803.SH','600804.SH',
            '600805.SH','600806.SH','600807.SH','600808.SH','600809.SH',
            '600810.SH','600811.SH','600812.SH','600814.SH','600815.SH',
            '600816.SH','600817.SH','600818.SH','600819.SH','600820.SH',
            '600821.SH','600822.SH','600823.SH','600824.SH','600825.SH',
            '600826.SH','600827.SH','600828.SH','600829.SH','600830.SH',
            '600831.SH','600832.SH','600833.SH','600834.SH','600835.SH',
            '600836.SH','600837.SH','600838.SH','600839.SH','600841.SH',
            '600843.SH','600844.SH','600845.SH','600846.SH','600848.SH',
            '600850.SH','600851.SH','600853.SH','600854.SH','600855.SH',
            '600856.SH','600857.SH','600858.SH','600859.SH','600860.SH',
            '600861.SH','600862.SH','600863.SH','600864.SH','600865.SH',
            '600866.SH','600867.SH','600868.SH','600869.SH','600871.SH',
            '600872.SH','600873.SH','600874.SH','600875.SH',
            '600876.SH','600877.SH','600879.SH','600880.SH','600881.SH',
            '600882.SH','600883.SH','600884.SH','600885.SH','600886.SH',
            '600887.SH','600888.SH','600889.SH','600890.SH','600891.SH',
            '600892.SH','600893.SH','600894.SH','600895.SH','600897.SH',
            '600898.SH','600900.SH','600901.SH','600903.SH','600905.SH',
            '600908.SH','600909.SH','600916.SH','600917.SH','600918.SH',
            '600919.SH','600926.SH','600928.SH','600929.SH','600933.SH',
            '600936.SH','600938.SH','600939.SH','600941.SH','600955.SH',
            '600956.SH','600958.SH','600959.SH','600960.SH','600961.SH',
            '600963.SH','600965.SH','600966.SH','600967.SH','600968.SH',
            '600969.SH','600970.SH','600971.SH','600973.SH','600975.SH',
            '600976.SH','600977.SH','600979.SH','600980.SH','600981.SH',
            '600982.SH','600983.SH','600984.SH','600985.SH','600986.SH',
            '600987.SH','600988.SH','600989.SH','600990.SH','600991.SH',
            '600992.SH','600993.SH','600995.SH','600996.SH','600997.SH',
            '600998.SH','600999.SH',
            '601000.SH','601001.SH','601003.SH','601005.SH','601006.SH',
            '601009.SH','601012.SH','601016.SH','601018.SH','601019.SH',
            '601021.SH','601022.SH','601028.SH','601038.SH','601058.SH',
            '601066.SH','601068.SH','601077.SH','601088.SH','601089.SH',
            '601098.SH','601099.SH','601100.SH','601101.SH','601106.SH',
            '601107.SH','601108.SH','601111.SH','601116.SH','601117.SH',
            '601118.SH','601126.SH','601127.SH','601128.SH','601133.SH',
            '601136.SH','601137.SH','601138.SH','601139.SH','601155.SH',
            '601156.SH','601158.SH','601162.SH','601163.SH','601166.SH',
            '601168.SH','601169.SH','601179.SH','601186.SH','601187.SH',
            '601198.SH','601199.SH','601200.SH','601208.SH','601211.SH',
            '601212.SH','601216.SH','601218.SH','601222.SH','601225.SH',
            '601226.SH','601228.SH','601229.SH','601231.SH','601233.SH',
            '601236.SH','601238.SH','601288.SH','601298.SH',
            '601311.SH','601318.SH','601319.SH','601326.SH','601328.SH',
            '601330.SH','601333.SH','601336.SH','601339.SH','601360.SH',
            '601369.SH','601375.SH','601377.SH','601388.SH','601390.SH',
            '601398.SH','601399.SH','601456.SH','601500.SH','601512.SH',
            '601515.SH','601519.SH','601528.SH','601555.SH','601566.SH',
            '601567.SH','601568.SH','601577.SH','601579.SH','601588.SH',
            '601595.SH','601598.SH','601600.SH','601601.SH','601606.SH',
            '601607.SH','601608.SH','601609.SH','601611.SH','601615.SH',
            '601618.SH','601619.SH','601628.SH','601633.SH','601636.SH',
            '601658.SH','601665.SH','601666.SH','601668.SH','601669.SH',
            '601677.SH','601678.SH','601686.SH','601688.SH','601689.SH',
            '601696.SH','601698.SH','601699.SH','601700.SH','601702.SH',
            '601717.SH','601718.SH','601727.SH','601728.SH','601766.SH',
            '601777.SH','601778.SH','601788.SH','601789.SH','601799.SH',
            '601800.SH','601801.SH','601808.SH','601811.SH','601816.SH',
            '601818.SH','601825.SH','601827.SH','601828.SH','601838.SH',
            '601857.SH','601858.SH','601860.SH','601865.SH','601866.SH',
            '601868.SH','601869.SH','601872.SH','601877.SH','601878.SH',
            '601880.SH','601881.SH','601882.SH','601886.SH','601888.SH',
            '601890.SH','601898.SH','601899.SH','601900.SH','601901.SH',
            '601908.SH','601916.SH','601918.SH','601919.SH','601921.SH',
            '601928.SH','601929.SH','601933.SH','601939.SH','601949.SH',
            '601952.SH','601956.SH','601958.SH','601963.SH','601965.SH',
            '601966.SH','601968.SH','601969.SH','601975.SH','601985.SH',
            '601988.SH','601989.SH','601990.SH','601991.SH','601992.SH',
            '601995.SH','601997.SH','601998.SH',
            '603000.SH','603005.SH','603008.SH','603019.SH','603026.SH',
            '603027.SH','603035.SH','603039.SH','603077.SH','603087.SH',
            '603113.SH','603127.SH','603128.SH','603129.SH','603156.SH',
            '603160.SH','603185.SH','603195.SH','603198.SH','603218.SH',
            '603225.SH','603228.SH','603233.SH','603236.SH','603256.SH',
            '603259.SH','603260.SH','603267.SH','603279.SH','603283.SH',
            '603288.SH','603290.SH','603296.SH','603298.SH','603300.SH',
            '603305.SH','603308.SH','603317.SH','603323.SH','603328.SH',
            '603338.SH','603345.SH','603348.SH','603355.SH','603358.SH',
            '603369.SH','603379.SH','603383.SH','603387.SH','603392.SH',
            '603393.SH','603396.SH','603444.SH','603456.SH','603486.SH',
            '603501.SH','603505.SH','603508.SH','603515.SH','603517.SH',
            '603529.SH','603530.SH','603533.SH','603556.SH','603558.SH',
            '603565.SH','603566.SH','603568.SH','603569.SH','603579.SH',
            '603588.SH','603589.SH','603596.SH','603599.SH','603605.SH',
            '603606.SH','603609.SH','603610.SH','603612.SH','603613.SH',
            '603616.SH','603619.SH','603626.SH','603628.SH','603638.SH',
            '603650.SH','603658.SH','603659.SH','603666.SH','603678.SH',
            '603680.SH','603688.SH','603690.SH','603693.SH','603698.SH',
            '603699.SH','603707.SH','603708.SH','603711.SH','603713.SH',
            '603728.SH','603730.SH','603733.SH','603737.SH','603738.SH',
            '603766.SH','603773.SH','603786.SH','603788.SH','603789.SH',
            '603799.SH','603801.SH','603806.SH','603816.SH','603826.SH',
            '603833.SH','603848.SH','603855.SH','603858.SH','603859.SH',
            '603866.SH','603868.SH','603871.SH','603876.SH','603877.SH',
            '603882.SH','603883.SH','603885.SH','603886.SH','603888.SH',
            '603889.SH','603893.SH','603896.SH','603899.SH','603901.SH',
            '603906.SH','603915.SH','603920.SH','603939.SH','603979.SH',
            '603986.SH','603989.SH','603993.SH','603995.SH','603997.SH',
        ]

    ContextInfo.set_universe(stock_list)
    ContextInfo.set_account(ACCOUNT)
    ContextInfo.st = {
        'positions': {},          # {code: {entry_price, entry_day, entry_idx}}
        'total_trades': 0,
        'total_pnl': 0.0,
        'total_wins': 0,
        'total_losses': 0,
        'total_commission': 0.0,
        'avail_cash': 0.0,
        'base_shares': 0,
        'base_cost': 0.0,
        'entry_price': 0.0,
        '_bt_inited': False,
        '_last_bar_date': '',
        '_bar_idx': 0,
        '_stock_count': len(stock_list),
    }
    ContextInfo.run_time("ontimer", TIMER_INTERVAL, "2019-10-14 13:20:00", "SH")
    _log('[启动] 中证500均线回踩 v1.0 {}票 max{}票/每票~{:,.0f}元/±{:.0f}%/{}d'.format(
        len(stock_list), MAX_POSITIONS, FIXED_AMOUNT, TAKE_PROFIT_PCT*100, MAX_HOLD_DAYS))


# ============================================================================
# 第五部分：handlebar — 日线触发
# ============================================================================

def handlebar(ContextInfo):
    st = ContextInfo.st
    is_live = ContextInfo.is_last_bar()

    # 获取全市场日线
    closes  = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'close')
    opens   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'open')
    highs   = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'high')
    lows    = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'low')
    volumes = ContextInfo.get_history_data(HIST_DATA_LEN, '1d', 'volume')

    if not closes: return

    positions = get_trade_detail_data(ACCOUNT, 'STOCK', 'POSITION')
    accounts  = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT')

    base_shares = 0; base_cost = 0.0
    my_codes = set()
    for pos in positions:
        code = pos.m_strInstrumentID
        if code in closes:  # 只在监控池中
            my_codes.add(code)
            bs = pos.m_nVolume
            if bs > base_shares:
                base_shares = bs; base_cost = pos.m_dOpenPrice

    st['base_shares'] = base_shares; st['base_cost'] = base_cost
    if st['entry_price'] == 0.0: st['entry_price'] = base_cost

    avail_cash = accounts[0].m_dAvailable if accounts else float(BT_INIT_CASH)

    # ---- 回测初始现金注入 ----
    if not is_live and avail_cash > BT_INIT_CASH * 2 and not st.get('_bt_inited'):
        avail_cash = BT_INIT_CASH
        st['_bt_inited'] = True
        _log('[回测] 初始资金 {:,.0f}'.format(BT_INIT_CASH))

    st['avail_cash'] = avail_cash
    st['_bar_idx'] += 1; idx = st['_bar_idx']

    # ---- 日级去重 ----
    first_close = list(closes.values())[0][-1] if closes else 0
    bar_key = str(round(first_close, 1)) + str(idx % 10)
    if not is_live and bar_key == st.get('_last_bar_date', ''): return
    st['_last_bar_date'] = bar_key

    # ==================================================================
    # 1. 检查现有持仓出场
    # ==================================================================
    _check_holds_exit(ContextInfo, opens, highs, lows, closes)

    # ==================================================================
    # 2. 全市场扫描 → 筛选信号
    # ==================================================================
    candidates = []
    active_codes = set(st['positions'].keys())

    for code in closes:
        c = closes[code]
        if len(c) < MA_LONG + 10: continue
        if code in active_codes: continue  # 已有持仓, 不重复

        o = opens.get(code, []); h = highs.get(code, []); l = lows.get(code, [])
        if len(o) < MA_LONG or len(h) < MA_LONG: continue

        bar_close = c[-1]
        if bar_close < MIN_PRICE: continue

        # 量比过滤
        vol = volumes.get(code, [])
        if len(vol) >= MA_MID2:
            ma20v = sum(vol[-MA_MID2:]) / MA_MID2
            if ma20v > 0 and vol[-1] / ma20v < MIN_VOL_RATIO: continue

        # 趋势检查
        if not _is_uptrend(c): continue

        # 回踩检查
        ma10 = _sma(c, MA_MID1)[-1]
        ma30 = _sma(c, MA_LONG)[-1]
        dist10 = (bar_close - ma10) / ma10 if ma10 > 0 else 999
        dist30 = (bar_close - ma30) / ma30 if ma30 > 0 else 999

        on_ma10 = PULLBACK_MA10 and abs(dist10) <= PULLBACK_TOLERANCE
        on_ma30 = PULLBACK_MA30 and abs(dist30) <= PULLBACK_TOLERANCE

        if on_ma10 or on_ma30:
            # 选最接近的那条均线
            best_dist = dist10 if abs(dist10) < abs(dist30) else dist30
            tag = 'MA10' if abs(dist10) <= abs(dist30) else 'MA30'
            candidates.append({
                'code': code, 'close': bar_close, 'dist': abs(best_dist),
                'tag': tag, 'ma10': ma10, 'ma30': ma30,
            })

    # ==================================================================
    # 3. 排序 → 选最优 → 买入
    # ==================================================================
    # 按距离均线最近排序(回踩越精准越优先)
    candidates.sort(key=lambda x: x['dist'])

    # 还能买几个
    can_add = MAX_POSITIONS - len(st['positions'])
    if can_add < 0: can_add = 0

    bought = 0
    for cand in candidates:
        if bought >= can_add: break
        code = cand['code']; px = cand['close']

        # ★ 固定金额: 每票约 50,000 元, 向下取整手(100股)
        target_shares = int(FIXED_AMOUNT / (px * 1.001) / 100) * 100
        if target_shares < 100: target_shares = 100
        lots = target_shares
        need = px * lots * 1.01
        if need > avail_cash: continue

        # 下单
        try: order_shares(code, lots, 'THIS_CLOSE', 0, ContextInfo, ACCOUNT)
        except Exception: continue

        fee = _trade_fee(px, lots)
        st['positions'][code] = {
            'entry_price': px, 'entry_day': idx, 'shares': lots, 'tag': cand['tag']
        }
        st['total_commission'] = st.get('total_commission', 0) + fee
        avail_cash -= need
        bought += 1

        _log('[买入] {} {:.2f} {}股({:,.0f}元) {}(d{:.1f}%)'.format(
            code, px, lots, px*lots, cand['tag'], cand['dist']*100))

    # ==================================================================
    # 4. 每日摘要
    # ==================================================================
    n_holds = len(st['positions'])
    if n_holds > 0 or bought > 0:
        _log('[日末] 持仓{}票 候选{}票 买入{}票 现金{:,.0f} 累计{}笔 net{:+,.0f}'.format(
            n_holds, len(candidates), bought, avail_cash,
            st['total_trades'], st['total_pnl'] - st.get('total_commission', 0)))


# ============================================================================
# 第六部分：持仓出场检查
# ============================================================================

def _check_holds_exit(ContextInfo, opens, highs, lows, closes):
    st = ContextInfo.st
    exited_codes = []

    for code, h in list(st['positions'].items()):
        if code not in closes: continue
        c = closes[code]
        if len(c) < MA_LONG: continue

        entry = h['entry_price']
        days_held = st['_bar_idx'] - h['entry_day']
        shares = h.get('shares', TRADE_LOT)  # 兼容老格式

        o = opens.get(code, [])[-1] if opens.get(code) else c[-1]
        hi = highs.get(code, [])[-1] if highs.get(code) else c[-1]
        lo = lows.get(code, [])[-1] if lows.get(code) else c[-1]
        cl = c[-1]

        sell_price = None; reason = ''

        if lo <= entry * (1.0 - STOP_LOSS_PCT) and o > 0:
            sell_price = min(o, entry * (1.0 - STOP_LOSS_PCT))
            reason = '止损'
        elif hi >= entry * (1.0 + TAKE_PROFIT_PCT) and o > 0:
            sell_price = max(o, entry * (1.0 + TAKE_PROFIT_PCT))
            reason = '止盈'
        elif days_held >= MAX_HOLD_DAYS:
            sell_price = cl
            reason = '到期'

        if sell_price:
            gross = (sell_price - entry) * shares
            fee = _trade_fee(entry, shares) + _trade_fee(sell_price, shares)
            net = gross - fee

            _log('[卖出] {} {:.2f}→{:.2f} {:+,.0f} {}d {}'.format(
                code, entry, sell_price, net, days_held, reason))

            try: order_shares(code, -shares, 'THIS_CLOSE', 0, ContextInfo, ACCOUNT)
            except Exception: pass

            st['total_trades'] += 1
            st['total_pnl'] += gross
            st['total_commission'] = st.get('total_commission', 0) + fee
            if net > 0: st['total_wins'] += 1
            else: st['total_losses'] += 1
            exited_codes.append(code)

    for code in exited_codes:
        del st['positions'][code]


# ============================================================================
# 第七部分：回调 & 工具
# ============================================================================

def order_callback(ContextInfo, order): pass
def deal_callback(ContextInfo, deal): pass

def stop(ContextInfo):
    st = getattr(ContextInfo, 'st', None)
    if st:
        gross = st['total_pnl']; comm = st['total_commission']
        w = st['total_wins']; l = st['total_losses']; t = st['total_trades']
        _log('=' * 70)
        _log('  中证500均线回踩 v1.0  {}笔 gross{:+,.0f} fee{:.0f} net{:+,.0f}'.format(
            t, gross, comm, gross - comm))
        _log('  W{}/L{} win{:.0f}%  max{}票 ±{:.0f}%/{:.0f}% {}d  池{}票'.format(
            w, l, w/t*100 if t>0 else 0, MAX_POSITIONS,
            TAKE_PROFIT_PCT*100, STOP_LOSS_PCT*100, MAX_HOLD_DAYS,
            st.get('_stock_count', 0)))
        _log('=' * 70)


def _cash(ContextInfo):
    try: a = get_trade_detail_data(ACCOUNT, 'STOCK', 'ACCOUNT'); return a[0].m_dAvailable if a else 0.0
    except Exception: return 0.0


def _acc(ContextInfo):
    return ContextInfo.accID if hasattr(ContextInfo, 'accID') and ContextInfo.accID else ACCOUNT


def _now():
    import time as _t; return _t.strftime('%H:%M:%S')


def _ts():
    import time as _t; return _t.strftime('[%H:%M:%S]')


def _log(*args, **kwargs):
    ts = _ts()
    if args: print('{} {}'.format(ts, args[0]), *args[1:], **kwargs)
    else: print(**kwargs)
