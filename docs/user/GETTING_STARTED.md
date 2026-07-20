# 首次安装与启动

本项目把首次安装分成两个档位，避免普通研究用户被前端开发依赖阻塞。

- `runtime`：Python 研究引擎、WQB 平台边界、研究政策、记忆与离线演示。推荐所有用户从这里开始，不需要 Node.js。
- `full`：在 runtime 基础上增加 MCP、UI、完整测试和构建，需要 Node.js。

## 支持基线

| 组件 | 支持范围 | 用途 |
| --- | --- | --- |
| uv | 0.11.27 或更高 | 安装 Python、创建虚拟环境、按锁文件安装依赖 |
| Python | 3.11 或 3.12，推荐 3.12 | 研究运行时 |
| Node.js | 22.12+ 或 24 LTS | 仅 full 档位需要 |
| npm | 10 或 11 | 仅 full 档位需要 |

Windows 和 GitHub Actions 的 Ubuntu 环境会持续验证；其他系统可以运行，但不属于首发版本的完整支持矩阵。仓库根目录的 `.python-version` 会引导 uv 使用 Python 3.12，`.nvmrc` 指向 Node 24 LTS；已有其他运行时版本不会被覆盖。

## Windows 全新环境

克隆仓库后，在仓库根目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Profile runtime
```

如果系统没有 uv，脚本会停止并输出 `ONBOARDING_ERROR`、官方文档和下一条命令。确认安装来源后，可以显式允许脚本下载固定版本的 uv 安装器：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -InstallUv -Profile runtime
```

脚本会让 uv 安装 Python 3.12、按 `uv.lock` 建立 `.venv`、创建本地 `.env` 和研究政策配置，然后运行 doctor。它不会读取 WQB 凭证，不会开启真实模拟或自动提交。

## macOS / Linux

```bash
sh scripts/bootstrap.sh --profile runtime
```

没有 uv 时，先检查官方安装方式，或显式运行：

```bash
sh scripts/bootstrap.sh --install-uv --profile runtime
```

## 验证 runtime

doctor 的 JSON 是人和 Agent 共用的事实来源：

```powershell
uv run python -m scripts.dev doctor --profile runtime --json
uv run wqb-engine policy.validate --config .local/research/workflows/production.json
uv run wqb-engine demo --workspace-root . --run-tag product-demo
```

`status=ready` 表示可以运行；`status=attention` 表示只有可选配置未完成；`status=blocked` 会以退出码 `2` 结束。每个失败项都带 `fix_command`，顶层还会给出 `actions` 和 `next_command`。doctor 不输出凭证值。

## 安装 full 档位

需要开发 MCP、UI 或运行完整仓库检查时，安装 Node.js 22.12+ 或 24 LTS 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Profile full
uv run python -m scripts.dev doctor --profile full --json
uv run python -m scripts.dev check
```

`full` bootstrap 会构建 React 工作台。启动后可以在浏览器维护预算与行为边界，并查看运行、记忆和评估状态：

```powershell
uv run python -m scripts.run.dashboard --host 127.0.0.1 --port 8765
```

如果工作台返回 503，运行 `npm run build --prefix packages/wqb-agent-ui`，再刷新页面。

Node 20、23、25、26 或其他未验证版本会被 doctor 明确阻断，并提供 Node.js 官方下载地址。Node 不是 runtime 档位的依赖。

## 配置真实服务

离线演示通过后，再按 [研究政策](RESEARCH_POLICY.md) 和 [LLM Provider](LLM_PROVIDERS.md) 配置。WQB 邮箱、密码和模型密钥只能写入 `.env`。首次安装不得把以下 capability 改为 `1`：

```env
WQB_LIVE_SIMULATION_CAPABILITY=0
WQB_LIVE_SUBMIT_CAPABILITY=0
```

真实模拟和提交属于后续显式授权，不属于 onboarding。
