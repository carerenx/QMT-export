# -*- coding: gbk -*-
"""
================================================================================
 xtquant 双均线趋势跟踪策略 v1.0 — 入门教程级
================================================================================

 【策略逻辑】
   快线上穿慢线(金叉) → 买入
   快线下穿慢线(死叉) → 卖出
   辅助过滤: 成交量放大确认 + 趋势过滤(价格在年线上方只做多)

 【适用场景】
   - 日线级别中长线趋势跟踪
   - 适合单边趋势市, 震荡市会产生假信号

 【xtquant API 使用示范】
   本策略演示了 xtquant(QMT) 最核心的 API:
   - ContextInfo.get_history_data()    获取历史K线数据
   - ContextInfo.get_full_tick()       获取实时tick数据
   - ContextInfo.barpos                当前K线位置
   - ContextInfo.is_last_bar()         判断是否为最后一根K线
   - passorder() / order_shares()      下单
   - get_trade_detail_data()           查询持仓/账户
   - ContextInfo.paint()               在图表上绘制指标
   - ContextInfo.set_universe()        设置股票池
   - ContextInfo.run_time()            设置定时器

 【运行方式】
   1. 在QMT客户端中新建Python策略, 粘贴本文件代码
   2. 设置策略基本信息:
      - 默认周期: 1d(日线)
      - 默认品种: 601869.SH (或其他标的)
      - 复权方式: 前复权
   3. 回测参数:
      - 初始资金: 1000000
      - 手续费: 万2.5
      - 滑点: 0.01
   4. 点击"回测"按钮验证策略表现
   5. 确认无误后, 切换到"实盘"模式运行

 【参数说明】
   见下方"用户配置区" — 所有可调参数集中在一个区域

 【注意事项】
   - QMT注入的函数(passorder/get_trade_detail_data等)在IDE中会标红, 正常现象, 忽略即可
   - 文件编码必须为 gbk, 否则中文注释在QMT中会乱码
   - 实盘前务必先在回测模式充分验证

================================================================================
"""

# ============================================================================
# 第一部分: 导入模块
# ============================================================================
import time as _time
import numpy as np

# ============================================================================
# 第二部分: ★ 用户配置区 — 修改策略参数在这里 ★
# ============================================================================

# ── 交易标的 ──
STOCK_CODE = '601869'           # 股票代码
STOCK_MARKET = 'SH'             # 市场: SH=上海, SZ=深圳
STOCK_QMT = f'{STOCK_CODE}.{STOCK_MARKET}'
STOCK_NAME = '长飞光纤'

# ── 资金账号 ──
ACCOUNT_ID = '8890145315'       # 您的QMT资金账号

# ── 均线参数 ──
FAST_MA_PERIOD = 5              # 快线周期(日)
SLOW_MA_PERIOD = 20             # 慢线周期(日)
TREND_MA_PERIOD = 60            # 趋势过滤均线(价格在其上方才做多)

# ── 成交量确认 ──
VOLUME_RATIO_THRESHOLD = 1.2    # 金叉日成交量需 > N日均量的1.2倍 (1.0=不限制)

# ── 交易参数 ──
TRADE_LOT_SIZE = 100            # 每手股数(A股=100)
MAX_POSITION_LOTS = 5           # 最大持仓手数(5手=500股)
INIT_CAPITAL_PER_LOT = 50000    # 每手分配资金(用于仓位计算)

# ── 风控参数 ──
STOP_LOSS_PCT = 0.05            # 止损线: 亏损5%止损
TAKE_PROFIT_PCT = 0.15          # 止盈线: 盈利15%止盈
TRAILING_STOP_PCT = 0.08        # 回撤止盈: 从最高点回撤8%止盈

# ── 下单方式 ──
ORDER_STYLE = 'COMPETE'         # 下单方式:
                                #   'LATEST'  — 最新价(市价)
                                #   'COMPETE' — 对手价(更快成交)
                                #   'LIMIT'   — 限价单(需指定价格)
                                #   'MARKET'  — 市价单

# ── 开关 ──
ENABLE_LONG = True              # 启用做多(买入)
ENABLE_SHORT = False            # 启用做空(A股暂不支持, 保留)

# ============================================================================
# 第三部分: 全局状态 (策略运行时存于 ContextInfo.st)
# ============================================================================

STATE_EMPTY = 'EMPTY'           # 空仓
STATE_HOLDING = 'HOLDING'       # 持仓中
STATE_STOPPED = 'STOPPED'       # 已止损/止盈(当日不再交易)


def _init_state():
    """创建初始状态字典"""
    return {
        # ── 持仓信息 ──
        'position_shares': 0,       # 当前持仓股数
        'position_cost': 0.0,       # 持仓成本价
        'available_cash': 0.0,      # 可用资金
        'highest_price': 0.0,       # 持仓期间最高价(用于回撤止盈)

        # ── 信号状态 ──
        'trade_state': STATE_EMPTY,
        'last_signal': '',          # 上一次信号: 'golden_cross' / 'dead_cross'
        'signal_date': '',          # 信号产生日期

        # ── 均线数据 ──
        'fast_ma': 0.0,             # 当前快线值
        'slow_ma': 0.0,             # 当前慢线值
        'trend_ma': 0.0,            # 当前趋势线值
        'prev_fast_ma': 0.0,        # 前一根快线值
        'prev_slow_ma': 0.0,        # 前一根慢线值

        # ── 风控 ──
        'stop_loss_price': 0.0,     # 止损价
        'take_profit_price': 0.0,   # 止盈价

        # ── 统计 ──
        'total_trades': 0,
        'win_trades': 0,
        'total_pnl': 0.0,
        'day_pnl': 0.0,
        'trade_date': '',

        # ── 稳定性 ──
        'initialized': False,
        'init_attempts': 0,
        'last_log_time': 0.0,
        'error_count': 0,
    }


# ============================================================================
# 第四部分: init() — 策略初始化
# ============================================================================

def init(ContextInfo):
    """
    策略初始化函数 — 在策略加载时调用一次。

    xtquant 调用时机: 点击"运行"按钮后, 数据到达前
    """
    try:
        # 1. 设置股票池 — 告诉QMT我们关注哪些标的
        ContextInfo.set_universe([STOCK_QMT])

        # 2. 设置资金账号
        ContextInfo.set_account(ACCOUNT_ID)

        # 3. 初始化策略状态
        ContextInfo.st = _init_state()

        # 4. (可选) 设置定时器 — 用于盘中实时监控
        #    格式: run_time("函数名", "周期", "开始时间", "市场")
        #    周期: "3nSecond"=3秒, "1nMin"=1分钟, "1nDay"=每天
        today = _time.strftime('%Y-%m-%d')
        ContextInfo.run_time("ontimer", "3nSecond", f"{today} 09:30:00", "SH")

        _log("[init] 策略初始化完成")

    except Exception as e:
        _log(f"[init] 初始化异常: {e}")


# ============================================================================
# 第五部分: handlebar() — 核心策略逻辑(K线驱动)
# ============================================================================

def handlebar(ContextInfo):
    """
    核心K线回调 — 每根K线调用一次。

    xtquant 调用时机:
      - 历史回测: 从第一根K线顺序调用到最后一根
      - 实盘盘中: 最后一根K线每个tick调用一次(只有最后一个tick的信号生效)

    参数:
      ContextInfo.barpos      → 当前K线索引(从0开始)
      ContextInfo.is_last_bar() → 是否为最后一根K线
      ContextInfo.period      → 当前周期('1d', '1h', '5m'等)
    """
    try:
        _handlebar_impl(ContextInfo)
    except Exception as e:
        st = getattr(ContextInfo, 'st', {})
        err_count = st.get('error_count', 0) + 1
        if st:
            st['error_count'] = err_count
        if err_count <= 5:
            _log(f"[handlebar异常#{err_count}] {e}")


def _handlebar_impl(ContextInfo):
    """handlebar 实际逻辑"""
    st = ContextInfo.st
    barpos = ContextInfo.barpos

    # ── 1. 跨日重置 ──
    today = _time.strftime('%Y-%m-%d')
    if st.get('trade_date', '') != today:
        _on_new_day(ContextInfo, today)

    # ── 2. 获取历史K线数据 ──
    #    这是 xtquant 最核心的数据获取函数:
    #    ContextInfo.get_history_data(count, period, field)
    #      count  — 获取多少根K线
    #      period — 周期: '1d'日线, '1h'小时, '5m'5分钟, '1m'1分钟
    #      field  — 字段: 'close'收盘, 'open'开盘, 'high'最高, 'low'最低, 'volume'成交量
    #    返回: {股票代码: [值列表], ...}  — list[0]最旧, list[-1]最新
    #
    #    获取足够长的历史数据(至少 TREND_MA_PERIOD+10 根)
    need_bars = max(FAST_MA_PERIOD, SLOW_MA_PERIOD, TREND_MA_PERIOD) + 10
    try:
        hist_close = ContextInfo.get_history_data(need_bars, '1d', 'close')
        hist_volume = ContextInfo.get_history_data(need_bars, '1d', 'volume')
    except Exception as e:
        _log(f"[数据获取失败] {e}")
        return

    # 检查数据是否有效
    if STOCK_QMT not in hist_close or len(hist_close[STOCK_QMT]) < SLOW_MA_PERIOD:
        if barpos <= 5:  # 只在开头几根bar打印, 避免刷屏
            _log(f"[数据不足] 需要{SLOW_MA_PERIOD}根K线, 当前{len(hist_close.get(STOCK_QMT, []))}")
        return

    closes = np.array(hist_close[STOCK_QMT], dtype=float)
    volumes = np.array(hist_volume.get(STOCK_QMT, []), dtype=float)

    # ── 3. 计算均线 ──
    fast_ma = _calc_sma(closes, FAST_MA_PERIOD)
    slow_ma = _calc_sma(closes, SLOW_MA_PERIOD)
    trend_ma = _calc_sma(closes, TREND_MA_PERIOD)

    # 当前价格 = 最新收盘价
    current_price = closes[-1]

    # 前一根均线值(用于判断交叉)
    prev_close = closes[:-1] if len(closes) > 1 else closes
    prev_fast = _calc_sma(prev_close, FAST_MA_PERIOD)
    prev_slow = _calc_sma(prev_close, SLOW_MA_PERIOD)

    # ── 4. 保存到状态 ──
    st['prev_fast_ma'] = prev_fast
    st['prev_slow_ma'] = prev_slow
    st['fast_ma'] = fast_ma
    st['slow_ma'] = slow_ma
    st['trend_ma'] = trend_ma

    # ── 5. 均线交叉检测 ──
    golden_cross = (prev_fast <= prev_slow) and (fast_ma > slow_ma)   # 金叉
    dead_cross = (prev_fast >= prev_slow) and (fast_ma < slow_ma)     # 死叉

    # ── 6. 辅助过滤条件 ──
    # 成交量确认: 当日成交量 > N日均量的1.2倍
    avg_vol = np.mean(volumes[-21:-1]) if len(volumes) >= 22 else np.mean(volumes[:-1])
    cur_vol = volumes[-1] if len(volumes) > 0 else 0
    volume_confirmed = cur_vol > avg_vol * VOLUME_RATIO_THRESHOLD if avg_vol > 0 else True

    # 趋势过滤: 价格在趋势线上方(牛市)才做多
    trend_bull = current_price > trend_ma if trend_ma > 0 else True

    # ── 7. 获取实时价格(实盘用) ──
    live_price = current_price
    if ContextInfo.is_last_bar():
        try:
            tick = ContextInfo.get_full_tick([STOCK_QMT])
            if STOCK_QMT in tick:
                lp = tick[STOCK_QMT].get('lastPrice', 0)
                if lp > 0:
                    live_price = lp
        except Exception:
            pass  # 回测时 get_full_tick 不可用, 用收盘价

    # ── 8. 获取当前持仓 ──
    _update_position(ContextInfo)

    # ── 9. 风控检查(持仓时) ──
    if st['trade_state'] == STATE_HOLDING and st['position_shares'] > 0:
        _check_risk_control(ContextInfo, live_price)

    # ── 10. 交易信号处理 ──
    # 只在最后一根K线执行交易(实盘) 或 回测时每根K线都判断
    is_last = ContextInfo.is_last_bar()

    # 金叉买入
    if golden_cross and ENABLE_LONG:
        st['last_signal'] = 'golden_cross'
        st['signal_date'] = today
        if volume_confirmed and trend_bull:
            _log(f"[金叉信号] 快线{fast_ma:.2f}↑慢线{slow_ma:.2f} | "
                 f"趋势{'多' if trend_bull else '空'} | 量比{cur_vol/avg_vol:.2f}")
            if is_last:
                _execute_buy(ContextInfo, live_price)
            else:
                # 回测模式: 在下根K线开盘买入
                pass  # QMT回测框架自动在下根K线第一个tick发单
        else:
            reason = []
            if not volume_confirmed:
                reason.append(f"量能不足({cur_vol/avg_vol:.2f}<{VOLUME_RATIO_THRESHOLD})")
            if not trend_bull:
                reason.append(f"趋势偏空(价格{current_price:.2f}<年线{trend_ma:.2f})")
            _log(f"[金叉过滤] {', '.join(reason)} — 不交易")

    # 死叉卖出
    if dead_cross and ENABLE_LONG:
        st['last_signal'] = 'dead_cross'
        st['signal_date'] = today
        if st['trade_state'] == STATE_HOLDING and st['position_shares'] > 0:
            _log(f"[死叉信号] 快线{fast_ma:.2f}↓慢线{slow_ma:.2f} | 当前价¥{live_price:.2f}")
            if is_last:
                _execute_sell(ContextInfo, live_price)

    # ── 11. 绘制指标(K线图叠加) ──
    #     ContextInfo.paint(name, value, line_type, color)
    #     name: 指标名称(显示在图例中)
    #     value: 数值(与K线对齐)
    #     line_type: -1=不做图, >=0=曲线图编号
    #     color: 0xRRGGBB
    if barpos > SLOW_MA_PERIOD:
        ContextInfo.paint(f'MA{FAST_MA_PERIOD}', fast_ma, 0, 0x00AAFF)   # 蓝色快线
        ContextInfo.paint(f'MA{SLOW_MA_PERIOD}', slow_ma, 1, 0xFF6600)   # 橙色慢线
        ContextInfo.paint(f'MA{TREND_MA_PERIOD}', trend_ma, 2, 0x888888) # 灰色趋势线


# ============================================================================
# 第六部分: 交易执行
# ============================================================================

def _execute_buy(ContextInfo, price):
    """执行买入"""
    st = ContextInfo.st

    # 已经持仓, 不重复买入
    if st['trade_state'] == STATE_HOLDING:
        _log(f"[买入跳过] 已有持仓{st['position_shares']}股")
        return

    # 已被风控停止
    if st['trade_state'] == STATE_STOPPED:
        _log(f"[买入跳过] 今日已被风控停止")
        return

    # 计算买入数量(不超过最大持仓)
    current_pos = st['position_shares']
    max_buy = MAX_POSITION_LOTS * TRADE_LOT_SIZE - current_pos
    if max_buy <= 0:
        _log(f"[买入跳过] 已达最大持仓{MAX_POSITION_LOTS}手")
        return

    # 资金检查
    need_cash = price * TRADE_LOT_SIZE * 1.001  # 预留手续费
    available = st['available_cash']
    lots_by_cash = int(available // need_cash) if need_cash > 0 else 0
    buy_lots = min(lots_by_cash, max_buy // TRADE_LOT_SIZE)
    if buy_lots <= 0:
        _log(f"[买入跳过] 资金不足: 需¥{need_cash:,.0f}/手, 可用¥{available:,.0f}")
        return

    buy_shares = buy_lots * TRADE_LOT_SIZE

    # ── 下单 (xtquant 核心下单函数) ──
    # 方式1: passorder() — 传统QMT下单
    #   passorder(opType, orderType, accountid, orderCode, prType, modelprice,
    #             volume, strategyName, quickTrade, userOrderId, ContextInfo)
    #
    # 方式2: order_shares() — 简化下单(推荐)
    #   order_shares(stockcode, shares, style, ContextInfo, accId)
    #     stockcode: 股票代码 '601869.SH'
    #     shares: 正数=买入, 负数=卖出
    #     style: 'LATEST'/'COMPETE'/'LIMIT'/'MARKET'
    try:
        # 使用 order_shares (推荐方式)
        order_shares(STOCK_QMT, buy_shares, ORDER_STYLE, ContextInfo, ACCOUNT_ID)
        _log(f"[>>> 买入] {ORDER_STYLE} × {buy_shares}股 @ ¥{price:.2f}")

        # 更新状态
        st['trade_state'] = STATE_HOLDING
        st['position_cost'] = price
        st['highest_price'] = price
        st['stop_loss_price'] = price * (1 - STOP_LOSS_PCT)
        st['take_profit_price'] = price * (1 + TAKE_PROFIT_PCT)
        st['total_trades'] += 1

    except Exception as e:
        _log(f"[买入失败] {e}")


def _execute_sell(ContextInfo, price):
    """执行卖出"""
    st = ContextInfo.st

    if st['position_shares'] <= 0:
        _log(f"[卖出跳过] 无持仓")
        return

    sell_shares = st['position_shares']  # 全仓卖出

    try:
        # 负数表示卖出
        order_shares(STOCK_QMT, -sell_shares, ORDER_STYLE, ContextInfo, ACCOUNT_ID)
        _log(f"[>>> 卖出] {ORDER_STYLE} × {sell_shares}股 @ ¥{price:.2f}")

        # 计算盈亏
        if st['position_cost'] > 0:
            pnl = (price - st['position_cost']) * sell_shares
            pnl_pct = (price / st['position_cost'] - 1) * 100
            st['total_pnl'] += pnl
            if pnl > 0:
                st['win_trades'] += 1
            _log(f"[卖出盈亏] ¥{pnl:,.0f} ({pnl_pct:+.2f}%) | 累计PnL=¥{st['total_pnl']:,.0f}")

        # 重置状态
        st['trade_state'] = STATE_EMPTY
        st['position_shares'] = 0
        st['position_cost'] = 0.0
        st['highest_price'] = 0.0
        st['stop_loss_price'] = 0.0
        st['take_profit_price'] = 0.0

    except Exception as e:
        _log(f"[卖出失败] {e}")


# ============================================================================
# 第七部分: 风控模块
# ============================================================================

def _check_risk_control(ContextInfo, price):
    """持仓风控检查 — 止损/止盈/回撤止盈"""
    st = ContextInfo.st

    if st['position_cost'] <= 0:
        return

    # 更新持仓期间最高价
    if price > st['highest_price']:
        st['highest_price'] = price

    cost = st['position_cost']
    high = st['highest_price']

    # ── 1. 硬止损 ──
    if price <= cost * (1 - STOP_LOSS_PCT):
        loss_pct = (price / cost - 1) * 100
        _log(f"[!!止损!!] 亏损{loss_pct:+.2f}% | 成本¥{cost:.2f} → 现价¥{price:.2f}")
        _execute_sell(ContextInfo, price)
        st['trade_state'] = STATE_STOPPED
        return

    # ── 2. 硬止盈 ──
    if price >= cost * (1 + TAKE_PROFIT_PCT):
        gain_pct = (price / cost - 1) * 100
        _log(f"[!!止盈!!] 盈利{gain_pct:+.2f}% | 成本¥{cost:.2f} → 现价¥{price:.2f}")
        _execute_sell(ContextInfo, price)
        return

    # ── 3. 回撤止盈(移动止损) ──
    #     从最高点回落超过 TRAILING_STOP_PCT 就止盈
    drawdown = (high - price) / high if high > 0 else 0
    if high > cost * (1 + TAKE_PROFIT_PCT * 0.5) and drawdown >= TRAILING_STOP_PCT:
        _log(f"[!!回撤止盈!!] 最高¥{high:.2f} 回撤{drawdown*100:.2f}% → ¥{price:.2f}")
        _execute_sell(ContextInfo, price)
        return


# ============================================================================
# 第八部分: ontimer() — 盘中实时监控
# ============================================================================

def ontimer(ContextInfo):
    """
    定时器回调 — 每隔固定时间调用一次.

    用于实时监控: 价格异动、止盈止损检查、补单等.
    handlebar 是K线驱动, ontimer 是时间驱动.
    """
    try:
        st = getattr(ContextInfo, 'st', None)
        if st is None:
            return

        now = _time.strftime('%H:%M:%S')

        # 只在交易时段运行
        if not ('09:30:00' <= now <= '11:30:00' or '13:00:00' <= now <= '15:00:00'):
            return

        # 获取实时行情
        try:
            tick = ContextInfo.get_full_tick([STOCK_QMT])
            if STOCK_QMT not in tick:
                return
            price = tick[STOCK_QMT].get('lastPrice', 0)
            if price <= 0:
                return
        except Exception:
            return

        # 更新持仓信息
        _update_position(ContextInfo)

        # 持仓风控检查
        if st['trade_state'] == STATE_HOLDING and st['position_shares'] > 0:
            _check_risk_control(ContextInfo, price)

        # 心跳日志(每60秒)
        now_ts = _time.time()
        if now_ts - st.get('last_log_time', 0) >= 60:
            st['last_log_time'] = now_ts
            pos_info = f"持仓{st['position_shares']}股" if st['position_shares'] > 0 else "空仓"
            _log(f"[心跳] {now} | ¥{price:.2f} | {pos_info} | "
                 f"MA{FAST_MA_PERIOD}={st['fast_ma']:.2f} MA{SLOW_MA_PERIOD}={st['slow_ma']:.2f}")

    except Exception as e:
        _log(f"[ontimer异常] {e}")


# ============================================================================
# 第九部分: 回调函数 (QMT主推)
# ============================================================================

def order_callback(ContextInfo, order):
    """
    委托回调 — QMT在有委托状态变化时自动调用.

    order 对象常用属性:
      m_nOrderStatus: 50=已报, 52=部成, 53=全成, 54=部撤, 55=已撤, 56=废单
      m_dLimitPrice:  委托价格
      m_nVolumeTotalOriginal: 委托数量
      m_nVolumeTraded: 已成交数量
      m_strInstrumentID: 股票代码
    """
    try:
        status_map = {
            50: '已报', 52: '部成', 53: '全成',
            54: '部撤', 55: '已撤', 56: '废单'
        }
        status = order.m_nOrderStatus
        status_text = status_map.get(status, f'未知({status})')

        _log(f"[委托回调] "
             f"{order.m_strInstrumentID} | "
             f"¥{order.m_dLimitPrice:.2f} | "
             f"{order.m_nVolumeTraded}/{order.m_nVolumeTotalOriginal}股 | "
             f"→ {status_text}")

        # 废单/已撤告警
        if status in (55, 56):
            _log(f"  ⚠ 委托异常, 请检查: 价格是否偏离市价? 数量是否超过持仓?")

    except Exception as e:
        _log(f"[委托回调异常] {e}")


def deal_callback(ContextInfo, deal):
    """
    成交回调 — QMT在有成交回报时自动调用.

    deal 对象常用属性:
      m_nDirection:  1=买入, 2=卖出
      m_dPrice:      成交价
      m_nVolume:     成交数量
      m_strInstrumentID: 股票代码
    """
    try:
        st = ContextInfo.st
        direction = '买' if deal.m_nDirection == 1 else '卖'
        price = deal.m_dPrice
        volume = deal.m_nVolume
        amount = price * volume

        _log(f"[成交回调] {direction} {deal.m_strInstrumentID} "
             f"¥{price:.2f} × {volume}股 = ¥{amount:,.0f}")

        # 更新持仓数量
        if deal.m_nDirection == 1:
            st['position_shares'] = st.get('position_shares', 0) + volume
        else:
            st['position_shares'] = max(0, st.get('position_shares', 0) - volume)

    except Exception as e:
        _log(f"[成交回调异常] {e}")


def stop(ContextInfo):
    """
    策略停止回调 — 策略停止时调用.

    用于: 输出最终统计、检查未平仓风险、清理资源.
    """
    st = getattr(ContextInfo, 'st', None)
    if st is None:
        return

    _log(f"\n{'='*55}")
    _log(f"  {STOCK_NAME} 双均线趋势跟踪 v1.0 — 已停止")
    _log(f"  总交易: {st.get('total_trades', 0)}次")
    _log(f"  胜率: {st.get('win_trades', 0)}/{max(1, st.get('total_trades', 1))}")
    _log(f"  累计PnL: ¥{st.get('total_pnl', 0):,.0f}")
    _log(f"  最终持仓: {st.get('position_shares', 0)}股")
    _log(f"  错误次数: {st.get('error_count', 0)}")
    if st.get('position_shares', 0) > 0:
        _log(f"  ⚠ 仍有持仓! 请手动处理.")
    _log(f"{'='*55}")


# ============================================================================
# 第十部分: 辅助函数
# ============================================================================

def _calc_sma(data, period):
    """计算简单移动平均(SMA)"""
    if len(data) < period or period <= 0:
        return 0.0
    return float(np.mean(data[-period:]))


def _update_position(ContextInfo):
    """更新当前持仓和资金信息"""
    st = ContextInfo.st
    try:
        # 查询持仓
        positions = get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'POSITION')
        for pos in positions:
            if pos.m_strInstrumentID == STOCK_CODE:
                st['position_shares'] = pos.m_nVolume
                st['position_cost'] = pos.m_dOpenPrice

        # 查询资金
        accounts = get_trade_detail_data(ACCOUNT_ID, 'STOCK', 'ACCOUNT')
        if accounts:
            st['available_cash'] = accounts[0].m_dAvailable

    except Exception:
        pass  # 回测环境下可能不可用


def _on_new_day(ContextInfo, today):
    """新交易日重置"""
    st = ContextInfo.st
    old_date = st.get('trade_date', '')
    if old_date:
        _log(f"\n[新交易日] {old_date} → {today}")

    # 保留跨日统计
    total_trades = st.get('total_trades', 0)
    win_trades = st.get('win_trades', 0)
    total_pnl = st.get('total_pnl', 0.0)

    # 重置日内状态
    new_st = _init_state()
    new_st['total_trades'] = total_trades
    new_st['win_trades'] = win_trades
    new_st['total_pnl'] = total_pnl
    new_st['trade_date'] = today
    ContextInfo.st = new_st


def _now():
    """返回当前时间字符串 HH:MM:SS"""
    return _time.strftime('%H:%M:%S')


def _log(msg):
    """统一日志输出"""
    print(f"[{_now()}] {msg}")


# ============================================================================
# 附: 快速上手 — 如何使用本策略
# ============================================================================
#
# 【第一步: 在QMT中创建策略】
#   1. 打开迅投QMT客户端
#   2. 左侧菜单 → "模型研究" → 右键 "新建模型" → 选择 "Python模型"
#   3. 将本文件全部代码复制粘贴到策略编辑器中
#   4. 点击"保存", 命名为 "双均线趋势跟踪_v1"
#
# 【第二步: 配置策略参数】
#   1. 在策略编辑器右侧设置:
#      - 默认周期: 1d (日线)
#      - 默认品种: 601869.SH
#      - 复权方式: 前复权
#   2. 修改"用户配置区"的参数来适配你的标的:
#      - STOCK_CODE: 改成你关注的股票代码
#      - ACCOUNT_ID: 改成你的QMT资金账号
#      - FAST_MA_PERIOD/SLOW_MA_PERIOD: 调整均线周期
#
# 【第三步: 回测验证】
#   1. 设置回测参数:
#      - 开始/结束时间: 如 2024-01-01 ~ 2025-12-31
#      - 初始资金: 1000000
#      - 手续费: 万2.5
#   2. 点击工具栏"回测"按钮
#   3. 查看回测报告: 收益率曲线、夏普比率、最大回撤、胜率等
#   4. 根据回测结果调整参数, 重复回测直到满意
#
# 【第四步: 实盘运行】
#   1. 确认回测结果OK
#   2. 在策略编辑器中切换到"实盘"模式
#   3. 点击"运行"按钮
#   4. 在"交易"面板查看委托和成交情况
#   5. 建议先用小仓位(1手)跑几天验证稳定后再加仓
#
# 【第五步: 日常维护】
#   1. 每天收盘后检查"stop"输出的PnL和胜率
#   2. 关注废单(56)和已撤(55)的委托回调, 及时处理异常
#   3. 每周review一次策略表现, 必要时调整参数
#   4. 如果策略长期不交易, 检查:
#      - 均线周期是否太长短导致没有交叉?
#      - VOLUME_RATIO_THRESHOLD 是否设得过高?
#      - 是否有足够的可用资金?
#
# 【常见问题】
#   Q: IDE里 passorder/order_shares 标红?
#   A: 这些是QMT运行时注入的函数, 本地IDE没有定义, 红色波浪线是正常的, 忽略即可.
#
#   Q: 回测有信号但实盘不成交?
#   A: 检查是否在最后一根K线(ContextInfo.is_last_bar()), 历史K线上的信号不会触发实盘下单.
#      盘中需要等K线走完最后一个tick才会发单. 如需即时发单, order_shares后调用 do_order().
#
#   Q: handlebar 执行频率过高?
#   A: 盘中每个tick都会调用handlebar. 用 ContextInfo.is_last_bar() 控制只在最后一根K线执行
#      交易逻辑, 其他tick只做行情记录和绘图.
#
#   Q: 如何切换标的?
#   A: 修改 STOCK_CODE 和 STOCK_MARKET. 上证填'SH', 深证填'SZ'.
#      多个标的: ContextInfo.set_universe(['000001.SZ', '600519.SH', ...])
#
# ============================================================================
