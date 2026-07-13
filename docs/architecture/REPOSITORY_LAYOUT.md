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
|   `-- wqb-agent-ui/           可选 React 本地监控器
|-- release/                    公开快照与依赖许可规则
|-- schemas/                    Python/TypeScript JSON contract
|-- scripts/
|   |-- bootstrap.ps1|sh        全新环境入口
|   |-- dev.py                  doctor/check/test/build/release-check
|   |-- run/                    canonical workflow launcher
|   |-- workers/                evaluation、memory、registry、submission worker
|   |-- submit/                 提交队列与 worker 实现
|   |-- checks/                 制品、供应链和公开快照检查
|   `-- maintenance/            显式维护任务，不属于日常入口
|-- src/
|   |-- wqb_agent_lab/          稳定产品命名空间
|   |-- wqb_engine/             机器可读 CLI
|   |-- wqb_mcp/                Python MCP adapter
|   |-- alpha_memory/           记忆存储、检索、治理和评估
|   |-- research_policy/        预算与行为边界
|   |-- output_evaluation/      输出诊断、策略和预算反馈
|   `-- */                      仍在收敛中的内部实现模块
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
| 本地无凭证演示 | `uv run wqb-engine demo --workspace-root . --run-tag product-demo` |
| 研究政策管理 | `uv run wqb-engine policy.validate` / `policy.show` |
| 工作流运行 | `python -m scripts.run.workflow` |
| 仓库工程检查 | `uv run python -m scripts.dev check` |
| 完整发布检查 | `uv run python -m scripts.dev release-check --json` |

根目录的 `run_scan.py` 和 `src/wqb/` 是有移除周期的兼容边界，不是新用户或 Agent 的发现入口。`scripts/` 中未列为 canonical 的平铺脚本是内部运维能力；调用前应先查对应测试和当前文档，不应根据文件名猜测参数。

私有维护仓库可能额外出现 `.local/`、`dist/`、`logs/`、`configs/scans/` 和 `docs/archive/`。这些目录不会进入公开快照，也不构成开源用户可依赖的产品接口。
