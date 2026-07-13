# 安装诊断

先运行结构化诊断，不要从构建日志中猜测环境状态：

```powershell
uv run python -m scripts.dev doctor --profile runtime --json
```

开发 MCP/UI 时改用 `--profile full`。

## 常见诊断码

| check 或错误码 | 含义 | 处理 |
| --- | --- | --- |
| `uv_missing` | 没有 uv 或 PATH 尚未刷新 | 按错误中的官方链接安装；安装后新开终端 |
| `python` | 当前 Python 不在 3.11-3.12 | 运行 `uv python install 3.12`，再用 `uv sync --python 3.12 --frozen` |
| `python_dependencies` | 没有按锁文件安装，或虚拟环境不完整 | 运行 doctor 给出的 `uv sync` 命令 |
| `node_missing` / `node` | full 档位没有 Node，或版本不受支持 | 安装 Node.js 22.12+ 或 24 LTS；runtime 档位不需要 Node |
| `node_dependencies` | MCP/UI 的 `node_modules` 不完整 | 对两个 package 分别运行 `npm ci` |
| `npm_node_runtime` / `npm_node_runtime_mismatch` | `node` 与 `npm` 来自两套安装 | 清理 PATH 中旧的 Node/npm，重开终端，确认 `node --version` 与 `npm version --json` 一致 |
| `local_env` | `.env` 尚未初始化 | 从 `.env.example` 复制；不要把 `.env` 提交到 Git |
| `workflow_config` | 本地政策配置缺失或 JSON 无效 | 从公开示例重新创建，或运行 `policy.validate` 定位字段 |
| `config_not_found` | 命令引用了不存在的 workflow | 使用 `.local/research/workflows/production.json` |
| `capability_disabled` | 真实副作用未授权 | onboarding 中保持关闭；仅在明确准备真实运行后单独授权 |

## Agent 处理规则

Agent 应读取 doctor JSON 的 `checks`、`actions` 和 `next_command`。`status=blocked` 时只执行对应修复，不应改锁文件、不应换包管理器、不应绕过 engines，也不应开启 WQB capability。修复后必须再次运行同一 profile 的 doctor。

如果 `uv sync --frozen` 报锁文件与项目不一致，这是仓库发布问题，不是用户环境问题。不要删除 `uv.lock` 或改用 `pip install -e`；提交 issue 并附上 doctor JSON、操作系统和命令错误，但先删除任何本地路径或账号信息。
