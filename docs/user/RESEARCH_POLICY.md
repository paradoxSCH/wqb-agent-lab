# 研究政策

`research_policy` 是公开生产配置中唯一的预算与行为经济学边界来源。策略验证完全在本地执行，不需要 WQB 凭证，也不会发起网络请求。

## 用户维护面

### 预算

- `daily_simulation_limit`：每日最多消耗的模拟次数。
- `exploration_share_limit`：探索方向可占用的预算比例上限，范围为 0 到 1。
- `exploration_stages`：明确哪些阶段计入探索预算；这些阶段的预算总和不得超过探索比例上限。
- `stage_allocations`：各阶段预算；所有值之和必须严格等于 `daily_simulation_limit`。

阶段名称同时定义执行顺序。公开示例不再维护 `daily_budget_modes`、`stage_order` 或 `max_daily_budget` 等重复预算来源。

### 行为经济学边界

- `block_unclassified_candidates`：阻断没有声明行为机制的候选。
- `require_kill_conditions`：要求候选包含机制规定的全部停止条件。
- `forbid_pure_price_volume`：阻断仅依赖价量字段的候选。
- `mechanisms[].mechanism_id`：稳定且唯一的行为机制 ID。
- `mechanisms[].enabled`：是否允许该机制进入预算分配。
- `mechanisms[].allowed_proxy_fields`：允许的 WQB 代理字段模式，支持 `*` 通配符。
- `mechanisms[].kill_conditions`：候选必须声明的事前停止或替换条件。

至少要启用一个机制。启用的机制必须有非空的代理字段模式和 kill conditions。

## 候选契约

进入生产 scan config 的每个候选必须携带：

```json
{
  "candidate_id": "candidate-001",
  "behavioral_mechanism": "reference_point_disposition_drift",
  "fields": ["anl4_eps_revision"],
  "kill_conditions": ["SELF_CORRELATION", "LOW_FITNESS"],
  "expression": "..."
}
```

边界评估发生在候选多样性选择和模拟预算提交之前。未知、禁用或缺失的 `behavioral_mechanism`，越界或缺失的 `fields`，缺失的 `kill_conditions`，以及政策禁止的纯价量候选都会被阻断，并且消耗 0 次模拟预算。

当前 live mining 仍需要 **agent 或本地生成器** 生成包含上述元数据的 scan config。行为机制研究、字段选择和表达式生成到生产 scan config 的自动编排尚未自动闭合；政策层负责验证和阻断，不会替代候选生成，也不会根据表达式猜测行为机制。

## 配置与验证

在仓库根目录执行：

```powershell
New-Item -ItemType Directory -Force .local\research\workflows | Out-Null
copy configs\examples\production-workflow.example.json .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe policy.validate --config .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe policy.show --config .local\research\workflows\production.json
```

`policy.validate` 返回版本和稳定 SHA-256 digest；`policy.show` 额外返回规范化后的完整策略。配置文件缺失、JSON 无效、预算不守恒、机制重复或没有启用机制都会以结构化错误退出。

## 安全规划

```powershell
.\.venv\Scripts\python.exe -m scripts.run.workflow --workspace-root . --workflow-config .local\research\workflows\production.json --run-once --dry-run
```

该命令只进行 planning，不写运行产物、不执行 WQB simulation。配置存在并通过验证，并不等于已经授权真实副作用。

## 生产预检

```powershell
.\.venv\Scripts\python.exe -m scripts.launch_daemon --workspace-root . --workflow-config .local\research\workflows\production.json --no-execute-scans --once
```

launcher 按顺序检查策略、side-effect capability 和 session。`.env.example` 的凭证默认为空，`WQB_LIVE_SIMULATION_CAPABILITY=0`、`WQB_LIVE_SUBMIT_CAPABILITY=0`；空凭证会在创建网络 session 前快速失败。

进行真实模拟时才填写本地 `.env` 并把 simulation capability 改为 `1`。提交是独立能力，还需要 submission capability 为 `1` 和显式的 `--auto-submit`。

## 审计产物

实际生产切片后，run 目录中包含：

- `research_policy_evaluation.json`：policy 版本、digest、逐候选 allowed 状态、错误码和阻断统计。
- `daily_budget_ledger.json`：生效预算、阶段分配、policy digest、启用机制和累计阻断数量。
- 切片后的 scan config：`daily_budget_context.research_policy` 保存同一政策摘要，方便追踪模拟使用的边界版本。

若 workflow 仍在等待带行为元数据的 scan config，审计文件可能尚未生成；这表示候选生成边界尚未满足，不代表政策被跳过。
