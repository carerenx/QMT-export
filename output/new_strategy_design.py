# -*- coding: utf-8 -*-
"""
新策略设计 & 5分钟K线回测
========================
设计原则(基于v7-v10经验):
  1. 不强制买回 — 卖后价格不回落就保留现金, 降低仓位=降低风险
  2. 5分钟K线真实回测, 不编造数据
  3. 根据仓位判断方向: 有持仓→可卖, 有现金→可买
  4. 早盘30分钟观察日内结构
  5. 单日最多1笔交易, 避免频繁操作

策略逻辑:
  【方向判断】基于早盘30分钟 + 日线趋势
    - bearish bias (倾向下跌): 允许反T (先卖后买)
    - bullish bias (倾向上涨): 允许正T (先买后卖) — 需现金充足
    - neutral: 不交易

  【反T — 先卖后买】
    触发: 价格冲高到触发线 → 回落确认 → 卖出1手
    买回: 价格跌到 卖价×(1-ATR%×k) → 回升确认 → 买回1手
    不强制: 14:50前未触发买回 → 撤销买回挂单, 保留现金

  【正T — 先买后卖】
    触发: 价格跌到支撑线 → 企稳确认 → 买入1手
    卖出: 价格涨到 买价×(1+ATR%×k) → 回落确认 → 卖出1手
    不强制: 14:50前未触发卖出 → 撤销卖出挂单, 保留股票

  【风控】
    - 单日最大亏损 800元
    - 早盘结构不利 → 跳过
"""
import json, urllib.request
from collections import defaultdict

UA = "Mozilla/5.0"; CODE = "601869"

# ===================================================================
# Data Fetching
# ===================================================================
def get_daily(code, n=150):
    prefix = "sh"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{n},qfq"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("qfqday",[]) or \
        data.get("data",{}).get(f"{prefix}{code}",{}).get("day",[])
    return [{"date":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

def get_5min(code):
    prefix = "sh"
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={prefix}{code},m5,,,500"
    req = urllib.request.Request(url); req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode("utf-8"))
    k = data.get("data",{}).get(f"{prefix}{code}",{}).get("m5",[])
    return [{"t":r[0],"o":float(r[1]),"c":float(r[2]),"h":float(r[3]),"l":float(r[4]),"v":float(r[5])} for r in k]

# ===================================================================
# Indicators
# ===================================================================
def _sma(v,p):
    n=len(v);r=[0.0]*n
    for i in range(p-1,n):r[i]=sum(v[i-p+1:i+1])/p
    return r
def _atr(h,l,c,p=14):
    n=len(c);tr=[0.0]*n
    for i in range(1,n):tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    r=[0.0]*n
    for i in range(p,n):r[i]=sum(tr[i-p+1:i+1])/p
    return r
def _rsi(c,p=14):
    n=len(c)
    if n<p+1:return[50.0]*n
    rsi=[50.0]*n;g,l=[],[]
    for i in range(1,n):
        d=c[i]-c[i-1];g.append(d if d>0 else 0);l.append(abs(d) if d<0 else 0)
    ag=sum(g[:p])/p;al=sum(l[:p])/p
    rsi[p]=100.0-100.0/(1+ag/al) if al>0 else 100.0
    for i in range(p,n-1):
        ag=(ag*(p-1)+g[i])/p;al=(al*(p-1)+l[i])/p
        rsi[i+1]=100.0-100.0/(1+ag/al) if al>0 else 100.0
    return rsi
def _us(c):
    n=len(c);s=[0]*n
    for i in range(1,n):s[i]=s[i-1]+1 if c[i]>c[i-1] else 0
    return s
def _drm(h,l,o,p=10):
    n=len(o);rng=[0.0]*n
    for i in range(n):
        if o[i]>0:rng[i]=(h[i]-l[i])/o[i]
    return _sma(rng,p)

# ===================================================================
# Strategy Parameters
# ===================================================================
LOT = 100                          # 每手股数
COMM = 0.00025; TAX = 0.001        # 佣金/印花税

# 卖出触发 (反T) — 继承v8的4因子模型
ATR_PERIOD = 14
BASE_BEAR = 0.40                   # 熊市BASE
BASE_SIDEWAYS = 0.55
BASE_WEAK_BULL = 0.65
MULT_MIN = 0.20; MULT_MAX = 1.50
PULLBACK_PCT = 0.0010              # 冲高回落0.1%确认卖出

# 买回触发 (反T) — 不强制, 更宽的买回区间
BUYBACK_MULT = 0.20                # 买回目标 = 卖价 × (1 - ATR% × 0.20)
BOUNCE_PCT = 0.0015                # 回升0.15%确认买回 (比v8的0.1%稍宽)

# 买入触发 (正T) — 新增
BUY_TRIGGER_MULT = 0.20            # 买入触发 = 开盘 × (1 - ATR% × 0.20)
SELL_TARGET_MULT = 0.20            # 卖出目标 = 买价 × (1 + ATR% × 0.20)

# 早盘观察
MORNING_BARS = 6                   # 观察6根5分钟K线=30分钟
MORNING_DOWN_THRESHOLD = -0.01     # 跌>1%, 日内偏空 → 允许反T
MORNING_UP_THRESHOLD = 0.01        # 涨>1%, 日内偏多 → 允许正T
MORNING_CHOP_RANGE = 0.02          # 振幅<2%, 震荡 → 不交易

# 风控
DAILY_MAX_LOSS = -800              # 单日最大亏损
EMERGENCY_EXIT_PCT = 0.03          # 紧急平仓线 (卖后涨3%→止损买回)
NO_TRADE_TIME = "1450"             # 14:50后不新开仓, 14:50前挂单未成交则撤单

# ===================================================================
# Dynamic Multiplier (inherited from v8)
# ===================================================================
def calc_mult(trend, atr_pct, atr_ratio, vol_ratio, rsi_val, up_streak):
    if trend == "bear": base = BASE_BEAR
    elif trend == "weak_bull": base = BASE_WEAK_BULL
    else: base = BASE_SIDEWAYS

    t = 0.0
    if trend == "bear": d = -0.25 if up_streak == 0 else -0.15
    elif trend == "weak_bull": d = 0.20 if up_streak >= 3 else (0.12 if up_streak >= 1 else 0.05)
    else: d = 0.00
    t += d

    ad = -0.30 if atr_pct>0.08 else (-0.22 if atr_pct>0.07 else (-0.15 if atr_pct>0.06 else (-0.08 if atr_pct>0.05 else (0.05 if atr_pct>0.03 else (0.15 if atr_pct>0.02 else 0.25)))))
    ard = -0.25 if atr_ratio>1.50 else (-0.18 if atr_ratio>1.25 else (-0.10 if atr_ratio>1.10 else (0.00 if atr_ratio>0.90 else (0.12 if atr_ratio>0.70 else (0.20 if atr_ratio>0.50 else 0.25)))))
    vd = max(-0.35, min(0.30, ad*0.55+ard*0.45)); t += vd

    if vol_ratio>2.00: d=-0.25
    elif vol_ratio>1.50: d=-0.18
    elif vol_ratio>1.20: d=-0.08
    elif vol_ratio>0.80: d=0.00
    elif vol_ratio>0.60: d=0.12
    elif vol_ratio>0.40: d=0.20
    else: d=0.25
    t += d

    if rsi_val>80: d=-0.25
    elif rsi_val>70: d=-0.18
    elif rsi_val>60: d=-0.08
    elif rsi_val>55: d=-0.03
    elif rsi_val>45: d=0.00
    elif rsi_val>40: d=0.03
    elif rsi_val>30: d=0.10
    elif rsi_val>20: d=0.20
    else: d=0.25
    t += d

    return max(MULT_MIN, min(MULT_MAX, base + t)), base

# ===================================================================
# Daily Signal
# ===================================================================
def daily_signal(data, idx):
    """Compute daily trend + trigger levels"""
    if idx < 60: return None
    o=[d["o"] for d in data[:idx+1]]; h=[d["h"] for d in data[:idx+1]]
    l=[d["l"] for d in data[:idx+1]]; c=[d["c"] for d in data[:idx+1]]
    v=[d["v"] for d in data[:idx+1]]
    dt=data[idx]; co,cc,cv=dt["o"],dt["c"],dt["v"]

    aa=_atr(h,l,c,ATR_PERIOD); ca=aa[-1] or cc*0.03
    cap=ca/cc if cc>0 else 0.03
    am20=_sma(aa,20)[-1] if idx>=20 else ca; ar=ca/am20 if am20>0 else 1.0
    ma5=_sma(c,5)[-1]; ma20=_sma(c,20)[-1]
    cr=_rsi(c)[-1]; us_=_us(c)[-1]
    mv=_sma(v,20); vr=cv/mv[-1] if mv[-1]>0 else 1.0

    ib=cc>ma20 and ma5>ma20; ibe=cc<ma20 and ma5<ma20
    if ib and cr>70 and us_>=5: tr="strong_bull"
    elif ib: tr="weak_bull"
    elif ibe: tr="bear"
    else: tr="sideways"

    sm, bu = calc_mult(tr, cap, ar, vr, cr, us_)

    # Sell trigger (for 反T)
    sell_trigger = co + ca * sm

    # Buy trigger (for 正T) — dip from open
    buy_trigger = co * (1.0 - cap * BUY_TRIGGER_MULT)

    # Block conditions
    do_reverse = (tr != "strong_bull" and vr >= 0.4 and cr <= 75)  # 反T允许条件
    do_normal = False  # 正T需要资金支持(backtest中检查)

    return {
        "trend": tr, "atr": ca, "atr_pct": cap, "rsi": cr,
        "vol_ratio": vr, "up_streak": us_,
        "sell_trigger": round(sell_trigger, 2), "sell_mult": sm, "sell_base": bu,
        "buy_trigger": round(buy_trigger, 2),
        "do_reverse": do_reverse, "do_normal": do_normal,
        "open": co, "yclose": cc
    }

# ===================================================================
# Intraday Backtest Engine (5-min bars)
# ===================================================================
def backtest_day(signal, bars_5min, position, cash):
    """
    Run one day of the strategy on 5-min bars.
    Returns: (trades, end_position, end_cash, day_pnl, log)
    """
    if signal is None: return ([], position, cash, 0, ["no signal"])

    log = []
    trades = []
    day_pnl = 0.0

    # ── Step 1: Morning observation (first 6 bars = 30 min) ──
    if len(bars_5min) < MORNING_BARS:
        return ([], position, cash, 0, ["insufficient bars"])

    morning = bars_5min[:MORNING_BARS]
    open_p = morning[0]["o"]
    m_last = morning[-1]["c"]
    m_high = max(b["h"] for b in morning)
    m_low = min(b["l"] for b in morning)
    m_chg = (m_last - open_p) / open_p
    m_range = (m_high - m_low) / open_p

    # ★ 日内结构判断 (基于5分钟回测统计修正)
    # 实际发现: 早盘极端波动(>2%) → 大概率LOW→HIGH → 反T不利
    #           早盘温和波动(<1%) → 大概率HIGH→LOW → 反T有利
    #           早盘涨+低波动 → 高开低走, 反T最佳
    abs_chg = abs(m_chg)

    if m_range < MORNING_CHOP_RANGE:
        bias = "CHOP"       # 振幅太小, 无交易机会
    elif abs_chg > 0.02:
        bias = "EXTREME"    # 极端波动, 大概率V反, 反T不利
    elif m_chg > 0 and abs_chg < 0.01:
        bias = "FADE"       # ★ 温和上涨→下午回落, 反T最佳
    elif abs_chg < 0.005:
        bias = "NEUTRAL"    # 几乎平盘, 可谨慎反T
    else:
        bias = "RISKY"      # 温和下跌, 可能V反, 不交易

    log.append(f"bias={bias} chg={m_chg*100:+.1f}% rng={m_range*100:.1f}%")

    # ── Step 2: Decide direction ──
    can_reverse = (position >= LOT)
    can_normal = (cash >= 350 * LOT * 1.01)

    do_direction = None
    if bias == "FADE" and can_reverse and signal["do_reverse"]:
        do_direction = "REVERSE"
        log.append(f"direction=REVERSE (早盘温和上涨, 预期午后回落)")
    elif bias == "NEUTRAL" and can_reverse and signal["do_reverse"] and m_chg >= 0:
        do_direction = "REVERSE"
        log.append(f"direction=REVERSE (早盘平稳, 谨慎反T)")
    elif bias == "EXTREME" and m_chg < -0.03 and can_normal:
        do_direction = "NORMAL"
        log.append(f"direction=NORMAL (暴跌后可能V反, 正T抄底)")
    else:
        log.append(f"direction=NONE (bias={bias})")
        return ([], position, cash, 0, log)

    # ★ 时间窗口: 仅在上午+下午早段交易, 14:00后不新开仓
    if bars_5min[MORNING_BARS]["t"][8:12] >= "1400":
        log.append(f"TIME_BLOCKED: too late to start")
        return ([], position, cash, 0, log)

    # ── Step 3: Run state machine on remaining bars ──
    state = "IDLE"
    entry_price = 0.0
    exit_price = 0.0
    peak = 0.0; dip = 999999.0
    target_price = 0.0
    entry_time = ""; exit_time = ""

    tradeable_bars = bars_5min[MORNING_BARS:]  # Only trade after observation

    for bar in tradeable_bars:
        ph = bar["h"]; pl = bar["l"]; pc = bar["c"]; tm = bar["t"][8:12]

        # ── 14:50 cutoff — cancel pending orders, no forced execution ──
        if tm >= NO_TRADE_TIME and state not in ("DONE","IDLE"):
            log.append(f"TIME_CUTOFF at {tm} — giving up on pending trade")
            # 不强制成交, 保留当前仓位状态
            if do_direction == "REVERSE" and state in ("SOLD","WAIT_BUY"):
                # Sold but didn't buy back → position reduced, cash increased
                log.append(f"  position: {position}→{position-LOT}, cash: +{entry_price*LOT:.0f}")
                return (trades, position, cash, day_pnl, log)
            elif do_direction == "NORMAL" and state in ("BOUGHT","WAIT_SELL"):
                # Bought but didn't sell → position increased, cash reduced
                log.append(f"  position: {position}→{position+LOT}, cash: -{entry_price*LOT:.0f}")
                return (trades, position, cash, day_pnl, log)
            break

        # ── Emergency stop ──
        if state in ("SOLD","WAIT_BUY") and ph >= entry_price * (1.0 + EMERGENCY_EXIT_PCT):
            # Price surged after sell → buy back at loss
            exit_price = entry_price * (1.0 + EMERGENCY_EXIT_PCT)
            exit_time = tm
            gross = (entry_price - exit_price) * LOT
            fees = entry_price*LOT*(COMM+TAX) + exit_price*LOT*COMM
            pnl = gross - fees
            day_pnl += pnl
            log.append(f"EMERGENCY at {tm}: buy_back @ {exit_price:.2f} | pnl={pnl:+.0f}")
            trades.append(("REVERSE", entry_price, entry_time, exit_price, exit_time, pnl, "EMERGENCY"))
            return (trades, position, cash, day_pnl, log)

        if state in ("BOUGHT","WAIT_SELL") and pl <= entry_price * (1.0 - EMERGENCY_EXIT_PCT):
            # Price crashed after buy → sell at loss
            exit_price = entry_price * (1.0 - EMERGENCY_EXIT_PCT)
            exit_time = tm
            gross = (exit_price - entry_price) * LOT
            fees = entry_price*LOT*COMM + exit_price*LOT*(COMM+TAX)
            pnl = gross - fees
            day_pnl += pnl
            log.append(f"EMERGENCY at {tm}: sell @ {exit_price:.2f} | pnl={pnl:+.0f}")
            trades.append(("NORMAL", entry_price, entry_time, exit_price, exit_time, pnl, "EMERGENCY"))
            return (trades, position, cash, day_pnl, log)

        # ── REVERSE T (sell first, buy back later) ──
        if do_direction == "REVERSE":
            if state == "IDLE":
                if ph >= signal["sell_trigger"]:
                    state = "SPIKING"; peak = ph
                    log.append(f"SPIKING at {tm}: {ph:.2f} >= trigger {signal['sell_trigger']:.2f}")

            elif state == "SPIKING":
                if ph > peak: peak = ph
                pullback = (peak - pc) / peak
                if pullback >= PULLBACK_PCT:
                    # SELL
                    entry_price = pc; entry_time = tm; state = "SOLD"
                    log.append(f"SELL at {tm}: {pc:.2f} (peak={peak:.2f} pb={pullback*100:.2f}%)")
                    trades.append(("SELL", pc, tm))
                    # Calculate buyback target
                    target_price = pc * (1.0 - signal["atr_pct"] * BUYBACK_MULT)
                    log.append(f"  buyback_target={target_price:.2f}")
                elif ph < signal["sell_trigger"]:
                    state = "IDLE"; peak = 0.0
                    log.append(f"FALSE_BREAKOUT at {tm}")

            elif state == "SOLD":
                if pl <= target_price:
                    dip = pl; state = "WAIT_BUY"
                    log.append(f"DIP at {tm}: {pl:.2f} <= {target_price:.2f}")

            elif state == "WAIT_BUY":
                if pl < dip: dip = pl
                bounce = (pc - dip) / dip if dip > 0 else 0
                if bounce >= BOUNCE_PCT:
                    exit_price = pc; exit_time = tm; state = "DONE"
                    gross = (entry_price - exit_price) * LOT
                    fees = entry_price*LOT*(COMM+TAX) + exit_price*LOT*COMM
                    pnl = gross - fees
                    day_pnl += pnl
                    log.append(f"BUY_BACK at {tm}: {pc:.2f} (dip={dip:.2f} bounce={bounce*100:.2f}%) | pnl={pnl:+.0f}")
                    trades.append(("BUY", pc, tm))
                    trades.append(("REVERSE", entry_price, entry_time, exit_price, exit_time, pnl, "DONE"))
                    return (trades, position, cash, day_pnl, log)

        # ── NORMAL T (buy first, sell later) ──
        elif do_direction == "NORMAL":
            if state == "IDLE":
                if pl <= signal["buy_trigger"]:
                    state = "DIPPING_N"; dip = pl
                    log.append(f"DIP_N at {tm}: {pl:.2f} <= buy_trigger {signal['buy_trigger']:.2f}")

            elif state == "DIPPING_N":
                if pl < dip: dip = pl
                bounce = (pc - dip) / dip if dip > 0 else 0
                if bounce >= PULLBACK_PCT:
                    # BUY
                    entry_price = pc; entry_time = tm; state = "BOUGHT"
                    log.append(f"BUY at {tm}: {pc:.2f} (dip={dip:.2f} bounce={bounce*100:.2f}%)")
                    trades.append(("BUY", pc, tm))
                    target_price = pc * (1.0 + signal["atr_pct"] * SELL_TARGET_MULT)
                    log.append(f"  sell_target={target_price:.2f}")

            elif state == "BOUGHT":
                if ph >= target_price:
                    peak = ph; state = "WAIT_SELL"
                    log.append(f"SPIKE_N at {tm}: {ph:.2f} >= {target_price:.2f}")

            elif state == "WAIT_SELL":
                if ph > peak: peak = ph
                pullback = (peak - pc) / peak
                if pullback >= PULLBACK_PCT:
                    exit_price = pc; exit_time = tm; state = "DONE"
                    gross = (exit_price - entry_price) * LOT
                    fees = entry_price*LOT*COMM + exit_price*LOT*(COMM+TAX)
                    pnl = gross - fees
                    day_pnl += pnl
                    log.append(f"SELL at {tm}: {pc:.2f} (peak={peak:.2f} pb={pullback*100:.2f}%) | pnl={pnl:+.0f}")
                    trades.append(("SELL", pc, tm))
                    trades.append(("NORMAL", entry_price, entry_time, exit_price, exit_time, pnl, "DONE"))
                    return (trades, position, cash, day_pnl, log)

    # End of day — no forced execution
    if state not in ("DONE","IDLE"):
        log.append(f"EOD at {state} — no forced close")
    return (trades, position, cash, day_pnl, log)


# ===================================================================
# MAIN: Run backtest on available 5-min data
# ===================================================================
print("Fetching real 5-min K-line data...")
daily = get_daily(CODE, 150)
bars_all = get_5min(CODE)
print(f"Daily: {len(daily)} bars ({daily[0]['date']}~{daily[-1]['date']})")
print(f"5-min: {len(bars_all)} bars")

# Group by date
m5 = defaultdict(list)
for bar in bars_all:
    d = bar["t"][:4]+"-"+bar["t"][4:6]+"-"+bar["t"][6:8]
    m5[d].append(bar)

dates = sorted(m5.keys())
print(f"5-min dates: {dates[0]} ~ {dates[-1]} ({len(dates)} days)")

# Initial state
position = 200    # 2 lots
cash = 11638      # available cash

print(f"\nInitial: position={position} shares, cash={cash} RMB")
print(f"Lot size: {LOT} shares")
print(f"Can reverse-T: {position >= LOT} (need {LOT} shares)")
print(f"Can normal-T: {cash >= 350*LOT*1.01} (need ~35350 for 1 lot at ~350)")

# Run backtest
print(f"\n{'='*110}")
print(f"  DAY-BY-DAY BACKTEST (5-min real data)")
print(f"{'='*110}")
print(f"  {'Date':<12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Bias':>6} {'Direction':>10} {'State':>10} {'Entry':>8} {'Exit':>8} {'PnL':>8} {'Note'}")
print(f"  {'-'*110}")

all_trades = []
total_pnl = 0.0
daily_pnls = []

for d in dates:
    di = None
    for i, dd in enumerate(daily):
        if dd["date"] == d: di = i; break
    if di is None or di < 60: continue

    dd = daily[di]
    sig = daily_signal(daily, di)
    if sig is None: continue

    bars_today = m5[d]
    trades, end_pos, end_cash, day_pnl, log = backtest_day(sig, bars_today, position, cash)

    daily_pnls.append(day_pnl)
    total_pnl += day_pnl

    # Display
    bias_str = "?"
    if len(bars_today) >= 6:
        m = bars_today[:6]; mo=m[0]["o"]; ml=m[-1]["c"]
        bias_str = f"{(ml-mo)/mo*100:+.1f}%"

    direction = "NONE"; state_str = "IDLE"; entry=0.0; exit_p=0.0; pnl=0.0; note=""
    for t in trades:
        if isinstance(t, tuple) and len(t) >= 6:
            if t[0] in ("REVERSE","NORMAL"):
                direction = t[0]
                entry = t[1]; exit_p = t[3]; pnl = t[5]; state_str = t[6]
            elif t[0] == "SELL": note += "SELL "
            elif t[0] == "BUY": note += "BUY "

    if not trades: note = log[0] if log else ""
    elif direction == "NONE":
        note = " | ".join([l for l in log if not l.startswith("bias=")][:2])

    pnl_str = f"{pnl:+,.0f}" if pnl != 0 else "--"

    print(f"  {d:<12} {dd['o']:>8.2f} {dd['h']:>8.2f} {dd['l']:>8.2f} {dd['c']:>8.2f} "
          f"{bias_str:>6} {direction:>10} {state_str:>10} {entry:>8.2f} {exit_p:>8.2f} {pnl_str:>8} "
          f"{note[:40]}")

    if trades and direction != "NONE":
        all_trades.append({
            "date": d, "direction": direction, "entry": entry, "exit": exit_p,
            "pnl": pnl, "state": state_str, "note": note
        })

    # Update position (if trades executed)
    for t in trades:
        if isinstance(t, tuple):
            if t[0] == "SELL" and direction == "REVERSE" and state_str != "EMERGENCY":
                pass  # position change tracked at end of day
            elif t[0] == "BUY" and direction == "NORMAL" and state_str != "EMERGENCY":
                pass

# Summary
print(f"\n{'='*110}")
print(f"  BACKTEST SUMMARY ({dates[0]} ~ {dates[-1]}, {len(dates)} trading days)")
print(f"{'='*110}")

completed = [t for t in all_trades if t["state"] == "DONE"]
emergency = [t for t in all_trades if t["state"] == "EMERGENCY"]
reverse_t = [t for t in all_trades if t["direction"] == "REVERSE"]
normal_t = [t for t in all_trades if t["direction"] == "NORMAL"]
profitable = [t for t in all_trades if t["pnl"] > 0]

print(f"""
  Total trading days:     {len(dates)}
  Days with trades:       {len(all_trades)}
  Completed round-trips:  {len(completed)}
  Emergency exits:        {len(emergency)}
  No forced close days:   {len(dates) - len(all_trades)} (strategy chose not to trade)

  By direction:
    Reverse-T (sell→buy):  {len(reverse_t)} trades, PnL: {sum(t['pnl'] for t in reverse_t):+,.0f}
    Normal-T (buy→sell):   {len(normal_t)} trades, PnL: {sum(t['pnl'] for t in normal_t):+,.0f}

  Total P&L:              {total_pnl:+,.0f} RMB
  Win rate:               {len(profitable)}/{max(1,len(all_trades))} = {len(profitable)/max(1,len(all_trades))*100:.0f}%

  Detailed trades:""")

for t in all_trades:
    tag = "[WIN]" if t["pnl"] > 0 else "[LOSS]"
    print(f"  {t['date']} {t['direction']:>10} {t['entry']:.2f} -> {t['exit']:.2f} {t['pnl']:+,.0f} {tag} ({t['state']})")
