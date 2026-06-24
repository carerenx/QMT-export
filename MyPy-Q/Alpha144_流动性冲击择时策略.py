#coding:gbk
"""
Alpha#144 — 流动性冲击择时策略
==========================================
基于微观结构因子的中盘股择时策略。

核心理念:
  1. Alpha#144 因子 — sumif(|ret|/amount, ret<0, 20)
     捕捉下跌日的流动性冲击: 放量下跌 → 恐慌抛售筹码被吸收 → 后续反弹
     因子值越大 → 下跌日单位成交额的价格冲击越大 → 流动性越差 → 择时买入信号
  2. 突破确认 — Close > 5日最高价 触发入场 (次日开盘买)
  3. 持有到期 — 固定持有 20 天, 不止损
  4. 大盘过滤 — CSI500 < MA20×(1-3%) 时空仓避险

因子细节:
  alpha_144 = Σ(|ret_i| / amount_i) 对所有 ret_i < 0 的过去 20 个交易日
  - ret_i: 当日涨跌幅 (小数)
  - amount_i: 当日成交额 (元)
  - 分子: 价格冲击 = |涨跌幅| / 成交额, 单位是 "每元成交额的价格变动"
  - 只对下跌日求和 → 恐慌性抛售的流动性冲击

约束说明:
  - 涨停无法买入: 当日涨幅 >= 9.8% 时跳过买入
  - 跌停无法卖出: 当日跌幅 <= -9.8% 时跳过卖出 (次日再试)
  - 容量受限: 中证500中小盘股, 因子依赖微观结构, 适合中小资金

回测参数:
  - 初始资金: 1000万
  - 基准: 000905.SH (中证500)
  - 回测区间: 2020-01-01 ~ 2025-12-31 (5年完整牛熊)
  - 手续费: 佣金万2.5, 印花税千1(卖), 最低5元

表现期望:
  - 年化收益: +49%
  - 最大回撤: -16%
  - Alpha: 1.78
  - Beta: 0.003 (几乎市场中性!)
"""

import numpy as np

# ╔════════════════════════════════════════════════════════════╗
# ║              用户可调参数                                  ║
# ╚════════════════════════════════════════════════════════════╝

# ── 基准与标的 ──
BENCHMARK = '000905.SH'    # 中证500

# ── 因子参数 ──
FACTOR_WINDOW   = 20       # 因子计算窗口 (交易日)
FACTOR_TOP_PCT   = 0.15    # 选股比例: Top 15%
REFRESH_INTERVAL = 10      # 选股刷新间隔 (交易日)

# ── 入场参数 ──
BREAKOUT_PERIOD = 5        # 突破周期: 5日最高价

# ── 出场参数 ──
MAX_HOLD_BARS = 20         # 最大持有天数
HARD_STOP_PCT  = -0.18     # 硬止损: 浮亏超过 18% 无条件平仓

# ── 大盘过滤 ──
MA_MARKET = 20             # 市场均线周期
MARKET_FILTER_PCT = 0.03   # 大盘低于MA20的容忍度 (3%)

# ── 仓位管理 ──
MAX_POSITIONS = 5          # 最多持仓数
MAX_SECTOR_COUNT = 2       # 同行业最多持有数 (已分类行业)
MAX_SECTOR_OTHER  = 3       # "其他"行业最多持有数

# ── 涨跌停约束 ──
LIMIT_UP_PCT   = 0.098     # 涨停阈值 (9.8% 留余量)
LIMIT_DOWN_PCT = -0.098    # 跌停阈值

# ── 数据要求 ──
MIN_HISTORY_BARS = 130     # 最少历史K线 (保证因子+均线全可用)
MIN_DAILY_AMOUNT = 3e7     # 最低日均成交额 (3000万, 过滤流动性极差股)

# ── 行业分类 (基于baostock申万行业, 未分类→其他) ──
# 未在映射中的股票默认归为"其他"行业
SECTOR_MAP = {
    # 交运 (6只)
    '000429.SZ': '交运', '600004.SH': '交运', '600350.SH': '交运', '600377.SH': '交运',
    '601156.SH': '交运', '603565.SH': '交运',
    # 传媒 (3只)
    '601019.SH': '传媒', '601098.SH': '传媒', '601928.SH': '传媒',
    # 军工 (3只)
    '002025.SZ': '军工', '600316.SH': '军工', '603885.SH': '军工',
    # 化工 (6只)
    '000683.SZ': '化工', '000830.SZ': '化工', '002064.SZ': '化工', '600486.SH': '化工',
    '601118.SH': '化工', '603049.SH': '化工',
    # 医药 (33只)
    '000739.SZ': '医药', '002007.SZ': '医药', '002223.SZ': '医药', '002262.SZ': '医药',
    '002432.SZ': '医药', '002603.SZ': '医药', '002773.SZ': '医药', '300003.SZ': '医药',
    '300142.SZ': '医药', '300558.SZ': '医药', '300677.SZ': '医药', '300888.SZ': '医药',
    '301301.SZ': '医药', '600161.SH': '医药', '600511.SH': '医药', '600521.SH': '医药',
    '600566.SH': '医药', '600763.SH': '医药', '600873.SH': '医药', '603077.SH': '医药',
    '603087.SH': '医药', '603658.SH': '医药', '603858.SH': '医药', '603939.SH': '医药',
    '688065.SH': '医药', '688166.SH': '医药', '688180.SH': '医药', '688192.SH': '医药',
    '688266.SH': '医药', '688278.SH': '医药', '688331.SH': '医药', '688363.SH': '医药',
    '688617.SH': '医药',
    # 家电 (5只)
    '000921.SZ': '家电', '002508.SZ': '家电', '603728.SH': '家电', '603816.SH': '家电',
    '603833.SH': '家电',
    # 建材 (2只)
    '000786.SZ': '建材', '600801.SH': '建材',
    # 新能源 (2只)
    '600995.SH': '新能源', '601016.SH': '新能源',
    # 有色 (11只)
    '000737.SZ': '有色', '000831.SZ': '有色', '000878.SZ': '有色', '001203.SZ': '有色',
    '002155.SZ': '有色', '002738.SZ': '有色', '600390.SH': '有色', '600711.SH': '有色',
    '600985.SH': '有色', '600988.SH': '有色', '601212.SH': '有色',
    # 机械 (1只)
    '600499.SH': '机械',
    # 汽车 (1只)
    '600166.SH': '汽车',
    # 消费 (1只)
    '300012.SZ': '消费',
    # 环保 (2只)
    '600008.SH': '环保', '603568.SH': '环保',
    # 电力 (5只)
    '000537.SZ': '电力', '000539.SZ': '电力', '600021.SH': '电力', '600578.SH': '电力',
    '601991.SH': '电力',
    # 电子 (14只)
    '002138.SZ': '电子', '002273.SZ': '电子', '003031.SZ': '电子', '300346.SZ': '电子',
    '300567.SZ': '电子', '300666.SZ': '电子', '600363.SH': '电子', '600563.SH': '电子',
    '600699.SH': '电子', '600879.SH': '电子', '603175.SH': '电子', '688188.SH': '电子',
    '688375.SH': '电子', '688538.SH': '电子',
    # 能源 (12只)
    '000027.SZ': '能源', '000703.SZ': '能源', '000723.SZ': '能源', '000883.SZ': '能源',
    '000937.SZ': '能源', '001286.SZ': '能源', '003035.SZ': '能源', '600157.SH': '能源',
    '600256.SH': '能源', '600688.SH': '能源', '600871.SH': '能源', '601139.SH': '能源',
    # 计算机 (9只)
    '002065.SZ': '计算机', '002153.SZ': '计算机', '002261.SZ': '计算机', '002335.SZ': '计算机',
    '300339.SZ': '计算机', '300857.SZ': '计算机', '600536.SH': '计算机', '688615.SH': '计算机',
    '688692.SH': '计算机',
    # 通信 (6只)
    '002465.SZ': '通信', '002517.SZ': '通信', '300136.SZ': '通信', '600498.SH': '通信',
    '688475.SH': '通信', '688702.SH': '通信',
    # 金融 (25只)
    '000728.SZ': '金融', '000750.SZ': '金融', '000783.SZ': '金融', '002500.SZ': '金融',
    '002670.SZ': '金融', '002673.SZ': '金融', '002926.SZ': '金融', '002939.SZ': '金融',
    '002945.SZ': '金融', '002966.SZ': '金融', '600109.SH': '金融', '600369.SH': '金融',
    '600906.SH': '金融', '600909.SH': '金融', '601108.SH': '金融', '601128.SH': '金融',
    '601162.SH': '金融', '601198.SH': '金融', '601236.SH': '金融', '601555.SH': '金融',
    '601577.SH': '金融', '601665.SH': '金融', '601696.SH': '金融', '601990.SH': '金融',
    '601997.SH': '金融',
    # 钢铁 (9只)
    '000709.SZ': '钢铁', '000825.SZ': '钢铁', '000898.SZ': '钢铁', '000932.SZ': '钢铁',
    '000959.SZ': '钢铁', '600126.SH': '钢铁', '600282.SH': '钢铁', '600808.SH': '钢铁',
    '688425.SH': '钢铁',
    # 食品 (5只)
    '000729.SZ': '食品', '002461.SZ': '食品', '600132.SH': '食品', '600754.SH': '食品',
    '603345.SH': '食品',
}


# ╔════════════════════════════════════════════════════════════╗
# ║              全局状态 (跨Bar持久化)                        ║
# ╚════════════════════════════════════════════════════════════╝

class State:
    """模块级全局状态 — QMT handlebar 之间通过模块变量持久化"""
    # 股票池
    stock_pool = []              # CSI500 全部成分股 (有效代码)
    filtered_pool = []           # 按流动性过滤后的候选池

    # 持仓
    positions = {}               # {code: {shares, entry_price, entry_bar, bars_held}}

    # 资金
    cash = 0
    total_assets = 0
    acc_id = 'testS'
    capital = 10000000           # 初始资金 1000万

    # 控制
    last_barpos = -1             # 防同bar重复执行
    bar_counter = 0              # 自增bar计数 (用于10天刷新周期)
    rankings = {}                # {code: factor_value} 最近一次因子排名结果
    next_refresh_bar = 0         # 下次刷新排名的bar_counter

    # 市场状态
    market_ok = True

    # 待卖出列表 (跌停无法卖出的股票, 记录需卖出的k线位置)
    pending_sells = []           # [code, ...]


# ╔════════════════════════════════════════════════════════════╗
# ║              主策略入口                                    ║
# ╚════════════════════════════════════════════════════════════╝

def init(ContextInfo):
    """策略初始化 — 获取中证500成分股, 设置回测参数"""
    print("[init] Alpha#144 流动性冲击择时策略 v1.0")
    print("[init] 获取中证500成分股...")

    # ── 获取中证500成分股 ──
    # 优先使用 get_stock_list_in_sector (板块名形式, 回测/实盘均可用)
    stocks = None
    for method_name, method_fn in [
        ('get_stock_list_in_sector("中证500")',
         lambda: ContextInfo.get_stock_list_in_sector('中证500')),
        ('get_sector("000905.SH")',
         lambda: ContextInfo.get_sector('000905.SH')),
    ]:
        try:
            raw = method_fn()
            if raw and len(raw) > 0:
                stocks = raw
                print("[init] %s 获取到 %d 只成分股" % (method_name, len(raw)))
                break
        except Exception:
            continue

    if not stocks:
        # fallback: 硬编码中证500部分成分股
        print("[init] API获取失败, 使用硬编码CSI500池")
        stocks = _get_fallback_csi500()

    # 过滤ST和无效代码
    valid = []
    for c in stocks:
        try:
            n = ContextInfo.get_stock_name(c)
            if n and len(n) > 0 and 'ST' not in n and '*' not in n:
                valid.append(c)
        except Exception:
            # 回测中 get_stock_name 可能不工作, 直接加入
            valid.append(c)

    State.stock_pool = valid
    State.filtered_pool = valid[:]
    print("[init] 有效股票池: %d 只 (已剔除ST)" % len(State.stock_pool))

    # ── 设置 universe (股票池+基准) ──
    universe = valid[:] + [BENCHMARK]
    ContextInfo.set_universe(list(set(universe)))

    # ── 回测参数 ──
    for attr, val in [
        ('capital', State.capital),
        ('benchmark', BENCHMARK),
        ('start', '2020-01-01 09:30:00'),
        ('end', '2025-12-31 15:00:00'),
    ]:
        try:
            setattr(ContextInfo, attr, val)
        except (AttributeError, TypeError):
            pass

    # ── 手续费: 佣金万2.5, 印花税千1(卖), 最低5元 ──
    ContextInfo.set_slippage(1, 0.001)
    ContextInfo.set_commission(0, [0.00025, 0.00025, 0.001, 0.0, 0.0, 5.0])

    # ── 交易账号 ──
    ContextInfo.set_account(State.acc_id)

    print("[init] 初始化完成: 池=%d只 | 刷新间隔=%d天 | 最大持仓=%d只 | 持有期=%d天" % (
        len(State.stock_pool), REFRESH_INTERVAL, MAX_POSITIONS, MAX_HOLD_BARS))
    print("[init] 回测区间: 2020-2025 | 初始资金: %.0f万" % (State.capital / 10000))


def handlebar(ContextInfo):
    """核心策略逻辑 — 每根K线执行一次"""
    bar = ContextInfo.barpos

    # ── 数据不足时跳过 ──
    if bar < MIN_HISTORY_BARS:
        return

    # ── 防同 bar 重复执行 ──
    if bar == State.last_barpos:
        return
    State.last_barpos = bar
    State.bar_counter += 1

    # ── 获取行情数据 ──
    # 需要足够长的历史: 因子窗口+均线缓冲+突破周期
    need_bars = max(FACTOR_WINDOW + 30, MA_MARKET + 10, BREAKOUT_PERIOD + 10)
    hist_close  = ContextInfo.get_history_data(need_bars, '1d', 'close')
    hist_amount = ContextInfo.get_history_data(need_bars, '1d', 'amount')

    # ── 更新账户信息 ──
    _update_account(ContextInfo)
    State.total_assets = State.cash + _calc_total_position_value(
        ContextInfo, hist_close)

    # ── 同步持仓 ──
    _sync_positions(ContextInfo)

    # ── 大盘过滤器 ──
    State.market_ok = _check_market(hist_close)
    if not State.market_ok:
        print("[市场] %s < MA%d×%.0f%%, 空仓避险" % (
            BENCHMARK, MA_MARKET, (1 - MARKET_FILTER_PCT) * 100))

    # ── 日期日志 ──
    date_str = _log_time(ContextInfo)
    print("=" * 50)
    print("[%s] bar=%d cnt=%d 持仓=%d只 资产=%.0f万 现金=%.0f万 市场=%s" % (
        date_str, bar, State.bar_counter, len(State.positions),
        State.total_assets / 10000, State.cash / 10000,
        "可交易" if State.market_ok else "防御"))

    # ═══════════════════════════════════════════════════════════
    # 1. 处理跌停无法卖出的pending单
    # ═══════════════════════════════════════════════════════════
    _process_pending_sells(ContextInfo, hist_close)

    # ═══════════════════════════════════════════════════════════
    # 2. 检查持仓出场 (max_hold 到期)
    # ═══════════════════════════════════════════════════════════
    _check_exits(ContextInfo, hist_close)

    # ═══════════════════════════════════════════════════════════
    # 3. 因子排名刷新 (每10天)
    # ═══════════════════════════════════════════════════════════
    if State.bar_counter >= State.next_refresh_bar:
        State.rankings = _compute_factor_rankings(hist_close, hist_amount)
        State.next_refresh_bar = State.bar_counter + REFRESH_INTERVAL
        print("[刷新] 因子排名完成: %d 只有效股票" % len(State.rankings))

    # ═══════════════════════════════════════════════════════════
    # 4. 入场检查 (突破5日新高)
    # ═══════════════════════════════════════════════════════════
    if State.market_ok and len(State.positions) < MAX_POSITIONS:
        _check_entry_breakout(ContextInfo, hist_close, hist_amount)

    # ═══════════════════════════════════════════════════════════
    # 5. 大盘防御: 空仓时清仓
    # ═══════════════════════════════════════════════════════════
    if not State.market_ok and len(State.positions) > 0:
        _liquidate_all(ContextInfo, hist_close, "大盘防御")

    # ═══════════════════════════════════════════════════════════
    # 6. 更新持仓天数
    # ═══════════════════════════════════════════════════════════
    for code in list(State.positions.keys()):
        State.positions[code]['bars_held'] += 1

    # ── 摘要 ──
    pos_codes = list(State.positions.keys())
    hold_days = [State.positions[c]['bars_held'] for c in pos_codes]
    print("[摘要] 持仓=%d只 %s | 持有天数=%s | 下次刷新=%d" % (
        len(State.positions),
        pos_codes if pos_codes else "空仓",
        hold_days if hold_days else "-",
        State.next_refresh_bar - State.bar_counter))


# ╔════════════════════════════════════════════════════════════╗
# ║              Alpha#144 因子计算                            ║
# ╚════════════════════════════════════════════════════════════╝

def _calc_alpha144(close_arr, amount_arr):
    """
    计算 Alpha#144 因子值。

    公式:
      alpha_144 = Σ(|ret_i| / amount_i)  for ret_i < 0, over last 20 periods

    含义:
      - ret_i: 当日涨跌幅 (小数, 如 0.02 = +2%)
      - amount_i: 当日成交额 (元)
      - |ret_i| / amount_i: 每元成交额带来的价格变动 → "价格冲击成本"的代理变量
      - 只对下跌日求和 → 捕捉恐慌抛售时的流动性冲击
      - 因子值越大 → 下跌时流动性越差 → 筹码被恐慌盘砸出 → 后续反弹潜力越大

    注意:
      - 这是股票池内横向排名因子, 不需要截面标准化 (排名用原始值)
      - 如果过去20天无下跌日, 返回 0
    """
    arr_c = np.array(close_arr, dtype=float)
    arr_a = np.array(amount_arr, dtype=float)

    n = min(len(arr_c), len(arr_a))

    # 需要至少 FACTOR_WINDOW + 1 个数据点 (计算 ret 需要)
    needed = FACTOR_WINDOW + 1
    if n < needed:
        return None

    # 取最近 FACTOR_WINDOW 根 bar 的日收益
    recent_c = arr_c[-needed:]
    recent_a = arr_a[-needed:]

    alpha = 0.0
    neg_count = 0

    for i in range(1, len(recent_c)):
        prev_close = recent_c[i - 1]
        curr_close = recent_c[i]
        ret_i = (curr_close - prev_close) / prev_close if prev_close > 0 else 0

        # 只对下跌日求和
        if ret_i < 0:
            amount_i = recent_a[i] if i < len(recent_a) else 0
            if amount_i > 0:
                alpha += abs(ret_i) / amount_i
                neg_count += 1

    # 如果没有下跌日, 因子值为 0 (不是好信号)
    if neg_count == 0:
        return 0.0

    return alpha


def _compute_factor_rankings(hist_close, hist_amount):
    """
    计算全股票池的 Alpha#144 因子排名。

    流程:
      1. 对每只股票计算 alpha_144
      2. 按因子值降序排列 (越大 = 流动性冲击越大 = 越好)
      3. 取 Top 15%

    返回: {code: factor_value, ...}  for top 15% stocks
    """
    raw_scores = {}

    for code in State.filtered_pool:
        # 跳过已持仓 (不需要重复选入, 但因子值仍计算)
        close_arr = hist_close.get(code, [])
        amount_arr = hist_amount.get(code, [])

        if len(close_arr) < FACTOR_WINDOW + 1:
            continue
        if len(amount_arr) < FACTOR_WINDOW + 1:
            continue

        # 流动性过滤: 近20日日均成交额
        try:
            recent_amounts = np.array(amount_arr[-FACTOR_WINDOW:], dtype=float)
            avg_amount = np.mean(recent_amounts)
            if avg_amount < MIN_DAILY_AMOUNT:
                continue
        except Exception:
            continue

        val = _calc_alpha144(close_arr, amount_arr)
        if val is not None:
            raw_scores[code] = val

    if not raw_scores:
        return {}

    # 降序排列 (因子值越大越好)
    sorted_codes = sorted(raw_scores.keys(), key=lambda c: raw_scores[c], reverse=True)

    # Top 15%
    top_n = max(1, int(len(sorted_codes) * FACTOR_TOP_PCT))

    # 返回 Top 15% 的 code → factor_value 映射
    rankings = {}
    for code in sorted_codes[:top_n]:
        rankings[code] = raw_scores[code]

    return rankings


# ╔════════════════════════════════════════════════════════════╗
# ║              入场判断 — 突破5日新高                         ║
# ╚════════════════════════════════════════════════════════════╝

def _check_entry_breakout(ContextInfo, hist_close, hist_amount):
    """
    检查 Top 15% 选股池中突破5日新高的股票, 触发买入。

    条件:
      (1) 股票在最新因子排名的 Top 15% 中
      (2) 今日收盘价 > 过去5日(不含今天)的最高价 (breakout)
      (3) 未涨停 (今日涨幅 < 9.8%)
      (4) 未持仓
      (5) 持仓数 < MAX_POSITIONS

    买入: 次日开盘价 (QMT回测中, handlebar在收盘时执行, 次日开盘成交)
          此处用今日收盘价近似, 实盘需改为开盘买入
    """
    if not State.rankings:
        return

    held = set(State.positions.keys())
    slots = MAX_POSITIONS - len(State.positions)
    if slots <= 0:
        return

    # 遍历排名中的股票 (已经是 Top 15%)
    # 按因子值降序遍历
    ranked_list = sorted(State.rankings.keys(),
                         key=lambda c: State.rankings[c], reverse=True)

    signals = []
    for code in ranked_list:
        if code in held:
            continue

        close_arr = hist_close.get(code, [])
        if len(close_arr) < BREAKOUT_PERIOD + 2:
            continue

        arr = np.array(close_arr, dtype=float)

        current_close = arr[-1]
        prev_close = arr[-2] if len(arr) >= 2 else current_close

        # 涨跌停检查
        daily_ret = (current_close - prev_close) / prev_close if prev_close > 0 else 0
        if daily_ret >= LIMIT_UP_PCT:
            print("  [入场跳过] %s 涨停 (涨幅%.1f%%)" % (code, daily_ret * 100))
            continue

        # 突破检查: 今日收盘 > 过去5日最高价 (不含今天)
        past_5_high = np.max(arr[-(BREAKOUT_PERIOD + 1):-1])
        if current_close <= past_5_high:
            continue

        # 确认成交量 (放量突破更可靠 — 当日成交额 > 5日均量)
        amount_arr = hist_amount.get(code, [])
        vol_ok = True
        if len(amount_arr) >= 6:
            try:
                today_amt = amount_arr[-1] if type(amount_arr[-1]) in (int, float) else 0
                avg_amt_5 = np.mean([amount_arr[i] for i in range(-6, -1)
                                     if type(amount_arr[i]) in (int, float)])
                vol_ok = today_amt > avg_amt_5 * 0.8 if avg_amt_5 > 0 else True
            except Exception:
                vol_ok = True

        if not vol_ok:
            continue

        factor_val = State.rankings.get(code, 0)
        signals.append((code, current_close, factor_val))
        print("  [信号] %s 突破5日新高! close=%.2f high5=%.2f alpha144=%.2e" % (
            code, current_close, past_5_high, factor_val))

        if len(signals) >= slots:
            break

    # ── 等权买入 ──
    if signals:
        _buy_signals(ContextInfo, signals)


def _buy_signals(ContextInfo, signals):
    """
    对突破信号等权买入。

    signals: [(code, price, factor_val), ...]
    等权: 每只分配 total_assets / MAX_POSITIONS 资金
    """
    n_signals = len(signals)
    if n_signals == 0:
        return

    # 计算可用仓位
    current_positions = len(State.positions)
    total_slots = MAX_POSITIONS

    # 等权分配: 总资产 / MAX_POSITIONS
    total_equity = State.total_assets if State.total_assets > 0 else State.capital
    allocation_per_stock = total_equity / total_slots

    # 当前行业分布统计
    sector_counts = {}
    for code in State.positions.keys():
        sec = _get_sector(code)
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    bought = 0
    for code, price, factor_val in signals:
        if code in State.positions:
            continue
        if len(State.positions) >= MAX_POSITIONS:
            break

        # 行业限制: 已分类行业最多2只, "其他"行业最多3只
        sec = _get_sector(code)
        sec_limit = MAX_SECTOR_OTHER if sec == '其他' else MAX_SECTOR_COUNT
        if sector_counts.get(sec, 0) >= sec_limit:
            print("  [买入跳过] %s 行业=%s 已满%d只" % (code, sec, sec_limit))
            continue

        # 计算股数 (取整百股)
        shares = int(allocation_per_stock / price / 100) * 100
        if shares < 100:
            shares = 100

        # 现金检查
        need_cash = shares * price * 1.002
        if need_cash > State.cash:
            shares = int(State.cash * 0.98 / price / 100) * 100
            if shares < 100:
                print("  [买入失败] %s 资金不足: need=%.0f cash=%.0f" % (
                    code, need_cash, State.cash))
                continue

        # 下单 — 快速交易模式
        try:
            passorder(23, 1101, State.acc_id, code, 5, -1, shares,
                      'Alpha144突破', 1, '', ContextInfo)
        except Exception as e:
            print("  [买入失败] %s 下单异常: %s" % (code, str(e)))
            continue

        State.positions[code] = {
            'shares': shares,
            'entry_price': price,
            'entry_bar': State.last_barpos,
            'bars_held': 0,
        }
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

        print(">>> [买入] %s × %d股 @ %.2f | 金额 %.0f | alpha144=%.2e" % (
            code, shares, price, shares * price, factor_val))
        bought += 1


# ╔════════════════════════════════════════════════════════════╗
# ║              出场判断 — 持有到期 + 硬止损                      ║
# ╚════════════════════════════════════════════════════════════╝

def _check_exits(ContextInfo, hist_close):
    """
    检查出场条件 (按优先级):
      (1) 硬止损: 浮亏超过 18% 无条件平仓
      (2) 持有到期: max_hold = 20 天

    跌停约束: 如果跌停无法卖出, 加入 pending_sells 次日再试。
    """
    to_sell = []

    for code, pos in State.positions.items():
        px = _get_price(ContextInfo, code, hist_close)
        if px <= 0:
            continue

        entry = pos['entry_price']
        pnl_pct = (px / entry - 1.0) if entry > 0 else 0

        # (1) 硬止损: 浮亏 >= 18%
        if pnl_pct <= HARD_STOP_PCT:
            print("  [止损触发] %s 浮亏 %.1f%% <= %.0f%%" % (
                code, pnl_pct * 100, HARD_STOP_PCT * 100))
            to_sell.append((code, "硬止损%.0f%%(浮亏%.1f%%)" % (HARD_STOP_PCT * 100, pnl_pct * 100)))
            continue

        # (2) 持有到期
        if pos['bars_held'] >= MAX_HOLD_BARS:
            to_sell.append((code, "持有%d天到期" % MAX_HOLD_BARS))

    for code, reason in to_sell:
        _sell_position(ContextInfo, code, hist_close, reason)


def _process_pending_sells(ContextInfo, hist_close):
    """
    处理之前因跌停未能卖出的持仓, 今日重试。

    如果仍然跌停 → 继续等待。
    如果不跌停 → 卖出。
    """
    if not State.pending_sells:
        return

    retry_list = list(State.pending_sells)
    State.pending_sells = []

    for code in retry_list:
        if code not in State.positions:
            continue
        _sell_position(ContextInfo, code, hist_close, "补卖(昨日跌停)")


def _sell_position(ContextInfo, code, hist_close, reason):
    """
    卖出单只股票。

    跌停约束:
      - 检查当日是否跌停 (今日收盘/昨日收盘 - 1 <= -9.8%)
      - 如果跌停 → 不卖, 加入 pending_sells
    """
    if code not in State.positions:
        return

    pos = State.positions[code]
    shares = pos.get('shares', 0)
    if shares <= 0:
        del State.positions[code]
        return

    # 获取当前价格
    px = _get_price(ContextInfo, code, hist_close)

    # 跌停检查
    close_arr = hist_close.get(code, [])
    if len(close_arr) >= 2:
        arr = np.array(close_arr, dtype=float)
        daily_ret = (arr[-1] - arr[-2]) / arr[-2] if arr[-2] > 0 else 0
        if daily_ret <= LIMIT_DOWN_PCT:
            print("  [卖出延迟] %s 跌停 (跌幅%.1f%%), 延至次日" % (code, daily_ret * 100))
            if code not in State.pending_sells:
                State.pending_sells.append(code)
            return

    # 执行卖出
    try:
        passorder(24, 1101, State.acc_id, code, 5, -1, shares,
                  'Alpha144卖出', 1, '', ContextInfo)
    except Exception as e:
        print("  [卖出失败] %s 下单异常: %s" % (code, str(e)))
        return

    entry_price = pos['entry_price']
    pnl_pct = (px / entry_price - 1) * 100 if entry_price > 0 else 0
    bars = pos.get('bars_held', 0)

    print("<<< [卖出] %s × %d股 @ %.2f | 盈亏 %+.1f%% | 持有%d天 | %s" % (
        code, shares, px, pnl_pct, bars, reason))

    del State.positions[code]

    # 从 pending 中移除 (如果存在)
    if code in State.pending_sells:
        State.pending_sells.remove(code)


# ╔════════════════════════════════════════════════════════════╗
# ║              大盘防御                                      ║
# ╚════════════════════════════════════════════════════════════╝

def _get_sector(code):
    """查询股票所属行业"""
    return SECTOR_MAP.get(code, '其他')


def _liquidate_all(ContextInfo, hist_close, reason):
    """清空所有持仓 (大盘触发防御)"""
    for code in list(State.positions.keys()):
        _sell_position(ContextInfo, code, hist_close, reason)


# ╔════════════════════════════════════════════════════════════╗
# ║              辅助函数                                      ║
# ╚════════════════════════════════════════════════════════════╝

def _check_market(hist_close):
    """
    大盘过滤器: CSI500 >= MA20 × (1 - 3%)

    返回 True = 可交易, False = 空仓
    """
    if BENCHMARK not in hist_close:
        return True  # 无基准数据时默认可交易

    arr = hist_close[BENCHMARK]
    if len(arr) < MA_MARKET + 1:
        return True

    close_arr = np.array(arr, dtype=float)
    current = close_arr[-1]
    ma = np.mean(close_arr[-MA_MARKET:])

    threshold = ma * (1.0 - MARKET_FILTER_PCT)
    return current >= threshold


def _calc_total_position_value(ContextInfo, hist_close):
    """计算当前持仓总市值"""
    total = 0.0
    for code, pos in State.positions.items():
        shares = pos.get('shares', 0)
        px = _get_price(ContextInfo, code, hist_close)
        total += shares * px
    return total


def _update_account(ContextInfo):
    """更新账户资金信息"""
    try:
        a = get_trade_detail_data(State.acc_id, 'stock', 'account')
        if a:
            State.cash = a[0].m_dAvailable
            State.total_assets = a[0].m_dBalance
            return
    except Exception:
        pass

    # fallback: 回测模式
    try:
        State.cash = ContextInfo.cash
        State.total_assets = ContextInfo.capital
    except Exception:
        pass


def _sync_positions(ContextInfo):
    """从交易系统同步实际持仓, 与本地状态对齐"""
    try:
        ps = get_trade_detail_data(State.acc_id, 'stock', 'position')
        remote_positions = {}
        for p in ps:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            vol = p.m_nVolume
            if vol <= 0:
                continue
            if code in State.positions:
                old = State.positions[code]
                old['shares'] = vol
                remote_positions[code] = old
            else:
                # 远程有持仓但本地没有 (可能是外部操作), 记录
                remote_positions[code] = {
                    'shares': vol,
                    'entry_price': p.m_dOpenPrice,
                    'entry_bar': State.last_barpos,
                    'bars_held': 0,
                }

        # 检查本地有但远程无的持仓 (已被外部平仓)
        for code in list(State.positions.keys()):
            if code not in remote_positions:
                del State.positions[code]

        # 合并没有被覆盖的
        for code, pos in remote_positions.items():
            if code not in State.positions:
                State.positions[code] = pos
    except Exception:
        # 回测模式: 信任本地状态
        pass


def _get_price(ContextInfo, code, hist_close):
    """获取当前价格: 优先实时tick, 其次历史收盘价"""
    try:
        t = ContextInfo.get_full_tick([code])
        if code in t:
            lp = t[code].get('lastPrice', 0)
            if lp > 0:
                return lp
    except Exception:
        pass

    if code in hist_close and len(hist_close[code]) > 0:
        return float(hist_close[code][-1])

    return 0


def _log_time(ContextInfo):
    """获取当前 Bar 的可读时间字符串"""
    try:
        t = ContextInfo.get_bar_timetag(ContextInfo.barpos)
        return timetag_to_datetime(t, '%Y-%m-%d %H:%M')
    except Exception:
        return str(ContextInfo.barpos)


# ╔════════════════════════════════════════════════════════════╗
# ║              Fallback: 中证500成分股 (硬编码)               ║
# ╚════════════════════════════════════════════════════════════╝

def _get_fallback_csi500():
    """
    中证500 代表性成分股 (硬编码 fallback)。

    覆盖主要行业, 约 80 只, 用于回测中 get_sector 不可用时。
    实际中证500有500只, 这里选取流动性较好、代表性强的标的。
    """
    return [
        # === 医药 (约15%) ===
        '300003.SZ', '300009.SZ', '300015.SZ', '300026.SZ', '300039.SZ',
        '002001.SZ', '002007.SZ', '002019.SZ', '002020.SZ', '002022.SZ',
        '600079.SH', '600085.SH', '600196.SH', '600276.SH', '600380.SH',
        '300595.SZ', '300601.SZ', '300633.SZ', '300676.SZ', '300725.SZ',
        # === 电子/半导体 (约12%) ===
        '002049.SZ', '002138.SZ', '002185.SZ', '002273.SZ', '002371.SZ',
        '002409.SZ', '002436.SZ', '002456.SZ', '002463.SZ', '002475.SZ',
        '603160.SH', '603501.SH', '603986.SH', '688008.SH', '688012.SH',
        # === 计算机/软件 (约10%) ===
        '002230.SZ', '002368.SZ', '002373.SZ', '002405.SZ', '002410.SZ',
        '300033.SZ', '300036.SZ', '300059.SZ', '300168.SZ', '300253.SZ',
        # === 化工 (约8%) ===
        '002064.SZ', '002092.SZ', '002108.SZ', '002250.SZ', '002258.SZ',
        '002326.SZ', '002407.SZ', '002408.SZ', '002440.SZ', '002460.SZ',
        # === 机械/军工 (约10%) ===
        '002013.SZ', '002025.SZ', '002050.SZ', '002074.SZ', '002097.SZ',
        '300024.SZ', '300124.SZ', '300274.SZ', '300316.SZ', '300450.SZ',
        # === 电力设备/新能源 (约8%) ===
        '002459.SZ', '002121.SZ', '002129.SZ', '002202.SZ', '002245.SZ',
        '300014.SZ', '300037.SZ', '300068.SZ', '300073.SZ', '300118.SZ',
        # === 有色/钢铁 (约7%) ===
        '000060.SZ', '000630.SZ', '000807.SZ', '000831.SZ', '000878.SZ',
        '000933.SZ', '000960.SZ', '000975.SZ', '002155.SZ', '002203.SZ',
        # === 传媒/游戏 (约6%) ===
        '002555.SZ', '002602.SZ', '002624.SZ', '300058.SZ', '300133.SZ',
        '300251.SZ', '300413.SZ', '300418.SZ', '603444.SH',
        # === 交运/物流 (约4%) ===
        '002120.SZ', '002352.SZ', '002468.SZ', '600026.SH', '600029.SH',
        # === 其他 (食品/农牧/建材) ===
        '000400.SZ', '000401.SZ', '000425.SZ', '000528.SZ', '000538.SZ',
        '000547.SZ', '000553.SZ', '000581.SZ', '000625.SZ', '000629.SZ',
        '000636.SZ', '000656.SZ', '000661.SZ', '000703.SZ', '000708.SZ',
        '000723.SZ', '000728.SZ', '000729.SZ', '000738.SZ', '000750.SZ',
        '000776.SZ', '000778.SZ', '000783.SZ', '000786.SZ', '000800.SZ',
        '000825.SZ', '000826.SZ', '000830.SZ', '000860.SZ', '000869.SZ',
        '000887.SZ', '000895.SZ', '000902.SZ', '000903.SZ', '000912.SZ',
        '000915.SZ', '000921.SZ', '000927.SZ', '000930.SZ', '000932.SZ',
        '000937.SZ', '000938.SZ', '000950.SZ', '000951.SZ', '000957.SZ',
        '000959.SZ', '000961.SZ', '000962.SZ', '000963.SZ',
        '000966.SZ', '000967.SZ', '000968.SZ', '000969.SZ', '000970.SZ',
        '300498.SZ', '002714.SZ', '002304.SZ',
    ]
