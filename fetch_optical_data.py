import sys, time, random, requests, json, os, csv
sys.stdout.reconfigure(encoding='utf-8')

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
DATA_DIR = r'd:\02Project\QMT-export\data\optical_sector'
os.makedirs(DATA_DIR, exist_ok=True)

key_stocks = [
    ('300308', '中际旭创', '光模块龙头'),
    ('300502', '新易盛', '光模块'),
    ('300394', '天孚通信', '光器件'),
    ('002281', '光迅科技', '光模块'),
    ('600487', '亨通光电', '光纤光缆'),
    ('601869', '长飞光纤', '光纤预制棒'),
    ('600522', '中天科技', '光纤光缆+海缆'),
    ('688498', '源杰科技', '光芯片'),
    ('688205', '德科立', '光模块'),
    ('301191', '菲菱科思', '光通信'),
    ('002902', '铭普光磁', '光模块磁性元件'),
    ('688313', '仕佳光子', '光芯片'),
    ('300570', '太辰光', '光器件'),
    ('300548', '博创科技', '光模块'),
    ('603083', '剑桥科技', '光模块'),
]

# ============ STEP 1: Tencent daily K-line ============
print(f'=== STEP 1: Fetching 6-month daily K-lines for {len(key_stocks)} stocks ===')

all_klines = {}
for code, name, desc in key_stocks:
    tc = f'sh{code}' if code.startswith(('6','9')) else f'sz{code}'
    try:
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        params = {'param': f'{tc},day,,,130,qfq'}
        r = requests.get(url, params=params, headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=10)
        d = r.json()
        data = d.get('data', {}).get(tc, {})
        klines = data.get('day', []) or data.get('qfqday', [])
        if klines:
            all_klines[code] = {'name': name, 'desc': desc, 'klines': klines}
            csv_path = os.path.join(DATA_DIR, f'kline_{code}_{name}.csv')
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['date', 'open', 'close', 'high', 'low', 'volume'])
                for k in klines:
                    writer.writerow(k[:6])
            if len(klines) >= 2:
                first_close = float(klines[0][2])
                last_close = float(klines[-1][2])
                chg = (last_close / first_close - 1) * 100
                print(f'  {code} {name}: {len(klines)} bars, {klines[0][0]}->{klines[-1][0]}, chg: {chg:+.1f}%')
            else:
                print(f'  {code} {name}: {len(klines)} bars')
        else:
            print(f'  {code} {name}: NO KLINE DATA')
    except Exception as e:
        print(f'  {code} {name}: KLINE ERROR - {str(e)[:80]}')
    time.sleep(0.3)

# ============ STEP 2: Eastmoney 120-day fund flow ============
print(f'\n=== STEP 2: Fetching 120-day fund flow ===')

EM_SESSION = requests.Session()
EM_SESSION.headers.update({'User-Agent': UA})
_em_last = [0.0]

def em_get(url, params=None, headers=None, timeout=15):
    wait = 1.5 - (time.time() - _em_last[0])
    if wait > 0: time.sleep(wait + random.uniform(0.2, 0.6))
    try: return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout)
    finally: _em_last[0] = time.time()

all_flows = {}
for code, name, desc in key_stocks:
    market_code = 1 if code.startswith('6') else 0
    try:
        url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
        params = {
            'secid': f'{market_code}.{code}',
            'fields1': 'f1,f2,f3,f7',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
            'lmt': '120',
        }
        r = em_get(url, params=params)
        d = r.json()
        klines = d.get('data', {}).get('klines', [])
        if klines:
            rows = []
            for line in klines:
                parts = line.split(',')
                if len(parts) >= 7:
                    rows.append({
                        'date': parts[0],
                        'main_net': float(parts[1]) if parts[1] != '-' else 0,
                        'small_net': float(parts[2]) if parts[2] != '-' else 0,
                        'mid_net': float(parts[3]) if parts[3] != '-' else 0,
                        'large_net': float(parts[4]) if parts[4] != '-' else 0,
                        'super_net': float(parts[5]) if parts[5] != '-' else 0,
                    })
            all_flows[code] = {'name': name, 'desc': desc, 'flows': rows}
            csv_path = os.path.join(DATA_DIR, f'fundflow_{code}_{name}.csv')
            with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['date', 'main_net', 'small_net', 'mid_net', 'large_net', 'super_net'])
                for row in rows:
                    writer.writerow([row['date'], row['main_net'], row['small_net'],
                                   row['mid_net'], row['large_net'], row['super_net']])
            total_main = sum(r['main_net'] for r in rows)
            recent_20 = sum(r['main_net'] for r in rows[-20:])
            recent_5 = sum(r['main_net'] for r in rows[-5:])
            print(f'  {code} {name}: {len(rows)} days, total={total_main/1e8:.2f}yi, rec20={recent_20/1e8:.2f}yi, rec5={recent_5/1e8:.2f}yi')
        else:
            print(f'  {code} {name}: NO FLOW DATA')
            all_flows[code] = {'name': name, 'desc': desc, 'flows': []}
    except Exception as e:
        print(f'  {code} {name}: FLOW ERROR - {str(e)[:80]}')
        all_flows[code] = {'name': name, 'desc': desc, 'flows': []}

# ============ STEP 3: Related concept board snapshots ============
print(f'\n=== STEP 3: Related concept board snapshots ===')
board_codes = [
    ('BK0448', '通信设备'), ('BK1215', '通信'), ('BK1038', '光学光电子'),
    ('BK0459', '元件'), ('BK0736', '通信服务'),
]
board_data = []
for bk_code, bk_name in board_codes:
    try:
        url2 = 'https://push2.eastmoney.com/api/qt/stock/get'
        params2 = {
            'secid': f'90.{bk_code}',
            'fields': 'f43,f44,f45,f46,f57,f58,f60,f116,f117,f169,f170,f171',
        }
        r2 = em_get(url2, params=params2)
        d2 = r2.json()
        dd = d2.get('data', {})
        if dd:
            price = dd.get('f43', 0)
            yest = dd.get('f60', 0)
            chg = (price / yest - 1) * 100 if yest else 0
            board_data.append({'code': bk_code, 'name': bk_name, 'price': price, 'chg_pct': chg, 'mcap': dd.get('f116', 0)})
            print(f'  {bk_code} {bk_name}: idx={price}, chg={chg:+.2f}%, mcap={dd.get("f116",0)/1e8:.0f}yi')
    except Exception as e:
        print(f'  {bk_code} {bk_name}: ERROR - {str(e)[:60]}')

# ============ STEP 4: Compute aggregate statistics ============
print(f'\n=== STEP 4: Computing aggregate statistics ===')
summary = []
for code in all_klines:
    kdata = all_klines[code]
    fdata = all_flows.get(code, {'flows': []})
    klines = kdata['klines']
    flows = fdata['flows']

    if len(klines) >= 2:
        first_c = float(klines[0][2])
        last_c = float(klines[-1][2])
        chg_6m = (last_c / first_c - 1) * 100
        q1_end = min(len(klines)-1, 65)
        q2_start = max(0, len(klines)-65)
        if q1_end > 0 and q2_start < len(klines):
            q_mid = float(klines[q1_end][2])
            chg_q1 = (q_mid / first_c - 1) * 100
            chg_q2 = (last_c / q_mid - 1) * 100
        else:
            chg_q1 = chg_6m
            chg_q2 = 0
        if len(klines) >= 22:
            month_ago = float(klines[-22][2])
            chg_1m = (last_c / month_ago - 1) * 100
        else:
            chg_1m = 0
        highs = [float(k[3]) for k in klines]
        max_h = max(highs)
        max_h_idx = highs.index(max_h)
        lows_after = [float(k[4]) for k in klines[max_h_idx:]]
        max_dd = (min(lows_after) / max_h - 1) * 100 if lows_after else 0
    else:
        chg_6m = chg_1m = chg_q1 = chg_q2 = max_dd = 0

    if flows:
        total_main = sum(r['main_net'] for r in flows) / 1e8
        recent20_main = sum(r['main_net'] for r in flows[-20:]) / 1e8
        recent5_main = sum(r['main_net'] for r in flows[-5:]) / 1e8
        total_super = sum(r['super_net'] for r in flows) / 1e8
        recent20_super = sum(r['super_net'] for r in flows[-20:]) / 1e8
    else:
        total_main = recent20_main = recent5_main = total_super = recent20_super = 0

    summary.append({
        'code': code, 'name': kdata['name'], 'desc': kdata['desc'],
        'chg_6m': chg_6m, 'chg_q1': chg_q1, 'chg_q2': chg_q2,
        'chg_1m': chg_1m, 'max_dd_6m': max_dd,
        'total_main_yi': total_main, 'recent20_main_yi': recent20_main,
        'recent5_main_yi': recent5_main,
        'total_super_yi': total_super, 'recent20_super_yi': recent20_super,
    })

summary.sort(key=lambda x: x['chg_6m'], reverse=True)

# Save summary
summary_path = os.path.join(DATA_DIR, 'summary_stats.csv')
with open(summary_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['code', 'name', 'desc', 'chg_6m_pct', 'chg_q1_pct', 'chg_q2_pct',
                     'chg_1m_pct', 'max_dd_6m_pct', 'total_main_yi', 'recent20_main_yi',
                     'recent5_main_yi', 'total_super_yi', 'recent20_super_yi'])
    for s in summary:
        writer.writerow([s['code'], s['name'], s['desc'],
                        f"{s['chg_6m']:.1f}", f"{s['chg_q1']:.1f}", f"{s['chg_q2']:.1f}",
                        f"{s['chg_1m']:.1f}", f"{s['max_dd_6m']:.1f}",
                        f"{s['total_main_yi']:.2f}", f"{s['recent20_main_yi']:.2f}",
                        f"{s['recent5_main_yi']:.2f}", f"{s['total_super_yi']:.2f}",
                        f"{s['recent20_super_yi']:.2f}"])

# Save board data
board_path = os.path.join(DATA_DIR, 'related_boards.csv')
with open(board_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(['code', 'name', 'price_idx', 'chg_pct', 'mcap'])
    for b in board_data:
        writer.writerow([b['code'], b['name'], b['price'], f"{b['chg_pct']:.2f}", b['mcap']])

print(f'\nSummary saved to: {summary_path}')
print(f'\n=== TOP PERFORMERS (by 6-month return) ===')
for s in summary[:8]:
    print(f"  {s['code']} {s['name']:8s} ({s['desc']:16s}): 6M={s['chg_6m']:+.1f}%  1M={s['chg_1m']:+.1f}%  DD={s['max_dd_6m']:.1f}%  total_main={s['total_main_yi']:+.2f}yi  rec20={s['recent20_main_yi']:+.2f}yi")

print(f'\n=== BOTTOM PERFORMERS ===')
for s in summary[-5:]:
    print(f"  {s['code']} {s['name']:8s} ({s['desc']:16s}): 6M={s['chg_6m']:+.1f}%  1M={s['chg_1m']:+.1f}%  DD={s['max_dd_6m']:.1f}%  total_main={s['total_main_yi']:+.2f}yi  rec20={s['recent20_main_yi']:+.2f}yi")

total_sector_main = sum(s['total_main_yi'] for s in summary)
total_sector_recent20 = sum(s['recent20_main_yi'] for s in summary)
avg_chg = sum(s['chg_6m'] for s in summary) / len(summary) if summary else 0
print(f'\nSECTOR AGGREGATE: avg_6m_chg={avg_chg:+.1f}%, total_120d_main={total_sector_main:+.2f}yi, recent20_main={total_sector_recent20:+.2f}yi')

print(f'\n=== ALL DATA FILES ===')
for f in sorted(os.listdir(DATA_DIR)):
    size = os.path.getsize(os.path.join(DATA_DIR, f))
    print(f'  {f} ({size:,} bytes)')
print(f'\nTotal files: {len(os.listdir(DATA_DIR))}')
