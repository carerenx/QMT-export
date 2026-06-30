import sys, os, csv, time, requests
sys.stdout.reconfigure(encoding='utf-8')
UA = 'Mozilla/5.0'
DATA_DIR = r'd:\02Project\QMT-export\data\similar_screening'

candidates = []
with open(os.path.join(DATA_DIR, 'candidates_scored.csv'), 'r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        row['score'] = int(row['score'])
        candidates.append(row)
candidates.sort(key=lambda x: x['score'], reverse=True)

bad = ['*','?','/',chr(92),':','"','<','>','|']
for c in candidates[:20]:
    code = c['code']
    safe = c['name']
    for ch in bad: safe = safe.replace(ch, '_')
    tc = 'sh' + code if code.startswith(('6','9')) else 'sz' + code
    try:
        url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
        r = requests.get(url, params={'param': tc + ',day,,,60,qfq'},
                        headers={'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}, timeout=8)
        d = r.json()
        raw = d.get('data', {}).get(tc, {}).get('qfqday', []) or d.get('data', {}).get(tc, {}).get('day', [])
        if raw:
            kp = os.path.join(DATA_DIR, 'kline_' + code + '_' + safe + '.csv')
            with open(kp, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f)
                w.writerow(['date','open','close','high','low','volume'])
                for k in raw: w.writerow(k[:6])
        time.sleep(0.15)
    except: pass
print('Done. Saved K-lines for top 20.')
