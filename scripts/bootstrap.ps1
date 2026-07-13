[CmdletBinding()]
param(
    [ValidateSet("runtime", "full")]
    [string]$Profile = "runtime",
    [switch]$InstallUv
)

$ErrorActionPreference = "Stop"
$UvVersion = "0.11.27"
$PythonVersion = "3.12"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-OnboardingError {
    param(
        [string]$Code,
        [string]$Message,
        [string[]]$Actions
    )
    $payload = [ordered]@{
        ok = $false
        code = $Code
        message = $Message
        actions = $Actions
    }
    Write-Error ("ONBOARDING_ERROR " + ($payload | ConvertTo-Json -Compress)) -ErrorAction Continue
}

function Resolve-UvExecutable {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path $HOME ".local\bin\uv.exe"),
        (Join-Path $HOME ".cargo\bin\uv.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        Write-OnboardingError "command_failed" "$Executable failed with exit code $LASTEXITCODE." @(
            "Review the command output above.",
            "Run: uv run python -m scripts.dev doctor --profile $Profile --json"
        )
        exit $LASTEXITCODE
    }
}

$uv = Resolve-UvExecutable
if (-not $uv) {
    if (-not $InstallUv) {
        Write-OnboardingError "uv_missing" "uv is required, but it is not installed or not on PATH." @(
            "Inspect the installer: powershell -c `"irm https://astral.sh/uv/$UvVersion/install.ps1 | more`"",
            "Install and continue: powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -InstallUv -Profile $Profile",
            "Official documentation: https://docs.astral.sh/uv/getting-started/installation/"
        )
        exit 2
    }
    $installer = Join-Path ([System.IO.Path]::GetTempPath()) "wqb-agent-lab-uv-install.ps1"
    try {
        Invoke-WebRequest -UseBasicParsing "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { throw "uv installer exited with code $LASTEXITCODE" }
    }
    finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    $env:Path = "$(Join-Path $HOME '.local\bin');$(Join-Path $HOME '.cargo\bin');$env:Path"
    $uv = Resolve-UvExecutable
}

if (-not $uv) {
    Write-OnboardingError "uv_not_resolved" "uv installation completed but uv.exe could not be resolved." @(
        "Open a new PowerShell window and rerun this script without -InstallUv.",
        "Run: Get-Command uv"
    )
    exit 2
}

$syncArguments = @("sync", "--python", $PythonVersion, "--frozen")
if ($Profile -eq "full") {
    $syncArguments += @("--extra", "dev", "--extra", "mcp")
}
Invoke-Checked $uv $syncArguments

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item ".env.example" ".env"
}
$workflowDirectory = ".local\research\workflows"
New-Item -ItemType Directory -Force $workflowDirectory | Out-Null
$workflowConfig = Join-Path $workflowDirectory "production.json"
if (-not (Test-Path -LiteralPath $workflowConfig)) {
    Copy-Item "configs\examples\production-workflow.example.json" $workflowConfig
}

if ($Profile -eq "full") {
    $node = Get-Command node -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
    if (-not $node -or -not $npm) {
        Write-OnboardingError "node_missing" "Full profile requires Node.js 22.12+ or 24 LTS with npm." @(
            "Install an LTS release from https://nodejs.org/en/download",
            "Open a new terminal and rerun: powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -Profile full"
        )
        exit 2
    }
    $supported = & $node.Source -e "const [a,b]=process.versions.node.split('.').map(Number); process.exit(((a===22&&b>=12)||a===24)?0:2)"
    if ($LASTEXITCODE -ne 0) {
        $detected = & $node.Source --version
        Write-OnboardingError "node_unsupported" "Detected $detected; full profile supports Node.js 22.12+ or 24 LTS." @(
            "Install a supported LTS release from https://nodejs.org/en/download",
            "Open a new terminal and rerun this script."
        )
        exit 2
    }
    $directNodeVersion = (& $node.Source --version).TrimStart("v")
    $npmRuntime = (& $npm.Source version --json | ConvertFrom-Json).node
    if ($directNodeVersion -ne $npmRuntime) {
        Write-OnboardingError "npm_node_runtime_mismatch" "node reports $directNodeVersion but npm runs under Node $npmRuntime." @(
            "Remove stale Node/npm entries from PATH.",
            "Open a new terminal and confirm: node --version; npm version --json",
            "Rerun this script after both commands resolve to the same installation."
        )
        exit 2
    }
    Invoke-Checked $npm.Source @("ci", "--prefix", "packages/wqb-agent-mcp")
    Invoke-Checked $npm.Source @("ci", "--prefix", "packages/wqb-agent-ui")
}

Invoke-Checked $uv @(
    "run", "--python", $PythonVersion, "python", "-m", "scripts.dev", "doctor",
    "--profile", $Profile, "--json"
)
