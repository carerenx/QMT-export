---
name: git-commit
description: Execute git commit with conventional commit message analysis, intelligent staging, and message generation. Use when user asks to commit changes, create a git commit, or mentions "/commit".
---

# Git Commit — 智能提交

分析当前工作区变更，生成符合项目风格的 conventional commit message，执行提交。

## 触发条件

- 用户说 "commit"、"提交"、"/commit"、"帮我提交"
- 用户要求生成 commit message
- 用户需要分组/分批提交

## 工作流程

### 1. 扫描变更

```bash
git status          # 未跟踪 + 已修改
git diff --stat     # 未暂存的修改
git diff --cached --stat  # 已暂存的修改
```

### 2. 分析变更内容

对每个变更文件，判断类别和影响范围：

| 变更内容 | type | 示例 |
|---------|------|------|
| 新策略/新功能/新文件 | `feat` | `feat: add Alpha144 v3 backtest-optimized version` |
| Bug修复 | `fix` | `fix: stop loss not triggering on gap-down open` |
| 性能优化 | `perf` | `perf: reduce stock pool from 500 to 35 with MA20 filter` |
| 重构(无功能变化) | `refactor` | `refactor: extract position sizing to shared module` |
| 文档/注释/非代码 | `docs` | `docs: add QMT API usage examples for passorder` |
| 清理/配置/杂项 | `chore` | `chore: remove deprecated v1-v6 strategy files` |

### 3. 生成 commit message

格式：`<type>: <简短中文或英文描述>`

规则：
- 首行不超过 72 字符
- 英文用现在时、小写开头
- 中文直接描述变更内容
- 多文件 → 概括为 1-2 个主题词 + 列举关键项
- 如有 breaking change，在 body 中注明

### 4. 分组提交 (可选)

当变更为多个独立主题时，建议分批提交：

```bash
git add MyPy-Q/Alpha144_*.py
git commit -m "feat: Alpha144 liquidity impact strategy v2-v5"

git add analysis/
git commit -m "feat: add backtest log parsers for v3/v4"
```

### 5. 执行提交

```bash
# 全部提交 (最常用)
git add -A
git commit -m "<message>"

# 仅提交已暂存
git commit -m "<message>"
```

## 项目约定

基于本仓库 `AGENTS.md` 和提交历史：

- **策略文件** (`MyPy-Q/QMT_*.py`) → 通常用 `feat:`，标注版本号和关键特性
- **回测引擎** (`backtest/`) → `feat:` 或 `fix:`，注明影响的模块
- **分析脚本** (`analysis/`) → `feat:` 或 `chore:`
- **文档** (`docs/`) → `docs:`
- **配置** (`.Codex/`, `.gitignore`) → `chore:`
- **不要提交** `Log/`、`backtest/output/`、`__pycache__/` 等运行时产物

## 示例

```bash
# 新增策略
git commit -m "feat: QMT 迷你反T v5 动态买回 — 基于实际卖出价触发买回"

# 多文件混合
git commit -m "feat: add Alpha144 v2-v5 strategies, analysis scripts, and serenity-challenge docs"

# 清理
git commit -m "chore: remove deprecated StockPickingStrategy v1-v6"

# Bug修复
git commit -m "fix: MA60 trend filter too strict, relax to MA20"

# 性能
git commit -m "perf: dedup signals within same bar, cut processing 40%"
```

## 注意事项

- **不要提交 `settings.local.json`** 中的个人配置（token、路径等），除非确认是项目通用配置
- 提交前确认文件编码：策略文件使用 GBK (`# -*- coding: gbk -*-`)
- 大文件 (>1MB) 不要直接提交，用 Git LFS 或 `.gitignore`
- `git add -A` 前先检查有无误加的临时文件
