# 仓库目录权威边界

公开仓库采用单仓库、双运行时结构。目录数量较多，但首次使用只需要 `scripts/bootstrap.*`、`scripts.dev doctor` 和 `wqb-engine` 三个入口。

```text
wqb-agent-lab/
|-- .github/                    CI、Issue 与 PR 模板
|-- configs/examples/           可公开的 workflow 模板，不含研究配方
|-- docs/
|   |-- user/                   当前用户操作文档
|   |-- architecture/           当前架构与决策
|   `-- maintainers/            发布与依赖治理
|-- packages/
|   |-- wqb-agent-mcp/          可选 TypeScript MCP 工具面
|   `-- wqb-agent-ui/           daemon 工作台的 React 前端与运行 API 消费者
|-- release/                    公开快照与依赖许可规则
|-- schemas/                    Python/TypeScript JSON contract
|-- scripts/
|   |-- bootstrap.ps1|sh        全新环境入口
|   |-- dev.py                  doctor/check/test/build/release-check
|   |-- evaluation/            评估、ablation 与 policy feedback 命令
|   |-- lib/                    CLI 共享帮助函数，不是运行入口
|   |-- memory/                 记忆导入、查询、同步、完整性与评估命令
|   |-- registry/               submitted alpha registry 同步命令
|   |-- research/               候选、proxy、repair 与 hypothesis 命令
|   |-- run/                    workflow、daemon、scan 与 stop 运行入口
|   |-- workers/                evaluation、memory、registry、submission worker
|   |-- submit/                 提交队列与 worker 实现
|   |-- checks/                 制品、供应链和公开快照检查
|   `-- maintenance/            仓库与本地状态维护任务
|-- wqb_agent_lab/              安装后使用的标准公开命名空间与 runtime 实现
|   |-- platform/               WorldQuant BRAIN 访问边界
|   `-- runtime/                operation journal 与 canonical scan runtime
|-- src/
|   |-- wqb_agent_lab/          仅保留到 0.3.0 的兼容导入层
|   |-- wqb_engine/             机器可读 CLI
|   |-- wqb_mcp/                Python MCP adapter
|   |-- alpha_memory/           记忆存储、检索、治理和评估
|   |-- research_policy/        预算与行为边界
|   |-- output_evaluation/      输出诊断、策略和预算反馈
|   `-- */                      内部实现模块
|-- tests/                      默认无凭证、无真实副作用的测试
|-- .python-version             推荐 Python 3.12
|-- .nvmrc                      推荐 Node 24 LTS
|-- pyproject.toml + uv.lock    Python 依赖唯一事实来源
`-- AGENTS.md                   编码 Agent 的冷启动和安全规则
```

## 入口优先级

| 任务 | 唯一推荐入口 |
| --- | --- |
| 检查普通运行环境 | `uv run python -m scripts.dev doctor --profile runtime --json` |
| 离线无凭证演示 | `uv run wqb-engine demo --workspace-root . --run-tag product-demo` |
| 研究政策管理 | `uv run wqb-engine policy.validate` / `policy.show` |
| 工作流运行 | `python -m scripts.run.workflow` |
| 仓库工程检查 | `uv run python -m scripts.dev check` |
| 完整发布检查 | `uv run python -m scripts.dev release-check --json` |

0.3 已删除根目录扫描启动器和 `src` 下的兼容命名空间。根级 `scripts/`
只保留 bootstrap、工程诊断和按职责分类的当前命令，不再新增转发启动器。

私有维护仓库可能额外出现 `.local/`、`dist/`、`logs/`、`configs/scans/` 和 `docs/archive/`。这些目录不会进入公开快照，也不构成开源用户可依赖的产品接口。
