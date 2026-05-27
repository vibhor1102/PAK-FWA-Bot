param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$PidFile,

    [Parameter(Mandatory = $true)]
    [string]$LogFile
)

$ErrorActionPreference = "Stop"
$host.UI.RawUI.WindowTitle = "PAK Originals Testing Bot - Codex Hook"

Set-Location -LiteralPath $RepoRoot

Write-Host ""
Write-Host "PAK Originals Testing Bot" -ForegroundColor Cyan
Write-Host "Started by Codex Stop hook. Close this window when you are done testing." -ForegroundColor Yellow
Write-Host "Repo: $RepoRoot"
Write-Host "Log:  $LogFile"
Write-Host ""

$state = [ordered]@{
    window_pid = $PID
    python_pid = $null
    repo_root = $RepoRoot
    log_file = $LogFile
    started_at = (Get-Date).ToString("o")
}
$state | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

Write-Host "Restarting after every Codex response will close this process and open a new one."
Write-Host ""
Write-Host "Output is shown here and written to the log file above. Press Ctrl+C or close this window to stop it manually."
Write-Host ""

try {
    $env:PAK_ORIGINALS_TESTING_BOT = "1"
    $env:PYTHONUNBUFFERED = "1"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & python -m bot 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Write-Host $line
            Add-Content -LiteralPath $LogFile -Value $line
        }
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}
finally {
    if (Test-Path -LiteralPath $PidFile) {
        $current = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
        if ($current.window_pid -eq $PID) {
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host ""
    Write-Host "Testing bot process exited." -ForegroundColor Yellow
}
