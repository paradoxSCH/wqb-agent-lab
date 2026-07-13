# LLM Provider 配置

WQB Agent Lab 使用统一的 `llm_provider` 配置连接模型。公开示例默认关闭 LLM；用户可以先完成研究政策配置和确定性流程，再按需启用模型。

## 快速检查

以下命令应从项目根目录运行。CLI 会从当前 workspace 向上查找 `.env`；已由 shell、CI 或进程管理器导出的环境变量优先于 `.env` 中的同名值，不会被覆盖。

Windows PowerShell：

```powershell
.\.venv\Scripts\wqb-engine.exe llm.validate --config .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe llm.show --config .local\research\workflows\production.json
.\.venv\Scripts\wqb-engine.exe llm.probe --config .local\research\workflows\production.json
```

POSIX shell：

```bash
./.venv/bin/wqb-engine llm.validate --config .local/research/workflows/production.json
./.venv/bin/wqb-engine llm.show --config .local/research/workflows/production.json
./.venv/bin/wqb-engine llm.probe --config .local/research/workflows/production.json
```

`llm.validate` 和 `llm.show` 不访问网络、不启动 CLI，也不要求网络 Provider 的密钥。`llm.show` 输出脱敏后的有效配置和迁移警告。`llm.probe` 是显式连通性检查：它构造一次 Provider，并发送一次不包含 alpha、WQB 账号、字段或记忆内容的最小请求。`disabled` 模式不能执行 probe。

## Disabled

这是公开示例和首次启动的默认值。规划与候选生成保持确定性回退。

```json
{
  "llm_provider": {
    "provider": "disabled"
  }
}
```

## OpenAI-compatible

适用于 OpenAI Chat Completions 兼容端点，例如由服务商或本地网关提供的 `/v1/chat/completions` API。不同服务商的模型名和 URL 请以其文档为准。

```json
{
  "llm_provider": {
    "provider": "openai_compatible",
    "display_name": "Research model",
    "model": "model-name",
    "api_key_env": "OPENAI_API_KEY",
    "base_url": "https://api.openai.com/v1",
    "timeout_seconds": 180,
    "temperature": 0.2,
    "max_tokens": 4096,
    "response_format": "json"
  }
}
```

在本地 `.env` 中填写 `OPENAI_API_KEY=`。使用 DeepSeek、Moonshot 或其他兼容服务时，替换 `model`、`base_url` 和 `api_key_env` 即可；配置文件中不得出现密钥值。

## Anthropic

```json
{
  "llm_provider": {
    "provider": "anthropic",
    "model": "claude-model-name",
    "api_key_env": "ANTHROPIC_API_KEY",
    "base_url": "https://api.anthropic.com",
    "response_format": "json"
  }
}
```

在本地 `.env` 中填写 `ANTHROPIC_API_KEY=`。JSON 模式会增加结构化输出约束，并校验响应确实是 JSON object 或 array。

## Gemini

```json
{
  "llm_provider": {
    "provider": "gemini",
    "model": "gemini-model-name",
    "api_key_env": "GEMINI_API_KEY",
    "base_url": "https://generativelanguage.googleapis.com",
    "response_format": "json"
  }
}
```

在本地 `.env` 中填写 `GEMINI_API_KEY=`。`model` 可以使用模型短名或 `models/...` 资源名。

## Ollama

Ollama 默认连接本机 loopback，不要求 API key。先确保 Ollama 服务和目标模型已在本机准备好。

```json
{
  "llm_provider": {
    "provider": "ollama",
    "model": "qwen3:8b",
    "base_url": "http://127.0.0.1:11434",
    "response_format": "json"
  }
}
```

只有显式执行 `llm.probe` 或启动需要模型的阶段时才会连接 Ollama。将 `base_url` 改成非 loopback 地址意味着用户主动选择了网络访问。

## CLI

CLI Provider 不调用 shell。命令必须是 JSON 字符串数组，首项必须是静态的原生可执行文件，不能含占位符。支持 `{prompt}`、`{system_prompt}`、`{model}` 和 `{workspace_root}` 四种占位符；工作目录必须位于 workspace 内。

Windows 示例：

```json
{
  "llm_provider": {
    "provider": "cli",
    "model": "local-cli",
    "command": ["C:\\Tools\\model-cli.exe", "--prompt", "{prompt}"],
    "prompt_transport": "argument",
    "working_directory": ".",
    "timeout_seconds": 180,
    "response_format": "json"
  }
}
```

Windows 必须提供原生 `.exe` 可执行文件或解析到原生 executable 的命令。为保持 `shell=False` 的边界，`.cmd` 和 `.bat` 会被明确拒绝；很多 npm 全局命令只有 `.cmd` shim，不能直接作为这里的 executable。

POSIX 可把 command 首项改为 `/usr/local/bin/model-cli`。若使用 `"prompt_transport": "stdin"`，进程会从 stdin 接收包含 system prompt、user prompt、model 和 response format 的 JSON；此时命令不需要 prompt 占位符。子进程只继承受限环境变量和为当前 Provider 显式选择的凭证变量。

## 迁移旧配置

兼容期内的解析优先级固定为：

`llm_provider > llm_adapter > deepseek_v4_pro > kimi_cli > KIMI_* > disabled`

存在 `llm_provider` 时，所有旧 block 都会被忽略。否则 resolver 会只读转换旧配置并返回迁移警告，不会自动改写文件：

- `llm_adapter` 和 `deepseek_v4_pro` 映射为 `openai_compatible`。
- `kimi_cli` 映射为 `cli`。
- 仅在 workflow 没有任何 Provider block 时，`KIMI_API_KEY` 或 `MOONSHOT_API_KEY` 及相关 `KIMI_*` 环境变量才会参与兼容解析。

新配置应只保留一个 canonical `llm_provider` block。先运行 `llm.validate` 查看 migration warnings，再手动迁移并运行 `llm.show` 确认有效配置。

## 能力边界

LLM Provider 只提供模型协议适配、响应解析、错误归一化和脱敏。它不会自行决定重试、换模型、消耗模拟预算或提交 alpha。

- `llm.validate` 和 `llm.show` 不会访问 WQB，也不会访问模型网络。
- `llm.probe` 只访问选定模型，不会访问 WQB。
- 启用 LLM 不会设置 `WQB_LIVE_SIMULATION_CAPABILITY`。
- 启用 LLM 不会设置 `WQB_LIVE_SUBMIT_CAPABILITY`。
- 当前统一 Provider 不能自动生成生产 scan config，也没有让普通 WQB 用户获得完整无人值守能力。

真实 simulation 和 submit 必须由工作流层分别授权、审计和执行。Provider 返回限流、超时或内容错误时，也由 agent policy 决定重试、回退或停止。
