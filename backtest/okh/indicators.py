# coding: utf-8
"""
MyTT — 技术指标库 (Technical Indicator Library)
=================================================
来源: https://github.com/mpquant/MyTT (OSkhQuant 内置版本)
适配: 移除 math 依赖，纯 numpy/pandas 实现

包含:
  0级 — 核心工具函数: MA, EMA, HHV, LLV, REF, STD, SUM, CROSS 等
  1级 — 应用层函数: COUNT, EVERY, EXIST, BARSLAST 等
  2级 — 技术指标: MACD, KDJ, RSI, BOLL, ATR, DMI, CCI 等
"""
import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════
# 0级：核心工具函数
# ═══════════════════════════════════════════════════════════════

def RD(N, D=3):
    """四舍五入取D位小数"""
    return np.round(N, D)


def RET(S, N=1):
    """返回序列倒数第N个值（默认最后一个）"""
    return np.array(S)[-N]


def ABS(S):
    """绝对值"""
    return np.abs(S)


def LN(S):
    """自然对数"""
    return np.log(S)


def POW(S, N):
    """S的N次方"""
    return np.power(S, N)


def SQRT(S):
    """平方根"""
    return np.sqrt(S)


def SIN(S):
    """正弦（弧度）"""
    return np.sin(S)


def COS(S):
    """余弦（弧度）"""
    return np.cos(S)


def TAN(S):
    """正切（弧度）"""
    return np.tan(S)


def MAX(S1, S2):
    """序列最大值"""
    return np.maximum(S1, S2)


def MIN(S1, S2):
    """序列最小值"""
    return np.minimum(S1, S2)


def IF(S, A, B):
    """布尔判断（S为真返回A，否则B）"""
    return np.where(S, A, B)


def REF(S, N=1):
    """序列后移N位（获取历史值，如REF(CLOSE,1)为昨收价）"""
    return pd.Series(S).shift(N).values


def DIFF(S, N=1):
    """序列差分（前值-后值，如DIFF(CLOSE)为当日涨跌额）"""
    return pd.Series(S).diff(N).values


def STD(S, N):
    """N日标准差（如计算波动率）"""
    return pd.Series(S).rolling(N).std(ddof=0).values


def SUM(S, N):
    """N日累计和（N=0为累加，如计算总成交量）"""
    return pd.Series(S).rolling(N).sum().values if N > 0 else pd.Series(S).cumsum().values


def CONST(S):
    """序列末尾值扩展为等长常量（如固定基准值）"""
    return np.full(len(S), S[-1])


def HHV(S, N):
    """N日最高价（支持固定周期或动态周期序列）"""
    if isinstance(N, (int, float)):
        return pd.Series(S).rolling(N).max().values
    else:
        res = np.repeat(np.nan, len(S))
        for i in range(len(S)):
            if (not np.isnan(N[i])) and N[i] <= i + 1:
                res[i] = S[i + 1 - int(N[i]):i + 1].max()
        return res


def LLV(S, N):
    """N日最低价（支持固定周期或动态周期序列）"""
    if isinstance(N, (int, float)):
        return pd.Series(S).rolling(N).min().values
    else:
        res = np.repeat(np.nan, len(S))
        for i in range(len(S)):
            if (not np.isnan(N[i])) and N[i] <= i + 1:
                res[i] = S[i + 1 - int(N[i]):i + 1].min()
        return res


def HHVBARS(S, N):
    """N日内最高价到当前的周期数"""
    return pd.Series(S).rolling(N).apply(lambda x: np.argmax(x[::-1]), raw=True).values


def LLVBARS(S, N):
    """N日内最低价到当前的周期数"""
    return pd.Series(S).rolling(N).apply(lambda x: np.argmin(x[::-1]), raw=True).values


def MA(S, N):
    """N日简单移动平均"""
    return pd.Series(S).rolling(N).mean().values


def EMA(S, N):
    """指数移动平均"""
    return pd.Series(S).ewm(span=N, adjust=False).mean().values


def SMA(S, N, M=1):
    """中国式SMA（如KDJ中的平滑计算）"""
    return pd.Series(S).ewm(alpha=M / N, adjust=False).mean().values


def WMA(S, N):
    """加权移动平均（按时间加权，近期权重更高）"""
    return pd.Series(S).rolling(N).apply(
        lambda x: x[::-1].cumsum().sum() * 2 / N / (N + 1), raw=True).values


def DMA(S, A):
    """动态移动平均（A为平滑因子，支持序列输入）"""
    if isinstance(A, (int, float)):
        return pd.Series(S).ewm(alpha=A, adjust=False).mean().values
    A = np.array(A)
    A[np.isnan(A)] = 1.0
    Y = np.zeros(len(S))
    Y[0] = S[0]
    for i in range(1, len(S)):
        Y[i] = A[i] * S[i] + (1 - A[i]) * Y[i - 1]
    return Y


def AVEDEV(S, N):
    """平均绝对偏差（如CCI指标中的平均偏差计算）"""
    return pd.Series(S).rolling(N).apply(lambda x: (np.abs(x - x.mean())).mean()).values


def SLOPE(S, N):
    """线性回归斜率（如趋势线斜率）"""
    return pd.Series(S).rolling(N).apply(
        lambda x: np.polyfit(range(N), x, deg=1)[0], raw=True).values


def FORCAST(S, N):
    """线性回归预测值"""
    return pd.Series(S).rolling(N).apply(
        lambda x: np.polyval(np.polyfit(range(N), x, deg=1), N - 1), raw=True).values


def LAST(S, A, B):
    """A到B日前持续满足条件"""
    return np.array(
        pd.Series(S).rolling(A + 1).apply(lambda x: np.all(x[::-1][B:]), raw=True),
        dtype=bool)


# ═══════════════════════════════════════════════════════════════
# 1级：应用层函数
# ═══════════════════════════════════════════════════════════════

def COUNT(S, N):
    """N日内满足条件的天数"""
    return SUM(S, N)


def EVERY(S, N):
    """N日内全部满足条件"""
    return IF(SUM(S, N) == N, True, False)


def EXIST(S, N):
    """N日内存在满足条件"""
    return IF(SUM(S, N) > 0, True, False)


def FILTER(S, N):
    """条件成立后屏蔽后续N周期"""
    for i in range(len(S)):
        if S[i]:
            S[i + 1:i + 1 + N] = 0
    return S


def BARSLAST(S):
    """上一次条件成立到当前的周期数"""
    M = np.concatenate(([0], np.where(S, 1, 0)))
    for i in range(1, len(M)):
        M[i] = 0 if M[i] else M[i - 1] + 1
    return M[1:]


def BARSLASTCOUNT(S):
    """连续满足条件的周期数"""
    rt = np.zeros(len(S) + 1)
    for i in range(len(S)):
        rt[i + 1] = rt[i] + 1 if S[i] else rt[i + 1]
    return rt[1:]


def BARSSINCEN(S, N):
    """N周期内首次满足条件到现在的周期数"""
    return pd.Series(S).rolling(N).apply(
        lambda x: N - 1 - np.argmax(x) if np.argmax(x) or x[0] else 0,
        raw=True).fillna(0).values.astype(int)


def CROSS(S1, S2):
    """向上金叉（S1上穿S2）"""
    return np.concatenate(([False], np.logical_not((S1 > S2)[:-1]) & (S1 > S2)[1:]))


def LONGCROSS(S1, S2, N):
    """持续N周期后交叉"""
    return np.array(np.logical_and(LAST(S1 < S2, N, 1), (S1 > S2)), dtype=bool)


def VALUEWHEN(S, X):
    """条件成立时记录X值"""
    return pd.Series(np.where(S, X, np.nan)).ffill().values


def BETWEEN(S, A, B):
    """S在A和B之间"""
    return ((A < S) & (S < B)) | ((A > S) & (S > B))


def TOPRANGE(S):
    """当前值为近多少周期内的最大值"""
    rt = np.zeros(len(S))
    for i in range(1, len(S)):
        rt[i] = np.argmin(np.flipud(S[:i] < S[i]))
    return rt.astype('int')


def LOWRANGE(S):
    """当前值为近多少周期内的最小值"""
    rt = np.zeros(len(S))
    for i in range(1, len(S)):
        rt[i] = np.argmin(np.flipud(S[:i] > S[i]))
    return rt.astype('int')


# ═══════════════════════════════════════════════════════════════
# 2级：技术指标函数
# ═══════════════════════════════════════════════════════════════

def MACD(CLOSE, SHORT=12, LONG=26, M=9):
    """MACD指标: DIF, DEA, MACD柱"""
    DIF = EMA(CLOSE, SHORT) - EMA(CLOSE, LONG)
    DEA = EMA(DIF, M)
    MACD_bar = (DIF - DEA) * 2
    return RD(DIF), RD(DEA), RD(MACD_bar)


def KDJ(CLOSE, HIGH, LOW, N=9, M1=3, M2=3):
    """KDJ指标: K, D, J"""
    RSV = (CLOSE - LLV(LOW, N)) / (HHV(HIGH, N) - LLV(LOW, N)) * 100
    K = EMA(RSV, (M1 * 2 - 1))
    D = EMA(K, (M2 * 2 - 1))
    J = K * 3 - D * 2
    return K, D, J


def RSI(CLOSE, N=24):
    """RSI指标"""
    DIF = CLOSE - REF(CLOSE, 1)
    return RD(SMA(MAX(DIF, 0), N) / SMA(ABS(DIF), N) * 100)


def WR(CLOSE, HIGH, LOW, N=10, N1=6):
    """威廉指标: WR(N), WR(N1)"""
    WR1 = (HHV(HIGH, N) - CLOSE) / (HHV(HIGH, N) - LLV(LOW, N)) * 100
    WR2 = (HHV(HIGH, N1) - CLOSE) / (HHV(HIGH, N1) - LLV(LOW, N1)) * 100
    return RD(WR1), RD(WR2)


def BIAS(CLOSE, L1=6, L2=12, L3=24):
    """乖离率: BIAS1, BIAS2, BIAS3"""
    B1 = (CLOSE - MA(CLOSE, L1)) / MA(CLOSE, L1) * 100
    B2 = (CLOSE - MA(CLOSE, L2)) / MA(CLOSE, L2) * 100
    B3 = (CLOSE - MA(CLOSE, L3)) / MA(CLOSE, L3) * 100
    return RD(B1), RD(B2), RD(B3)


def BOLL(CLOSE, N=20, P=2):
    """布林带: UPPER, MID, LOWER"""
    MID = MA(CLOSE, N)
    UPPER = MID + STD(CLOSE, N) * P
    LOWER = MID - STD(CLOSE, N) * P
    return RD(UPPER), RD(MID), RD(LOWER)


def PSY(CLOSE, N=12, M=6):
    """心理线: PSY, PSYMA"""
    PSY_val = COUNT(CLOSE > REF(CLOSE, 1), N) / N * 100
    PSYMA_val = MA(PSY_val, M)
    return RD(PSY_val), RD(PSYMA_val)


def CCI(CLOSE, HIGH, LOW, N=14):
    """商品通道指数"""
    TP = (HIGH + LOW + CLOSE) / 3
    return (TP - MA(TP, N)) / (0.015 * AVEDEV(TP, N))


def ATR(CLOSE, HIGH, LOW, N=20):
    """平均真实波幅"""
    TR = MAX(MAX((HIGH - LOW), ABS(REF(CLOSE, 1) - HIGH)), ABS(REF(CLOSE, 1) - LOW))
    return MA(TR, N)


def BBI(CLOSE, M1=3, M2=6, M3=12, M4=20):
    """多空指数（BBI）"""
    return (MA(CLOSE, M1) + MA(CLOSE, M2) + MA(CLOSE, M3) + MA(CLOSE, M4)) / 4


def DMI(CLOSE, HIGH, LOW, M1=14, M2=6):
    """趋向指标: PDI, MDI, ADX, ADXR"""
    TR = SUM(MAX(MAX(HIGH - LOW, ABS(HIGH - REF(CLOSE, 1))),
                 ABS(LOW - REF(CLOSE, 1))), M1)
    HD = HIGH - REF(HIGH, 1)
    LD = REF(LOW, 1) - LOW
    DMP = SUM(IF((HD > 0) & (HD > LD), HD, 0), M1)
    DMM = SUM(IF((LD > 0) & (LD > HD), LD, 0), M1)
    PDI = DMP * 100 / TR
    MDI = DMM * 100 / TR
    ADX = MA(ABS(MDI - PDI) / (PDI + MDI) * 100, M2)
    ADXR = (ADX + REF(ADX, M2)) / 2
    return PDI, MDI, ADX, ADXR


def TAQ(HIGH, LOW, N):
    """三重平均线: UP, MID, DOWN"""
    UP = HHV(HIGH, N)
    DOWN = LLV(LOW, N)
    MID = (UP + DOWN) / 2
    return UP, MID, DOWN


def KTN(CLOSE, HIGH, LOW, N=20, M=10):
    """肯特纳通道: UPPER, MID, LOWER"""
    MID = EMA((HIGH + LOW + CLOSE) / 3, N)
    ATRN = ATR(CLOSE, HIGH, LOW, M)
    UPPER = MID + 2 * ATRN
    LOWER = MID - 2 * ATRN
    return UPPER, MID, LOWER


def TRIX(CLOSE, M1=12, M2=20):
    """三重指数平滑: TRIX, TRMA"""
    TR = EMA(EMA(EMA(CLOSE, M1), M1), M1)
    TRIX_val = (TR - REF(TR, 1)) / REF(TR, 1) * 100
    TRMA_val = MA(TRIX_val, M2)
    return TRIX_val, TRMA_val


def VR(CLOSE, VOL, M1=26):
    """VR容量比率"""
    LC = REF(CLOSE, 1)
    return SUM(IF(CLOSE > LC, VOL, 0), M1) / SUM(IF(CLOSE <= LC, VOL, 0), M1) * 100


def CR(CLOSE, HIGH, LOW, N=20):
    """CR价格动量指标"""
    MID = REF(HIGH + LOW + CLOSE, 1) / 3
    return SUM(MAX(0, HIGH - MID), N) / SUM(MAX(0, MID - LOW), N) * 100


def EMV(HIGH, LOW, VOL, N=14, M=9):
    """简易波动指标: EMV, MAEMV"""
    VOLUME = MA(VOL, N) / VOL
    MID = 100 * (HIGH + LOW - REF(HIGH + LOW, 1)) / (HIGH + LOW)
    EMV_val = MA(MID * VOLUME * (HIGH - LOW) / MA(HIGH - LOW, N), N)
    MAEMV_val = MA(EMV_val, M)
    return EMV_val, MAEMV_val


def DPO(CLOSE, M1=20, M2=10, M3=6):
    """区间震荡线: DPO, MADPO"""
    DPO_val = CLOSE - REF(MA(CLOSE, M1), M2)
    MADPO_val = MA(DPO_val, M3)
    return DPO_val, MADPO_val


def BRAR(OPEN, CLOSE, HIGH, LOW, M1=26):
    """BRAR情绪指标: AR, BR"""
    AR = SUM(HIGH - OPEN, M1) / SUM(OPEN - LOW, M1) * 100
    BR = SUM(MAX(0, HIGH - REF(CLOSE, 1)), M1) / \
         SUM(MAX(0, REF(CLOSE, 1) - LOW), M1) * 100
    return AR, BR


def DFMA(CLOSE, N1=10, N2=50, M=10):
    """平行线差指标: DIF, DIFMA"""
    DIF = MA(CLOSE, N1) - MA(CLOSE, N2)
    DIFMA_val = MA(DIF, M)
    return DIF, DIFMA_val


def MTM(CLOSE, N=12, M=6):
    """动量指标: MTM, MTMMA"""
    MTM_val = CLOSE - REF(CLOSE, N)
    MTMMA_val = MA(MTM_val, M)
    return MTM_val, MTMMA_val


def MASS(HIGH, LOW, N1=9, N2=25, M=6):
    """梅斯线: MASS, MA_MASS"""
    HIGH_LOW = HIGH - LOW
    MA_HL = MA(HIGH_LOW, N1)
    MA_MA_HL = MA(MA_HL, N1)
    MASS_val = SUM(MA_HL / MA_MA_HL, N2)
    MA_MASS_val = MA(MASS_val, M)
    return MASS_val, MA_MASS_val


def ROC(CLOSE, N=12, M=6):
    """变动率指标: ROC, MAROC"""
    ROC_val = 100 * (CLOSE - REF(CLOSE, N)) / REF(CLOSE, N)
    MAROC_val = MA(ROC_val, M)
    return ROC_val, MAROC_val


def EXPMA(CLOSE, N1=12, N2=50):
    """双指数移动平均"""
    return EMA(CLOSE, N1), EMA(CLOSE, N2)


def OBV(CLOSE, VOL):
    """能量潮指标"""
    return SUM(IF(CLOSE > REF(CLOSE, 1), VOL,
                  IF(CLOSE < REF(CLOSE, 1), -VOL, 0)), 0) / 10000


def MFI(CLOSE, HIGH, LOW, VOL, N=14):
    """资金流量指标（成交量的RSI）"""
    TYP = (HIGH + LOW + CLOSE) / 3
    V1 = SUM(IF(TYP > REF(TYP, 1), TYP * VOL, 0), N) / \
         SUM(IF(TYP < REF(TYP, 1), TYP * VOL, 0), N)
    return 100 - (100 / (1 + V1))


def ASI(OPEN, CLOSE, HIGH, LOW, M1=26, M2=10):
    """振动升降指标: ASI, ASIT"""
    LC = REF(CLOSE, 1)
    AA = ABS(HIGH - LC)
    BB = ABS(LOW - LC)
    CC = ABS(HIGH - REF(LOW, 1))
    DD = ABS(LC - REF(OPEN, 1))

    R = IF((AA > BB) & (AA > CC), AA + BB / 2 + DD / 4,
           IF((BB > CC) & (BB > AA), BB + AA / 2 + DD / 4, CC + DD / 4))

    X = (CLOSE - LC) + (CLOSE - OPEN) / 2 + (LC - REF(OPEN, 1))
    SI = 16 * X / R * MAX(AA, BB)
    ASI_val = SUM(SI, M1)
    ASIT_val = MA(ASI_val, M2)
    return ASI_val, ASIT_val


def XSII(CLOSE, HIGH, LOW, N=102, M=7):
    """薛斯通道II: TD1, TD2, TD3, TD4"""
    AA = MA((2 * CLOSE + HIGH + LOW) / 4, 5)
    TD1 = AA * N / 100
    TD2 = AA * (200 - N) / 100

    CC = ABS((2 * CLOSE + HIGH + LOW) / 4 - MA(CLOSE, 20)) / MA(CLOSE, 20)
    DD = DMA(CLOSE, CC)
    TD3 = (1 + M / 100) * DD
    TD4 = (1 - M / 100) * DD
    return TD1, TD2, TD3, TD4


def SAR(HIGH, LOW, N=10, S=2, M=20):
    """抛物转向指标（Parabolic SAR）"""
    f_step = S / 100
    f_max = M / 100
    af = 0.0
    is_long = HIGH[N - 1] > HIGH[N - 2]
    b_first = True
    length = len(HIGH)

    s_hhv = REF(HHV(HIGH, N), 1)
    s_llv = REF(LLV(LOW, N), 1)
    sar_x = np.repeat(np.nan, length)

    for i in range(N, length):
        if b_first:
            af = f_step
            sar_x[i] = s_llv[i] if is_long else s_hhv[i]
            b_first = False
        else:
            ep = s_hhv[i] if is_long else s_llv[i]
            if (is_long and HIGH[i] > ep) or ((not is_long) and LOW[i] < ep):
                af = min(af + f_step, f_max)
            sar_x[i] = sar_x[i - 1] + af * (ep - sar_x[i - 1])

        if (is_long and LOW[i] < sar_x[i]) or ((not is_long) and HIGH[i] > sar_x[i]):
            is_long = not is_long
            b_first = True
    return sar_x


def TDX_SAR(High, Low, iAFStep=2, iAFLimit=20):
    """通达信版本抛物转向指标"""
    af_step = iAFStep / 100
    af_limit = iAFLimit / 100
    SarX = np.zeros(len(High))

    bull = True
    af = af_step
    ep = High[0]
    SarX[0] = Low[0]

    for i in range(1, len(High)):
        if bull:
            if High[i] > ep:
                ep = High[i]
                af = min(af + af_step, af_limit)
        else:
            if Low[i] < ep:
                ep = Low[i]
                af = min(af + af_step, af_limit)

        SarX[i] = SarX[i - 1] + af * (ep - SarX[i - 1])

        if bull:
            SarX[i] = max(SarX[i - 1], min(SarX[i], Low[i], Low[i - 1]))
        else:
            SarX[i] = min(SarX[i - 1], max(SarX[i], High[i], High[i - 1]))

        if bull:
            if Low[i] < SarX[i]:
                bull = False
                tmp_SarX = ep
                ep = Low[i]
                af = af_step
                if High[i - 1] == tmp_SarX:
                    SarX[i] = tmp_SarX
                else:
                    SarX[i] = tmp_SarX + af * (ep - tmp_SarX)
        else:
            if High[i] > SarX[i]:
                bull = True
                ep = High[i]
                af = af_step
                SarX[i] = min(Low[i], Low[i - 1])

    return SarX
