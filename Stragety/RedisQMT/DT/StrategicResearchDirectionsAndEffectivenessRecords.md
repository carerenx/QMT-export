# RedisQMT 日内做 T 研究方向与有效性记录

> 记录规则：[`Stragety/STRATEGY_RESEARCH_RECORD_RULES.md`](../../STRATEGY_RESEARCH_RECORD_RULES.md)

## 当前结论

- 当前最佳版本：待确认
- 适用评价范围：RedisQMT 外部 Python 日内做 T
- 基准版本：MiniQMT `DayTradeing_v39_stragety_miniqmt.py`（删除 MOM）
- 最后更新时间：2026-09-15
- 说明：DT_v1 完成运行时迁移与模块拆分，统一口径验证完成前不标记为最佳。

## 研究记录

### 2026-09-15｜DT｜v1｜v39 非 MOM 逻辑迁移至 RedisQMT

- 状态：待验证
- 策略文件：`Stragety/RedisQMT/DT/DT_v1.py`
- 基准版本：`Stragety/MiniQMT_Stragety/DayT/DayTradeing_v39_stragety_miniqmt.py`
- 研究假设：将运行时访问隔离到 RedisQMT Adapter，并复用冲高回落与探底回升状态机，可以在不引入 MOM 的情况下保留 v39 正反 T 行为。
- 主要变更：删除 MOM 和 MiniQMT Context/connector；新增 Redis RPC Adapter、纯日线信号、公共拐点状态机及精简策略编排。
- 预期改善：降低后续做 T 策略重复代码量，并使交易逻辑可以在无真实 Redis 和无真实委托的条件下测试。
- 可能代价：运行时与 v39 不同，Redis RPC 时延、委托回报和当前日线过滤仍需终端联调。
- 验证口径：601869.SH、1 分钟行情、与冻结 v39 相同的资金/持仓/费用/T+1/成交规则，并单独报告 Redis 执行安全测试。
- 复现命令：`python backtest/dayt_benchmark.py replay`；`python -m pytest tests/test_redisqmt_dayt_v1.py -q`
- 证据：冻结黄金回放通过（396 个独立日）；`tests/test_redisqmt_dayt_v1.py` 20 项通过；根 `tests/` 270 项及 39 个子测试通过；BigQMT Redis 专项 69 项通过；本地 `--check` 通过。
- 结果摘要：v39/v51 冻结成交与盈亏未变化；DT_v1 公共状态机、正反 T、阶梯、涨停保护、成交超时与部分成交安全测试通过。2026-09-15 只读 `--probe` 因 Redis 服务连接超时未完成，未发送委托。
- 分市场阶段结果：未验证。
- 结论：继续验证。
- 最佳版本影响：不影响。
- 后续方向：完成固定连续账户比较，再验证 Redis 行情新鲜度、委托回报与恢复行为。
