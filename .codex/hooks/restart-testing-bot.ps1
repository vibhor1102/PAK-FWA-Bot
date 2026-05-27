param()

$ErrorActionPreference = "Stop"

try {
    $hookInput = [Console]::In.ReadToEnd()
    if ($hookInput.Trim()) {
        $event = $hookInput | ConvertFrom-Json
        if ($event.hook_event_name -and $event.hook_event_name -ne "Stop") {
            '{"continue":true,"suppressOutput":true}'
            exit 0
        }
    }

    $repoRoot = (git rev-parse --show-toplevel).Trim()
    if (-not $repoRoot) {
        '{"continue":true,"suppressOutput":true}'
        exit 0
    }

    $runtimeDir = Join-Path $repoRoot ".codex\runtime"
    $logDir = Join-Path $runtimeDir "logs"
    New-Item -ItemType Directory -Force -Path $runtimeDir, $logDir | Out-Null

    $pidFile = Join-Path $runtimeDir "testing-bot.json"
    if (Test-Path -LiteralPath $pidFile) {
        $state = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        foreach ($oldPid in @($state.window_pid, $state.python_pid)) {
            if ($oldPid) {
                $process = Get-Process -Id ([int]$oldPid) -ErrorAction SilentlyContinue
                if ($process) {
                    Stop-Process -Id ([int]$oldPid) -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }

    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -match '^python(\.exe)?$' -and
            $_.CommandLine -match '(?i)\s-m\s+bot(\s|$)' -and
            $_.ExecutablePath -like '*Python314*'
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }

    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq 'powershell.exe' -and
            $_.CommandLine -match 'testing-bot-window\.ps1'
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $logFile = Join-Path $logDir "testing-bot-$timestamp.log"
    $launcher = Join-Path $repoRoot ".codex\hooks\testing-bot-window.ps1"

    $window = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoExit",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$launcher`"",
            "-RepoRoot", "`"$repoRoot`"",
            "-PidFile", "`"$pidFile`"",
            "-LogFile", "`"$logFile`""
        ) `
        -WorkingDirectory $repoRoot `
        -PassThru

    $initialState = [ordered]@{
        window_pid = $window.Id
        python_pid = $null
        repo_root = $repoRoot
        log_file = $logFile
        started_at = (Get-Date).ToString("o")
    }
    $initialState | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8

    '{"continue":true,"suppressOutput":true}'
    exit 0
}
catch {
    $message = ($_ | Out-String).Trim().Replace("\", "\\").Replace('"', '\"')
    "{`"continue`":true,`"systemMessage`":`"Testing bot hook failed: $message`",`"suppressOutput`":true}"
    exit 0
}
