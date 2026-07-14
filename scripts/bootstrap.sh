#!/usr/bin/env sh
set -eu

profile="runtime"
install_uv="0"
uv_version="0.11.27"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      profile="${2:-}"
      shift 2
      ;;
    --install-uv)
      install_uv="1"
      shift
      ;;
    *)
      printf '%s\n' "ONBOARDING_ERROR {\"code\":\"invalid_argument\",\"message\":\"Unknown argument: $1\"}" >&2
      exit 2
      ;;
  esac
done

if [ "$profile" != "runtime" ] && [ "$profile" != "full" ]; then
  printf '%s\n' 'ONBOARDING_ERROR {"code":"invalid_profile","message":"Profile must be runtime or full."}' >&2
  exit 2
fi

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  if [ "$install_uv" != "1" ]; then
    printf '%s\n' "ONBOARDING_ERROR {\"code\":\"uv_missing\",\"message\":\"Install uv, then rerun.\",\"action\":\"sh scripts/bootstrap.sh --install-uv --profile $profile\",\"docs_url\":\"https://docs.astral.sh/uv/getting-started/installation/\"}" >&2
    exit 2
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf '%s\n' 'ONBOARDING_ERROR {"code":"curl_missing","message":"curl is required for automatic uv installation. Use the official manual installation instructions.","docs_url":"https://docs.astral.sh/uv/getting-started/installation/"}' >&2
    exit 2
  fi
  curl -LsSf "https://astral.sh/uv/$uv_version/install.sh" | sh
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export PATH
fi

if [ "$profile" = "full" ]; then
  uv sync --python 3.12 --frozen --extra dev --extra mcp
else
  uv sync --python 3.12 --frozen
fi

[ -f .env ] || cp .env.example .env
mkdir -p .local/research/workflows
[ -f .local/research/workflows/production.json ] || \
  cp configs/examples/production-workflow.example.json .local/research/workflows/production.json

if [ "$profile" = "full" ]; then
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    printf '%s\n' 'ONBOARDING_ERROR {"code":"node_missing","message":"Full profile requires Node.js 22.12+ or 24 LTS with npm.","docs_url":"https://nodejs.org/en/download"}' >&2
    exit 2
  fi
  if ! node -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit(((a===22&&b>=12)||a===24)?0:2)"; then
    printf '%s\n' "ONBOARDING_ERROR {\"code\":\"node_unsupported\",\"detected\":\"$(node --version)\",\"expected\":\"Node.js 22.12+ or 24 LTS\",\"docs_url\":\"https://nodejs.org/en/download\"}" >&2
    exit 2
  fi
  direct_node="$(node --version | sed 's/^v//')"
  npm_node="$(npm version --json | node -e "let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>console.log(JSON.parse(s).node||''))")"
  if [ "$direct_node" != "$npm_node" ]; then
    printf '%s\n' "ONBOARDING_ERROR {\"code\":\"npm_node_runtime_mismatch\",\"detected_node\":\"$direct_node\",\"detected_npm_node\":\"$npm_node\",\"message\":\"Remove stale Node/npm entries from PATH and open a new terminal.\"}" >&2
    exit 2
  fi
  npm ci --prefix packages/wqb-agent-mcp
  npm ci --prefix packages/wqb-agent-ui
  npm run build --prefix packages/wqb-agent-ui
fi

uv run --python 3.12 python -m scripts.dev doctor --profile "$profile" --json
