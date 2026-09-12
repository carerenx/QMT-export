# DayT 固定比较与严格连续回测

## 两层结果不得混称

固定比较档案：`backtest/dayt_golden_20260910/`。

- 标的：601869.SH；99个完整交易日，2026-04-21至2026-09-10。
- 保留原始1分钟、日线、v39/v51源码、当时使用的core/infra模块和原回放脚本。
- 每日独立重新给予200股底仓、100,000元现金；零滑点及一分钱滑点。
- 这是历史版本比较模型，不是连续账户，也不是实盘收益。
- 零滑点黄金相对持有毛增量：v51 **−14,042元 / 366次成交**；v39 **−11,531元 / 392次成交**。
- 24个档案文件的SHA-256在`manifest.json`；zip中的`results.json`永久保留原逐笔结果。
- 首次完整复现已通过：396次独立日实验，逐笔、周期毛收益、未完成数量与黄金结果一致。

## 使用方法

在仓库根目录运行：

```powershell
python backtest/dayt_benchmark.py verify
python backtest/dayt_benchmark.py replay
python -m unittest discover -s tests
```

`verify`检查档案清单和文件哈希；`replay`在临时目录解压旧源码及依赖后运行，不使用后来修改的同名公共模块，不下载数据，不覆盖原结果。断言失败或子进程失败返回非零状态。

不要为日常新策略测试运行`freeze`。已有黄金目录时该命令拒绝写入。

复现环境记录：Python 3.13.14、NumPy 2.5.1、pandas 3.0.3。系统环境不是归档虚拟机；换环境后必须重新跑黄金验证，不能默认浮点及依赖行为一致。

## 新策略接入

在`backtest/dayt_registry.py`登记源码名称。当前适配协议是独立策略提供`PortfolioRunner`和`StrategyRunner(portfolio, code)`；v39保留专用旧接口转换。不同接口的策略须补适配及对应测试，不可仅改文件名就视为兼容。

```powershell
python backtest/dayt_compare_candidate.py --version v52 --output analysis/my_run/fixed_v52.json
python backtest/dayt_strict.py --version v52 --output analysis/my_run/strict_v52.json
python backtest/dayt_strict.py --version v52 --rate 0.0003 --output analysis/my_run/strict_v52_3bp.json
python backtest/dayt_strict.py --version v52 --rate 0.001 --output analysis/my_run/strict_v52_10bp.json
```

各命令拒绝覆盖既有输出。固定比较命令先复现黄金结果，再使用zip中的原始数据运行候选。旧版固定结果必须从黄金档案取得，不能以当前目录重跑结果替代。

严格命令检查冻结数据、旧策略与其公共依赖的哈希，记录研究驱动及候选源码哈希。输出不同引擎版本的结果时必须同时标出哈希，禁止当作同一次实验混合排序。

## 严格模型及限制

- 连续现金、持仓、T+1可卖数量，日间不重新赠送资金或底仓。
- 完整分钟收盘驱动策略；下单本身不成交。最早处理其后的OPEN事件，随后才反馈成交。
- 每日09:29另有盘前生命周期事件，仅暴露昨日已知行情，供原策略进行换日维护；不能在这个事件看到当天开盘价或成交。这不是一次盘中开仓决策。
- 同一墙钟时间的前一分钟CLOSE、下一分钟OPEN仍是先后不同事件。
- FIX订单只在后续满足限价的开盘事件成交；不会利用分钟高低价猜测先后顺序。竞争价在此研究模型中按后续开盘事件市场成交建模，不模拟真实队列。
- 无成交量、非连续交易时段，以及接近主板涨跌停且高低相同的一字分钟不成交。不能据此宣称已经覆盖所有板块、ST、临停或撮合规则。
- 每分钟所有订单共用不超过分钟股数1%的额度，向下取整到交易单位，按委托先后处理部分成交。
- 分钟成交量是历史撮合容量假设，不作为策略特征；这仍不是逐笔委托队列重建。
- 单边5bp、每笔委托最低5元，实际扣现金；部分成交累计计算同一委托最低费用。3bp/10bp是敏感性假设，不是已确认的券商收费。
- 策略同步等待成交时推进交易事件，等待期间不额外调用策略形成决策。
- v51遇隔夜未平腿按原逻辑停止；v39若跨日重置会丢失未平账本则记录无效，不暗中修复旧策略。
- 停止后保留实际现金和持仓，估值到期末。这样的收益只能解释为“停止后的剩余账户”，不能作为正常99天运行收益参与验收。

## 研究流程

### 旧策略的可选跨日适配

```powershell
python backtest/dayt_strict.py --version v39 --legacy-carry --output analysis/my_run/v39_carry.json
python backtest/dayt_strict.py --version v51 --legacy-carry --output analysis/my_run/v51_carry.json
```

`--legacy-carry`是明确改变跨日行为的离线适配，并非原版基线：未平腿保留退出参考、现金、持仓及成交账本，跨日只刷新当日次数与观察缓存；旧腿全部平仓前不新增周期或阶梯，全部平仓后再初始化当日信号。v39还保留尾盘平仓未成交的MOM腿，并取消部分成交委托的未成交余量，避免内部状态与真实模拟成交脱节。

结果标记为`LEGACY_CARRY_EXITS_ONLY`，额外记录`carry_events`和适配模块哈希。默认不加该参数时仍保持原版停止行为，黄金文件和原策略源码不变。

```powershell
python analysis/research_v52.py --output analysis/my_run/development
```

预先固定前69天（截至2026-07-30）研究：仅方向准入0/0.2/0.4、仅隔夜、两者组合0/0.2/0.4、关闭正向对照；每组真实重新运行资金路径。命令不读取后30天作候选选择。

后30天已在之前研究中被观察过，不是真正样本外。若连续基线无效，或回撤/敞口约束不满足，不选上线候选。只有明确候选后才单独进行留出诊断；不得根据诊断结果再搜索参数。

任何一次测试失败、停止、数据不足和未完成交易都必须进入报告。真实费率尚未确认；即使研究净增量为正，也不能宣称实盘净收益验收通过。
