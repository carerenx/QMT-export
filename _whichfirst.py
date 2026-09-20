import pandas as pd, numpy as np
D='analysis/dayt_redis_halfposition_20260101_20260918'
m=pd.read_csv(D+'/1m.csv',dtype={'time':str}).set_index('time').sort_index()
d=m.index.str[:8]; m=m.loc[(d>='20260101')&(d<='20260918')]
cnt={'上带先':0,'下带先':0,'都未触及':0,'同日都触(上先)':0,'同日都触(下先)':0}
rows=[]
for day,b in m.groupby(m.index.str[:8]):
    if len(b)<230: continue
    px=b['close'].astype(float).values; vol=b['volume'].astype(float).values
    amt=b['amount'].astype(float).cumsum().values; pv=vol.cumsum()*100
    vw=amt/np.maximum(pv,1)
    up_i=dn_i=None
    for i in range(20,len(px)):
        sd=px[:i+1].std()
        if sd<=0 or not np.isfinite(vw[i]) or vw[i]<=0: continue
        up=vw[i]+3.0*sd; dn=vw[i]-3.0*sd
        if up_i is None and px[i]>=up: up_i=i
        if dn_i is None and px[i]<=dn: dn_i=i
        if up_i is not None and dn_i is not None: break
    if up_i is None and dn_i is None: k='都未触及'
    elif dn_i is None: k='上带先'
    elif up_i is None: k='下带先'
    else: k='同日都触(上先)' if up_i<dn_i else '同日都触(下先)'
    cnt[k]+=1; rows.append((day,k))
tot=sum(cnt.values())
print('601869 2026-01-01~09-18, 共 %d 个交易日'%tot)
print()
for k,v in sorted(cnt.items(),key=lambda x:-x[1]):
    print('  %-14s %3d 天  (%.0f%%)'%(k,v,100*v/tot))
print()
first_down=cnt['下带先']+cnt['同日都触(下先)']
print('先触发下带的天数(本该做正T): %d 天 (%.0f%%)'%(first_down,100*first_down/tot))
print('先触发上带的天数(现策略做反T): %d 天 (%.0f%%)'%(cnt['上带先']+cnt['同日都触(上先)'],100*(cnt['上带先']+cnt['同日都触(上先)'])/tot))
print('两侧都未触及: %d 天 (%.0f%%) -- 这些天现策略必然空仓'%(cnt['都未触及'],100*cnt['都未触及']/tot))
