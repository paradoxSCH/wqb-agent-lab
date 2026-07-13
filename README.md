# WQB Agent Lab

WQB Agent Lab 是一个本地优先、agent-native 的 WorldQuant BRAIN 研究工作台，用来把因子挖掘从零散脚本整理成可审计的研究 loop：行为经济学假设、WQB 字段代理、候选生成、模拟预算、失败诊断、记忆治理、评估报告和提交队列都留在本地项目里。

它适合已经熟悉 WQB alpha 研究、希望用 agent 改进研究流程的个人研究者。默认不会自动提交，也不会在没有显式配置和命令的情况下消耗真实 WQB 模拟预算。

> Not affiliated with WorldQuant or WorldQuant BRAIN. This project does not guarantee alpha quality, platform acceptance, rewards, or profits. You are responsible for complying with platform terms and local laws.

![WQB Agent 当前架构](docs/assets/wqb-agent-architecture-current-zh.svg)

## 核心能力

- WQB 平台边界：`src.wqb_agent_lab.platform.WQBClient` 统一封装认证、alpha 查询、checks、simulation、submit 状态确认。
- MCP 工具面：`src/wqb_mcp` 暴露 agent 可调用的 WQB 工具，避免把平台 HTTP 细节散落到 prompt 和脚本里。
- 行为经济学候选生成：从行为机制、可代理字段、假设、kill condition 到 scan budget 的受控链路。
- 记忆治理：把运行结果、诊断、proxy map、policy feedback 转成可评估的本地 evidence，而不是无限堆上下文。
- 独立提交 worker：挖掘 loop 只把候选放进队列，提交确认、重试、限流和 registry 更新由 worker 处理。

## 当前闭环边界

当前系统已经具备无人值守执行链，但不把“组件存在”等同于“反馈已经闭合”：

- 记忆 worker 会同步和治理运行证据，独立查询接口也已经存在；生产 planner 尚未自动调用记忆检索。
- `wqb-engine submission.*` 会经过结构化决策、策略评估和审计；自动 backlog 路径尚未统一经过结构化 submission governance。
- completion worker 会生成效果和 ablation 报告；评估报告尚未直接控制下一轮预算。

因此当前定位是可无人值守的执行闭环和部分反馈闭环，不宣称是完全自进化系统。

## 快速开始

普通研究用户只需要 Python runtime，不需要 Node.js。克隆仓库后，在 Windows PowerShell 运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Profile runtime
```

如果机器上还没有 uv，脚本会输出机器可读的 `ONBOARDING_ERROR` 和修复命令；确认官方安装来源后，显式增加 `-InstallUv` 即可。macOS/Linux 使用 `sh scripts/bootstrap.sh --profile runtime`。bootstrap 会由 uv 安装受支持的 Python 3.12，不依赖或覆盖电脑上已有的其他 Python。

完成后运行结构化 doctor 和本地演示：

```powershell
uv run python -m scripts.dev doctor --profile runtime --json
uv run wqb-engine --help
uv run wqb-engine demo --workspace-root . --run-tag product-demo
```

doctor 的每个失败项都包含 `fix_command`，顶层包含 `actions` 和 `next_command`，可直接供用户的 Agent 读取。以上命令不要求激活虚拟环境，不需要 WQB 凭证，不会调用真实平台，也不会提交 alpha。完整的全新环境路径、支持版本和错误处理见 [首次安装](docs/user/GETTING_STARTED.md) 与 [安装诊断](docs/user/TROUBLESHOOTING.md)。

安装后可运行只使用合成数据的本地演示：

真实 WQB 调用数和提交尝试数都固定为 `0`。演示产物位于 `.local/data/runs/continuous-alpha/product-demo/`，该目录默认被 Git 忽略。

## 配置研究政策

公开示例把预算和行为经济学边界统一放在 `research_policy`。先创建本地配置，再验证和查看生效内容：

```powershell
New-Item -ItemType Directory -Force .local\research\workflows | Out-Null
copy configs\examples\production-workflow.example.json .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe policy.validate --config .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe policy.show --config .local\research\workflows\production.json
```

例如把每日预算改为 20 时，阶段预算必须严格守恒为 8/8/4：

```json
{
  "daily_simulation_limit": 20,
  "exploration_share_limit": 0.4,
  "exploration_stages": ["direction_probe"],
  "stage_allocations": {
    "direction_probe": 8,
    "scale_winners": 8,
    "holdout": 4
  }
}
```

修改 `.local\research\workflows\production.json` 后重新运行 `policy.validate` 和 `policy.show`。用户只需要维护 `research_policy.budget` 与 `research_policy.behavioral_boundaries`，不要再添加 `daily_budget_modes`、`stage_order` 或 `max_daily_budget` 等旧预算来源。

每个进入生产切片的候选必须携带 `behavioral_mechanism`、`fields` 和 `kill_conditions`。当前 live mining 仍需要 agent 或本地生成器产生包含这些字段的 scan config；从行为政策到生产 scan config 的自动生成尚未闭合，项目不会把缺失元数据的候选伪装为合规。完整字段说明见 [RESEARCH_POLICY.md](docs/user/RESEARCH_POLICY.md)。

## 配置 LLM Provider

公开 workflow 默认使用 `"provider": "disabled"`，无需 API key，也不会发起模型请求。需要 LLM 规划或候选细化时，只修改同一个 `llm_provider` block；支持 OpenAI-compatible、Anthropic、Gemini、Ollama 和本地 CLI。凭证仅写入本地 `.env`，workflow 只保存环境变量名。以下命令从项目根目录运行；已经导出的环境变量优先于 `.env` 中的同名值。

```powershell
.\.venv\Scripts\wqb-engine.exe llm.validate --config .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe llm.show --config .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe llm.probe --config .local\research\workflows\production.json
```

`llm.validate` 和 `llm.show` 只做本地解析，不访问网络，也不启动 CLI；`llm.probe` 只有在用户显式运行时才发出一次最小模型请求，且不会调用 WQB。Provider 能力不会开启真实 simulation 或 submit，二者仍分别受 `WQB_LIVE_SIMULATION_CAPABILITY` 和 `WQB_LIVE_SUBMIT_CAPABILITY` 控制。

完整配置示例、旧配置迁移顺序和 CLI 安全边界见 [LLM_PROVIDERS.md](docs/user/LLM_PROVIDERS.md)。统一 Provider 只负责规划和候选细化，当前不能自动生成生产 scan config，也不代表普通用户已经可以完全无人值守运行。

## 安全规划与生产预检

保持 `.env` 中两个 capability 为 `0`，运行一次不写文件、不执行模拟的安全规划：

```powershell
.\.venv\Scripts\python.exe -m scripts.run.workflow --workspace-root . --workflow-config .local\research\workflows\production.json --run-once --dry-run
```

生产 launcher 会先验证政策、side-effect capability 和 WQB session。以下命令不会绕过门禁；凭证为空时会在建立 session 前快速失败：

```powershell
.\.venv\Scripts\python.exe -m scripts.launch_daemon --workspace-root . --workflow-config .local\research\workflows\production.json --no-execute-scans --once
```

只有准备进行真实模拟时，才在本地 `.env` 填写 WQB 凭证并设置 `WQB_LIVE_SIMULATION_CAPABILITY=1`；自动提交还必须单独设置 `WQB_LIVE_SUBMIT_CAPABILITY=1` 并显式传入 launcher 的 `--auto-submit`。

生产运行会在 `.local/data/runs/continuous-alpha/<run-tag>/` 写入 `research_policy_evaluation.json` 和 `daily_budget_ledger.json`。前者逐候选记录允许/阻断结果及错误码，后者记录政策版本、digest、启用机制、预算和阻断统计。

## 公开快照

当前私有工作仓库不应直接推送。可以先检查 manifest 控制的公开文件集合：

```powershell
uv run python -m scripts.dev release-check --json
```

确认选择结果后，可生成一个不包含 `.git` 历史的草稿快照：

```powershell
uv run python -m scripts.checks.public_snapshot_smoke --workspace-root . --output dist/public-snapshot --json
```

导出器默认排除研究配方、运行数据、平台目录和内部文档，并写出逐文件 SHA-256 清单。只要 `publish_ready` 为 `false`，该目录就只是审查用 draft，不能作为正式公开版本。

不要发布从当前私有工作仓库直接构建的 sdist 或 wheel。Python 和 TypeScript 发布制品只能从审查后的公开快照或干净的公开仓库构建，避免把本地研究脚本带入制品。

需要连接 WQB 时，再编辑 `.env`：

```env
WQB_LIVE_SIMULATION_CAPABILITY=0
WQB_LIVE_SUBMIT_CAPABILITY=0
```

真实模拟和自动提交必须分别显式开启并保持可审计。`auto_submit=true` 只表达 agent 的执行意图，不会替代 runtime capability。公开示例和 CI 都不会启用平台写操作。

## 常用验证命令

普通研究运行时：

```powershell
uv run python -m scripts.dev doctor --profile runtime --json
uv run wqb-engine demo --workspace-root . --run-tag product-demo
```

只有开发 MCP、UI 或参与仓库开发时才安装 Node.js 22.12+ 或 24 LTS，并使用 full 档位：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Profile full
uv run python -m scripts.dev doctor --profile full --json
uv run python -m scripts.dev check
uv run python -m scripts.dev test
uv run python -m scripts.dev build
uv run python -m scripts.dev release-check --json
```

## 项目结构

生产编排器通过 `src.wqb_agent_lab.workflow.ResearchWorkflow` 暴露。`ContinuousAlphaScheduler` 仅用于恢复历史实验运行，不属于新的产品 API。

```text
src/wqb_agent_lab/        产品命名空间与稳定平台、workflow 边界
src/wqb/                  一轮兼容期的旧导入转发
src/alpha_memory/         分层记忆、检索、治理与评估
src/output_evaluation/    输出质量门禁、policy evaluator、budget feedback
src/behavioral_proxy/     WQB 字段可代理的行为经济学映射
src/wqb_engine/           机器可读的 Python CLI
packages/wqb-agent-mcp/   可选的 TypeScript MCP 工具面
packages/wqb-agent-ui/    可选的 React 本地运行监控器
scripts/run|workers|submit/  工作流入口与解耦 worker
configs/examples/         不含私有研究配方的公开配置模板
schemas/                  Python 与 TypeScript 的 JSON contract
docs/user/                当前用户文档与安装诊断
docs/architecture/        当前架构、目录权威边界与 ADR
tests/                    不依赖真实 WQB 凭证的单元测试优先
```

更多仓库边界见 [仓库目录权威边界](docs/architecture/REPOSITORY_LAYOUT.md)、[文档索引](docs/README.md) 和 [OPEN_SOURCE_READINESS.md](docs/maintainers/OPEN_SOURCE_READINESS.md)。

## English Summary

`wqb-agent-lab` is a local-first, agent-native research harness for WorldQuant BRAIN workflows. It focuses on auditable hypothesis generation, behavioral proxy mapping, memory governance, evaluation, and submission queues. It is not an official WorldQuant project and does not promise rewards or profits.

## Python / TypeScript Contract

`schemas/` is the stable JSON contract boundary between the Python research engine and the existing TypeScript packages. TypeScript MCP, dashboard, CLI, and IDE integrations consume these schemas instead of importing Python internals directly.

Python producers can validate outbound artifacts through `src.contracts`:

```python
from src.contracts import assert_valid_contract

assert_valid_contract("submission_job", payload)
```

The engine command boundary is the project-local executable:

```powershell
.\.venv\Scripts\wqb-engine.exe schemas.list
.\.venv\Scripts\wqb-engine.exe schemas.digest --schema submission_job
Get-Content payload.json | .\.venv\Scripts\wqb-engine.exe contracts.validate --schema submission_job
.\.venv\Scripts\wqb-engine.exe submission.evaluate
.\.venv\Scripts\wqb-engine.exe submission.submit_intent --run-dir .local/data/runs/example
.\.venv\Scripts\wqb-engine.exe submission.execute_live --run-dir .local/data/runs/example
.\.venv\Scripts\wqb-engine.exe loop.dry_run_validate --workspace-root . --run-tag dry-run-loop-validation
```

`loop.dry_run_validate` runs the local closed-loop validation path and writes candidate generation, policy feedback, decision attribution, memory governance, and memory sync artifacts without calling WQB or submitting alphas.

The first TypeScript shell lives in `packages/wqb-agent-mcp`. It uses the official MCP SDK for read-only schema and contract tools. Tool metadata and engine invocation are in TypeScript, while WQB semantics remain in Python.

Live WQB operations are exposed as capabilities rather than hidden behind tool adapters. Autonomous simulation requires `WQB_LIVE_SIMULATION_CAPABILITY=1`; autonomous submission requires `WQB_LIVE_SUBMIT_CAPABILITY=1`. Calls made through `submission.evaluate`, `submission.submit_intent`, `submission.execute_live`, and `submission.audit_tail` use Python governance and audit records. The automatic mining backlog still queues the independent worker directly and is documented above as an open integration boundary.

The first TypeScript dashboard lives in `packages/wqb-agent-ui`. It is a Vite/React read-only run monitor that consumes the public `run_summary` contract and does not read Python internals directly.

```powershell
npm test --prefix packages/wqb-agent-ui
npm run typecheck --prefix packages/wqb-agent-ui
npm run build --prefix packages/wqb-agent-ui
```

## License And Citation

Software, schemas, tests, and machine-executable project files are licensed
under Apache-2.0. Documentation prose and visual assets are licensed under
CC BY 4.0. Code snippets in documentation remain Apache-2.0 unless marked
otherwise. See `LICENSE`, `LICENSES/CC-BY-4.0.txt`, and `NOTICE`.

If this project materially influences a paper, article, course, architecture,
or another agent system, please credit WQB Agent Lab. `CITATION.cff` contains
machine-readable citation metadata. This citation request does not add a
restriction beyond the applicable licenses.
