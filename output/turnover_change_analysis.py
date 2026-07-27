# -*- coding: utf-8 -*-
"""
长飞光纤(601869) 换手率/涨跌幅比值分析
使用东财K线API获取数据，计算 (换手率 / |涨跌幅|) 比值
"""
import time
import random
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import json
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# 0. 配置
# ============================================================
CODE = '601869'
NAME = '长飞光纤'
OUTPUT_DIR = Path(__file__).parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

# 东财限流
EM_SESSION = requests.Session()
EM_SESSION.headers.update({"User-Agent": UA})
_em_last_call = [0.0]

def em_get(url, params=None, headers=None, timeout=15):
    """东财节流请求"""
    wait = 1.0 - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return EM_SESSION.get(url, params=params, headers=headers or {}, timeout=timeout)
    finally:
        _em_last_call[0] = time.time()

# ============================================================
# 1. 获取K线数据 (东财 push2his)
# ============================================================
print(f"[1/4] 获取 {NAME}({CODE}) 日K线数据...")

# 东财K线API - 包含换手率
# secid: 1.601869 (沪市)
all_klines = []
# 每次最多取200条，分2次取覆盖400个交易日(~1.6年)
for page in range(1, 3):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"1.{CODE}",
        "ut": "fa5fd1943c7b386f172d6893dbbd4dcf",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",       # 日线
        "fqt": "1",         # 前复权
        "end": "20500101",
        "lmt": "200",
        "page": str(page),
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
    }
    r = em_get(url, params=params, headers=headers, timeout=15)
    d = r.json()
    klines = d.get("data", {}).get("klines", [])
    if not klines:
        break
    all_klines.extend(klines)
    if len(klines) < 200:
        break

print(f"  获取到 {len(all_klines)} 根日K线")

if len(all_klines) == 0:
    print("ERROR: 未获取到K线数据!")
    exit(1)

# 解析K线数据
# fields2: f51=日期, f52=开盘, f53=收盘, f54=最高, f55=最低,
#           f56=成交量(手), f57=成交额(元), f58=振幅%, f59=涨跌幅%,
#           f60=涨跌额, f61=换手率%
rows = []
for line in all_klines:
    parts = line.split(",")
    if len(parts) >= 11:
        rows.append({
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "vol_shou": float(parts[5]),      # 成交量(手)
            "amount_yuan": float(parts[6]),   # 成交额(元)
            "amplitude_pct": float(parts[7]), # 振幅%
            "change_pct": float(parts[8]),    # 涨跌幅%
            "change_amt": float(parts[9]),    # 涨跌额
            "turnover_pct": float(parts[10]), # 换手率%
        })

df = pd.DataFrame(rows)
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)

# 截取近一年
cutoff = df['date'].max() - timedelta(days=365)
df = df[df['date'] >= cutoff].copy()
print(f"  近一年: {len(df)}个交易日 ({df['date'].min().date()} ~ {df['date'].max().date()})")

# ============================================================
# 2. 计算指标
# ============================================================
print("[2/4] 计算换手率/涨跌幅比值...")

# 比值 = 换手率 / |涨跌幅|
MIN_CHANGE = 0.01  # 最小涨跌幅阈值
df['abs_change'] = df['change_pct'].abs()
df['ratio'] = np.where(
    df['abs_change'] > MIN_CHANGE,
    df['turnover_pct'] / df['abs_change'],
    np.nan
)

# 成交额/|涨跌幅| - 辅助指标（亿元/%）
df['amount_ratio'] = np.where(
    df['abs_change'] > MIN_CHANGE,
    df['amount_yuan'] / 1e8 / df['abs_change'],
    np.nan
)

# 涨跌方向
df['direction'] = np.where(df['change_pct'] > 0, '上涨', '下跌')
df['direction'] = np.where(df['change_pct'] == 0, '平盘', df['direction'])

# 移动平均
df['ratio_ma5'] = df['ratio'].rolling(5).mean()
df['ratio_ma20'] = df['ratio'].rolling(20).mean()
df['turnover_ma5'] = df['turnover_pct'].rolling(5).mean()
df['close_ma20'] = df['close'].rolling(20).mean()

# 股价位置（相对近一年高低点）
year_high = df['close'].max()
year_low = df['close'].min()
df['price_position'] = (df['close'] - year_low) / (year_high - year_low) * 100  # 0~100

valid = df.dropna(subset=['ratio']).copy()
total_days = len(df)
valid_days = len(valid)

# ============================================================
# 3. 统计分析
# ============================================================
print("[3/4] 统计分析...")

up_days = len(valid[valid['direction'] == '上涨'])
down_days = len(valid[valid['direction'] == '下跌'])

ratio_desc = valid['ratio'].describe()
q25 = valid['ratio'].quantile(0.25)
q50 = valid['ratio'].median()
q75 = valid['ratio'].quantile(0.75)
q90 = valid['ratio'].quantile(0.90)
q95 = valid['ratio'].quantile(0.95)

ratio_up = valid[valid['direction'] == '上涨']['ratio']
ratio_down = valid[valid['direction'] == '下跌']['ratio']

# 区间分布
bins = [0, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0, 50.0, float('inf')]
bin_labels = ['0~0.3', '0.3~0.5', '0.5~1', '1~2', '2~3', '3~5', '5~10', '10~20', '20~50', '50+']
valid['ratio_bin'] = pd.cut(valid['ratio'], bins=bins, labels=bin_labels)
ratio_dist = valid['ratio_bin'].value_counts().sort_index()

# 极值（去重）
extreme_high = valid.nlargest(20, 'ratio').drop_duplicates(subset=['date']).head(10)[
    ['date', 'close', 'change_pct', 'turnover_pct', 'ratio', 'vol_shou', 'amplitude_pct']
]
extreme_low = valid.nsmallest(20, 'ratio').drop_duplicates(subset=['date']).head(10)[
    ['date', 'close', 'change_pct', 'turnover_pct', 'ratio', 'vol_shou', 'amplitude_pct']
]

# 相关性
correlation = valid['turnover_pct'].corr(valid['abs_change'])
corr_amt = valid['amount_yuan'].corr(valid['abs_change'])

# 近一月 / 近一季
last_month = valid[valid['date'] >= valid['date'].max() - timedelta(days=22)]
last_quarter = valid[valid['date'] >= valid['date'].max() - timedelta(days=66)]

# 高比值日价格位置分布
high_ratio_days = valid[valid['ratio'] > q75]
low_ratio_days = valid[valid['ratio'] < q25]

# 近期比值趋势
recent_20_ratio = valid['ratio'].tail(20)

print(f"  有效交易日: {valid_days}/{total_days}")
print(f"  比值均值: {valid['ratio'].mean():.3f} 中位数: {q50:.3f}")
print(f"  四分位距: {q25:.3f} ~ {q75:.3f}")
print(f"  换手率-涨跌幅相关系数: {correlation:.3f}")

# ============================================================
# 4. 生成报告
# ============================================================
print("[4/4] 输出报告与图表...")

recent_ratio_str = " → ".join([f"{v:.2f}" for v in recent_20_ratio.tail(5).values])

# 价格区间划分
price_high_zone = year_high * 0.85
price_low_zone = year_low * 1.15
high_pos_mask = valid['close'] >= price_high_zone
low_pos_mask = valid['close'] <= price_low_zone
high_pos_ratio = valid.loc[high_pos_mask, 'ratio'].mean() if high_pos_mask.sum() > 0 else np.nan
low_pos_ratio = valid.loc[low_pos_mask, 'ratio'].mean() if low_pos_mask.sum() > 0 else np.nan

md_report = f"""# {NAME}({CODE}) 换手率/涨跌幅 比值分析报告

> **分析区间**: {df['date'].min().date()} ~ {df['date'].max().date()}（{total_days}个交易日）
> **生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> **数据来源**: 东方财富K线API（前复权）

---

## 一、核心结论

| 指标 | 数值 | 含义 |
|------|------|------|
| 近一年区间 | {df['date'].min().date()} ~ {df['date'].max().date()} | {total_days}个交易日 |
| 年初/年末价 | ¥{df['close'].iloc[0]:.2f} → ¥{df['close'].iloc[-1]:.2f} | 区间涨幅 {((df['close'].iloc[-1]/df['close'].iloc[0])-1)*100:.1f}% |
| 年内最高/最低 | ¥{year_high:.2f} / ¥{year_low:.2f} | 振幅 {((year_high/year_low)-1)*100:.1f}% |
| 平均换手率 | {valid['turnover_pct'].mean():.2f}% | {'高换手特征明显' if valid['turnover_pct'].mean() > 3 else '正常换手水平' if valid['turnover_pct'].mean() > 1 else '低换手/冷门股特征'} |
| 平均\|涨跌幅\| | {valid['abs_change'].mean():.2f}% | {'波动较大' if valid['abs_change'].mean() > 3 else '波动适中' if valid['abs_change'].mean() > 1.5 else '波动较小'} |
| **比值均值** | **{valid['ratio'].mean():.2f}** | 每1%涨跌对应{valid['ratio'].mean():.2f}%换手率 |
| **比值中位数** | **{q50:.2f}** | 50%的交易日比值 ≤ {q50:.2f} |
| 近5日比值 | {recent_ratio_str} | {'↑ 比值上升趋势' if recent_20_ratio.tail(5).mean() > recent_20_ratio.head(15).mean() else '↓ 比值下降趋势' if recent_20_ratio.tail(5).mean() < recent_20_ratio.head(15).mean() else '→ 比值稳定'} |

---

## 二、指标定义

```
比值(R) = 换手率(%) / |涨跌幅(%)|
```

| R值范围 | 量价关系解读 | 市场含义 |
|---------|-------------|---------|
| **R < 0.3** | 极少换手推动大幅涨跌 | 市场共识极强，趋势效率极高。可能是封板/跌停流动性枯竭，或机构控盘拉升 |
| **0.3 ≤ R < 0.5** | 少量换手推动明显涨跌 | 趋势高效，方向明确，多方或空方占绝对优势 |
| **0.5 ≤ R < 2.0** | 量价基本匹配 | 正常交易状态，换手与涨跌幅协调 |
| **2.0 ≤ R < 5.0** | 换手相对涨跌偏大 | 多空分歧加大，筹码交换活跃但方向不明确 |
| **5.0 ≤ R < 10.0** | 大量换手仅推动有限价格变动 | ⚠️ 严重分歧，可能是变盘前夜 |
| **R ≥ 10.0** | 巨量换手而价格几乎不动 | 🔴 极端信号，警惕主力对倒或出货 |

---

## 三、全样本统计

### 3.1 描述性统计

| 统计量 | 全部(N={valid_days}) | 上涨日(N={up_days}) | 下跌日(N={down_days}) |
|--------|---------------------|--------------------|--------------------|
| 均值 | {valid['ratio'].mean():.2f} | {ratio_up.mean():.2f} | {ratio_down.mean():.2f} |
| 标准差 | {valid['ratio'].std():.2f} | {ratio_up.std():.2f} | {ratio_down.std():.2f} |
| 最小值 | {valid['ratio'].min():.2f} | {ratio_up.min():.2f} | {ratio_down.min():.2f} |
| 25%分位 | {q25:.2f} | {ratio_up.quantile(0.25):.2f} | {ratio_down.quantile(0.25):.2f} |
| 中位数 | {q50:.2f} | {ratio_up.median():.2f} | {ratio_down.median():.2f} |
| 75%分位 | {q75:.2f} | {ratio_up.quantile(0.75):.2f} | {ratio_down.quantile(0.75):.2f} |
| 90%分位 | {q90:.2f} | {ratio_up.quantile(0.90):.2f} | {ratio_down.quantile(0.90):.2f} |
| 最大值 | {valid['ratio'].max():.2f} | {ratio_up.max():.2f} | {ratio_down.max():.2f} |
| 偏度 | {valid['ratio'].skew():.2f} | - | - |
| 峰度 | {valid['ratio'].kurtosis():.2f} | - | - |

### 3.2 区间分布

| 比值区间 | 天数 | 占比 | 解读 |
|----------|------|------|------|
"""

bin_meanings = {
    '0~0.3':   '趋势高效，共识极强',
    '0.3~0.5': '趋势偏高效',
    '0.5~1':   '量价匹配，略偏趋势',
    '1~2':     '量价基本协调',
    '2~3':     '换手略偏大，分歧初现',
    '3~5':     '多空分歧明显',
    '5~10':    '分歧较大，关注变盘',
    '10~20':   '严重分歧，警惕',
    '20~50':   '极度分歧，危险信号',
    '50+':     '异常信号',
}

for bin_name in bin_labels:
    count = ratio_dist.get(bin_name, 0)
    pct = count / valid_days * 100
    bar = '█' * max(1, int(pct * 2))
    md_report += f"| {bin_name} | {count} | {pct:.1f}% | {bar} | {bin_meanings.get(bin_name, '')} |\n"

md_report += f"""
### 3.3 关键统计

- **75%的交易日**比值在 **{q25:.2f} ~ {q75:.2f}** 范围内
- **90%的交易日**比值 < **{q90:.2f}**
- **正常区间(0.5~2.0)占比**: {ratio_dist[['0.5~1','1~2']].sum()/valid_days*100:.1f}%
- **趋势高效区(<0.5)占比**: {ratio_dist[['0~0.3','0.3~0.5']].sum()/valid_days*100:.1f}%
- **分歧预警区(>5)占比**: {ratio_dist[['5~10','10~20','20~50','50+']].sum()/valid_days*100:.1f}%

---

## 四、量价相关性分析

| 指标 | 数值 | 说明 |
|------|------|------|
| 换手率 vs \|涨跌幅\| 相关系数 | {correlation:.3f} | {'正相关，量价同步良好' if correlation > 0.4 else '弱正相关' if correlation > 0.15 else '几乎不相关，量价脱节'} |
| 成交额 vs \|涨跌幅\| 相关系数 | {corr_amt:.3f} | {'成交额与价格变动同步' if corr_amt > 0.4 else '成交额与价格变动弱相关'} |

### 不同价格位置的比值特征

| 价格位置 | 平均比值 | 含义 |
|----------|---------|------|
| 高位区（>¥{price_high_zone:.0f}） | {high_pos_ratio:.2f} | {'高位换手偏大，警惕出货' if high_pos_ratio > q50 else '高位换手控制良好'} |
| 低位区（<¥{price_low_zone:.0f}） | {low_pos_ratio:.2f} | {'低位换手偏大，可能吸筹' if low_pos_ratio > q50 else '低位人气不足'} |

---

## 五、极端比值日分析

### 5.1 TOP 10 最高比值（换手率远超涨跌幅）

> 这些日期大量换手但价格变动微小，显示多空激烈博弈

| 日期 | 收盘价 | 涨跌幅% | 换手率% | **比值** | 成交量(手) | 振幅% |
|------|--------|---------|---------|----------|-----------|-------|
"""

for _, row in extreme_high.iterrows():
    flag = '🔴' if row['ratio'] > q90 else '🟡' if row['ratio'] > q75 else ''
    md_report += f"| {row['date'].date()} | {row['close']:.2f} | {row['change_pct']:+.2f} | {row['turnover_pct']:.2f} | **{row['ratio']:.2f}**{flag} | {row['vol_shou']:.0f} | {row['amplitude_pct']:.2f} |\n"

md_report += f"""
### 5.2 TOP 10 最低比值（涨跌幅远超换手率）

> 这些日期极少量换手即推动大幅涨跌，市场共识极强

| 日期 | 收盘价 | 涨跌幅% | 换手率% | **比值** | 成交量(手) | 振幅% |
|------|--------|---------|---------|----------|-----------|-------|
"""

for _, row in extreme_low.iterrows():
    md_report += f"| {row['date'].date()} | {row['close']:.2f} | {row['change_pct']:+.2f} | {row['turnover_pct']:.2f} | **{row['ratio']:.2f}** | {row['vol_shou']:.0f} | {row['amplitude_pct']:.2f} |\n"

# 近期
md_report += f"""
---

## 六、近期信号（近一月）

| 指标 | 数值 |
|------|------|
| 近一月比值均值 | {last_month['ratio'].mean():.2f} |
| 近一月比值中位数 | {last_month['ratio'].median():.2f} |
| 近一月平均换手率 | {last_month['turnover_pct'].mean():.2f}% |
| 近一月平均\|涨跌幅\| | {last_month['abs_change'].mean():.2f}% |
| 近一月涨/跌天数 | {len(last_month[last_month['direction']=='上涨'])} / {len(last_month[last_month['direction']=='下跌'])} |
| 近一月异常高比值日(>Q75) | {len(last_month[last_month['ratio']>q75])} 天 |
| 当前比值趋势 | {'比值上升→分歧加大' if recent_20_ratio.tail(5).mean() > recent_20_ratio.head(15).mean() * 1.2 else '比值下降→共识增强' if recent_20_ratio.tail(5).mean() < recent_20_ratio.head(15).mean() * 0.8 else '比值稳定'} |

---

## 七、策略参考

### 7.1 极端比值信号的历史胜率

| 信号条件 | 出现次数 | 5日后同向概率 | 说明 |
|----------|---------|-------------|------|
| R > {q90:.1f}（极端高比值） | {len(high_ratio_days)} | - | 大量换手不涨/不跌，关注变盘方向 |
| R < {q25:.2f}（极端低比值） | {len(low_ratio_days)} | - | 趋势极强，顺势操作效率最高 |

### 7.2 日内做T参考

- 当 **R > {q75:.1f}**（前25%高比值）：市场分歧大，做T应**缩小价差目标**，快进快出
- 当 **R < {q25:.2f}**（前25%低比值）：趋势明确，做T应**顺势而为**，不要逆势接飞刀
- 当 R值处于正常区间({q25:.2f}~{q75:.2f})：量价协调，按常规策略参数执行

### 7.3 变盘预警

比值连续3日 > {q75:.1f} 且股价接近阶段高点 → ⚠️ **高位滞涨预警**，减少做多仓位
比值连续3日 > {q75:.1f} 且股价接近阶段低点 → 🔍 **底部放量关注**，可能筑底

---

## 八、数据附录

- 原始数据: `{CODE}_ratio_data.csv`
- 走势图: `{CODE}_换手率涨跌幅比值走势图.png`
- 数据来源: 东方财富 push2his K线 API（前复权）
"""

# 写MD
md_path = OUTPUT_DIR / f'{CODE}_换手率涨跌幅比值分析.md'
md_path.write_text(md_report, encoding='utf-8')
print(f"  Markdown: {md_path}")

# 写CSV
csv_path = OUTPUT_DIR / f'{CODE}_ratio_data.csv'
export_cols = ['date', 'open', 'close', 'high', 'low', 'change_pct', 'turnover_pct',
               'amplitude_pct', 'ratio', 'vol_shou', 'amount_yuan', 'direction', 'price_position']
valid[export_cols].to_csv(csv_path, index=False, encoding='utf-8-sig')
print(f"  CSV: {csv_path}")

# ============================================================
# 5. 绘制走势图
# ============================================================
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'Noto Sans SC', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

COLOR_UP = '#E74C3C'
COLOR_DOWN = '#2ECC71'
COLOR_RATIO = '#3498DB'
COLOR_TURNOVER = '#E67E22'
COLOR_CHANGE = '#9B59B6'
COLOR_AMOUNT = '#1ABC9C'
BG_COLOR = '#F8F9FA'

fig = plt.figure(figsize=(20, 18))
gs = fig.add_gridspec(5, 1, height_ratios=[1.5, 1.2, 1.2, 1.2, 1], hspace=0.4,
                       left=0.07, right=0.97, top=0.95, bottom=0.04)
fig.suptitle(f'{NAME}({CODE})  换手率 / 涨跌幅绝对值 比值分析  ({df["date"].min().date()} ~ {df["date"].max().date()})',
             fontsize=17, fontweight='bold', y=0.98)

x = range(len(df))
tick_n = max(1, len(df) // 12)
tick_pos = list(range(0, len(df), tick_n))
tick_labels = [df['date'].iloc[i].strftime('%Y-%m') for i in tick_pos]

# ── 子图1: 收盘价 + K线 ──
ax1 = fig.add_subplot(gs[0])
colors_k = [COLOR_UP if c >= o else COLOR_DOWN for c, o in zip(df['close'], df['open'])]
ax1.bar(x, df['close'] - df['open'], bottom=df['open'], color=colors_k,
        width=0.8, alpha=0.7, edgecolor='none')
ax1.plot(x, df['close'], color='#2C3E50', linewidth=1.5, label='收盘价', zorder=5)
ax1.plot(x, df['close_ma20'], color=COLOR_RATIO, linewidth=1.8, linestyle='--', alpha=0.85, label='MA20')
# 区间高/低
ax1.axhline(y=year_high, color=COLOR_UP, linewidth=0.8, linestyle=':', alpha=0.5)
ax1.axhline(y=year_low, color=COLOR_DOWN, linewidth=0.8, linestyle=':', alpha=0.5)
ax1.text(len(df)-1, year_high, f' 高 ¥{year_high:.0f}', fontsize=8, color=COLOR_UP, va='bottom')
ax1.text(len(df)-1, year_low, f' 低 ¥{year_low:.0f}', fontsize=8, color=COLOR_DOWN, va='top')

ax1.set_ylabel('Price (¥)', fontsize=11, fontweight='bold')
ax1.legend(loc='upper left', fontsize=9, ncol=3, framealpha=0.9)
ax1.grid(True, alpha=0.2, linestyle='--')
ax1.set_facecolor(BG_COLOR)
ax1.set_xticks(tick_pos)
ax1.set_xticklabels([])
# 最新价标注
lp = df['close'].iloc[-1]
ax1.annotate(f'¥{lp:.2f}', xy=(len(df)-1, lp), xytext=(len(df)-8, lp + (year_high-year_low)*0.06),
             fontsize=10, fontweight='bold', color='#2C3E50', ha='right',
             arrowprops=dict(arrowstyle='->', color='#555', lw=1.2))

# 高比值日标记
for i in x:
    if i < len(valid) and valid['ratio'].iloc[i] > q90:
        ax1.plot(i, df['close'].iloc[i], 'o', color=COLOR_UP, markersize=4, alpha=0.6, markeredgewidth=0)

# ── 子图2: 换手率 + |涨跌幅| 双轴 ──
ax2 = fig.add_subplot(gs[1])
ax2.bar(x, df['turnover_pct'], width=0.8, color=[f'{COLOR_UP}55' if c > 0 else f'{COLOR_DOWN}55' for c in df['change_pct']],
        alpha=0.6, label='换手率%')
ax2.plot(x, df['turnover_ma5'], color=COLOR_TURNOVER, linewidth=1.3, alpha=0.9, label='换手率 MA5')
ax2.set_ylabel('换手率 %', fontsize=10, fontweight='bold', color=COLOR_TURNOVER)
ax2.tick_params(axis='y', labelcolor=COLOR_TURNOVER)

ax2b = ax2.twinx()
ax2b.plot(x, df['abs_change'], color=COLOR_CHANGE, linewidth=1.0, marker='.', markersize=1.5, alpha=0.7, label='|涨跌幅|%')
ax2b.set_ylabel('|涨跌幅| %', fontsize=10, fontweight='bold', color=COLOR_CHANGE)
ax2b.tick_params(axis='y', labelcolor=COLOR_CHANGE)

lines1, labels1 = ax2.get_legend_handles_labels()
lines2, labels2 = ax2b.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=8, framealpha=0.9)
ax2.grid(True, alpha=0.2, linestyle='--')
ax2.set_facecolor(BG_COLOR)
ax2.set_xticks(tick_pos)
ax2.set_xticklabels([])

# ── 子图3: 比值 R ──
ax3 = fig.add_subplot(gs[2])
# 填充区域
ax3.fill_between(x, 0, q25, alpha=0.12, color=COLOR_DOWN, label=f'低比值 <{q25:.1f}')
ax3.fill_between(x, q25, q75, alpha=0.08, color='#95A5A6', label=f'正常 {q25:.1f}~{q75:.1f}')
ax3.fill_between(x, q75, max(valid['ratio'].max(), q90*1.5), alpha=0.12, color=COLOR_UP, label=f'高比值 >{q75:.1f}')

# 比值曲线
ax3.plot(x, df['ratio'], color='#BDC3C7', linewidth=0.4, alpha=0.4)
for i in range(len(valid) - 1):
    r_val = valid['ratio'].iloc[i]
    if pd.notna(r_val):
        c = COLOR_UP if r_val > q75 else COLOR_DOWN if r_val < q25 else COLOR_RATIO
        ax3.plot([i, i+1], [valid['ratio'].iloc[i], valid['ratio'].iloc[i+1]], color=c, linewidth=0.7, alpha=0.7)
ax3.plot(x, df['ratio_ma20'], color='#2C3E50', linewidth=1.5, linestyle='-', alpha=0.8, label='R MA20')

ax3.axhline(y=q50, color='#555', linewidth=0.8, linestyle='--', alpha=0.5, label=f'中位数 {q50:.2f}')
ax3.axhline(y=q90, color=COLOR_UP, linewidth=0.6, linestyle=':', alpha=0.5, label=f'90分位 {q90:.1f}')

ax3.set_ylabel('Ratio (Turnover/|Change|)', fontsize=10, fontweight='bold')
ax3.legend(loc='upper left', fontsize=7, ncol=4, framealpha=0.9)
ax3.grid(True, alpha=0.2, linestyle='--')
ax3.set_facecolor(BG_COLOR)
ax3.set_xticks(tick_pos)
ax3.set_xticklabels([])
y_upper = min(valid['ratio'].max() * 1.05, q95 * 4)
ax3.set_ylim(-0.05 * y_upper, y_upper)

# ── 子图4: 成交额/|涨跌幅| ──
ax4 = fig.add_subplot(gs[3])
ax4.fill_between(x, 0, df['amount_ratio'], color=COLOR_AMOUNT, alpha=0.3)
ax4.plot(x, df['amount_ratio'].rolling(10).mean(), color=COLOR_AMOUNT, linewidth=1.3, alpha=0.9, label='成交额/|涨跌幅| MA10 (亿/%)')
ax4.set_ylabel('Amount/|Change| (亿/%)', fontsize=10, fontweight='bold', color=COLOR_AMOUNT)
ax4.tick_params(axis='y', labelcolor=COLOR_AMOUNT)
ax4.legend(loc='upper left', fontsize=8, framealpha=0.9)
ax4.grid(True, alpha=0.2, linestyle='--')
ax4.set_facecolor(BG_COLOR)
ax4.set_xticks(tick_pos)
ax4.set_xticklabels([])

# ── 子图5: 比值分布直方图 ──
ax5 = fig.add_subplot(gs[4])
hist_data = valid['ratio'].clip(upper=q95 * 1.5)
n_data, bins_data, patches = ax5.hist(hist_data, bins=80, alpha=0.7, edgecolor='white', linewidth=0.3)

for i, patch in enumerate(patches):
    bc = (bins_data[i] + bins_data[i+1]) / 2
    if bc < q25:
        patch.set_facecolor(COLOR_DOWN); patch.set_alpha(0.45)
    elif bc > q75:
        patch.set_facecolor(COLOR_UP); patch.set_alpha(0.45)
    else:
        patch.set_facecolor(COLOR_RATIO); patch.set_alpha(0.45)

ax5.axvline(x=q25, color=COLOR_DOWN, linewidth=1.5, linestyle='--', label=f'Q25={q25:.2f}')
ax5.axvline(x=q50, color='#2C3E50', linewidth=2, linestyle='-', label=f'Median={q50:.2f}')
ax5.axvline(x=q75, color=COLOR_UP, linewidth=1.5, linestyle='--', label=f'Q75={q75:.2f}')
ax5.axvline(x=valid['ratio'].mean(), color='#F39C12', linewidth=1.2, linestyle=':', label=f'Mean={valid["ratio"].mean():.2f}')

ax5.set_xlabel('Ratio (Turnover% / |Change%|)', fontsize=11, fontweight='bold')
ax5.set_ylabel('Days', fontsize=11, fontweight='bold')
ax5.legend(loc='upper right', fontsize=9, framealpha=0.9)
ax5.grid(True, alpha=0.2, linestyle='--', axis='y')
ax5.set_facecolor(BG_COLOR)
ax5.set_xticks(tick_pos)
ax5.set_xticklabels(tick_labels, rotation=30, ha='right', fontsize=8)

stats_text = (f'N={valid_days} | Mean={valid["ratio"].mean():.2f} | Std={valid["ratio"].std():.2f}\n'
              f'Skew={valid["ratio"].skew():.2f} | Kurt={valid["ratio"].kurtosis():.2f} | '
              f'Q25~Q75: {q25:.2f}~{q75:.2f}')
ax5.text(0.98, 0.94, stats_text, transform=ax5.transAxes, fontsize=8.5,
         va='top', ha='right', bbox=dict(boxstyle='round', facecolor='white', alpha=0.85, edgecolor='#ddd'))

# 保存
chart_path = OUTPUT_DIR / f'{CODE}_换手率涨跌幅比值走势图.png'
fig.savefig(chart_path, dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
plt.close(fig)
print(f"  走势图: {chart_path}")

print("\n===== 完成 =====")
