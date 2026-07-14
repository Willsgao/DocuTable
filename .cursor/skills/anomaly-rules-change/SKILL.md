---
name: anomaly-rules-change
description: >-
  用户随机提问时，若涉及表格质检/异常检测/红点/规则漏检误报，自动走防回归：
  意图识别、init-scope、preflight、小步改码、verify。用户无需记命令或填模板。
---

# 表格质检规则修改（防回归）

## 零负担模式（默认）

用户**随便问**即可，不要求：
- 填 `anomaly-change-scope.txt`
- 跑 preflight / verify 命令
- 读契约或流程文档

**由你**根据用户原话判断是否在改表格质检；若是，静默执行下方强制步骤。

### 意图识别（自行判断，勿反问用户「要不要走流程」）

| 信号 | 动作 |
|------|------|
| 修规则 / 漏检 / 误报 / 表头 / 折行 / 🔴 | 走本技能全流程 |
| 只问「为什么这样判」、不改代码 | 读契约解释即可，可不跑 guard |
| 完全无关（样式、部署等） | 忽略本技能 |

## 强制步骤（表格质检类改动，不得跳过）

用户只描述**现象或目标**；你必须执行标准流程，避免修一个坏一个。

## 启动时必读

1. [docs/正常表契约.md](../../docs/正常表契约.md)
2. [docs/表格质检-修改标准流程.md](../../docs/表格质检-修改标准流程.md)

## 强制步骤（不得跳过）

### 1. 改代码前

```powershell
cd <项目根>
python -m codes.v2_steps.anomaly_change_guard preflight
```

- **失败**：停止新功能，先修回归；向用户说明哪条黄金样本/单元测试挂了。

### 2. 自动写 scope（勿让用户手填）

```powershell
python -m codes.v2_steps.anomaly_change_guard init-scope "用户原话或任务摘要"
```

- 脚本会从模板生成 `anomaly-change-scope.txt`，`task` 用用户原话。
- **默认** `allowed` 仅含 `codes/v2_steps/table_anomaly_rules.py`。
- 动 `bridge` / `processor` / `UI` 时你自行在 scope 的 `notes` 追加原因。

### 3. 实现

- **最小 diff**；不重写整文件。
- 不删 `tests/anomaly_golden/cases/*.json`。
- 契约变更：先改 `docs/正常表契约.md` + 黄金样本，再改代码。

### 4. 改代码后

```powershell
python -m codes.v2_steps.anomaly_change_guard verify
```

- **失败**：不得说「已完成」；继续修或建议用户回滚。

### 5. 交付说明（固定格式）

```markdown
## 修改摘要
- 目标：…
- 改动文件：…

## 防护结果
- preflight: PASS/FAIL
- verify: PASS/FAIL

## 影响面
- 触及规则：…
- 未改动的契约条目：…

## 你需要做的
- 是否重开 PDF / 删缓存：…
```

## 黄金样本

- 目录：`tests/anomaly_golden/cases/`
- 新增场景：加 JSON + 更新 `manifest.json` + 跑 `verify`

## 禁止

- 未跑 `verify` 就宣称完成
- 一次改动多个无关模块
- 删除或弱化已有黄金样本/单元测试断言
- 擅自改契约却不更新文档与样本
