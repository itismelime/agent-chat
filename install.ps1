# Install bullpen for the current Windows user. Safe to rerun.
#   .\install.ps1                        install, using the Ollama at http://127.0.0.1:11434
#   .\install.ps1 -OllamaUrl <url>       use another Ollama (http://127.0.0.1:<port>)
#   .\install.ps1 -Uninstall             remove it; chats stay in %LOCALAPPDATA%\bullpen
# Starting agents from the page needs tmux, so on Windows it is off; run install.sh in WSL for it.
param([switch]$Uninstall, [string]$OllamaUrl = "http://127.0.0.1:11434")
$ErrorActionPreference = "Stop"

$here = $PSScriptRoot
$chat = Join-Path $here "bin\bullpen"
$data = Join-Path $env:LOCALAPPDATA "bullpen"
$bin = Join-Path $data "bin"
$task = "bullpen"
$port = if ($env:BULLPEN_PORT) { $env:BULLPEN_PORT } else { "8765" }

function Say($text) { Write-Host "  $text" }
function Has($name) { [bool](Get-Command $name -ErrorAction SilentlyContinue) }
function UserPath { [Environment]::GetEnvironmentVariable("Path", "User") }
function SetUserPath($value) { [Environment]::SetEnvironmentVariable("Path", $value, "User") }
function Run($exe) {  # a native command whose failure is fine (e.g. removing what is not there)
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $exe @args *> $null
    $ErrorActionPreference = $old
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$chat*serve*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Remove-Item (Join-Path $bin "bullpen.cmd") -ErrorAction SilentlyContinue
    SetUserPath ((UserPath) -split ";" | Where-Object { $_ -and $_ -ne $bin }) -join ";"
    if (Has claude) { Run claude mcp remove --scope user bullpen }
    if (Has codex) { Run codex mcp remove bullpen }
    Write-Host "bullpen removed; chats kept in $data"
    exit 0
}

Write-Host "Installing bullpen from $here"

# from agent-chat, this project's name before bullpen: its task, command and MCP entry go,
# and its chats and settings move over
if (Get-ScheduledTask -TaskName "agent-chat" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "agent-chat" -Confirm:$false
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
        Where-Object { $_.CommandLine -like "*$(Join-Path $here 'bin\chat')*serve*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Say "removed the old agent-chat task"
}
foreach ($base in $env:LOCALAPPDATA, $env:APPDATA) {
    $old = Join-Path $base "agent-chat"; $new = Join-Path $base "bullpen"
    if ((Test-Path $old) -and -not (Test-Path $new)) { Move-Item $old $new; Say "moved $old to $new" }
}
Remove-Item (Join-Path $bin "chat.cmd") -ErrorAction SilentlyContinue
SetUserPath ((UserPath) -split ";" | Where-Object { $_ -and $_ -ne (Join-Path $env:LOCALAPPDATA "agent-chat\bin") }) -join ";"
if (Has claude) { Run claude mcp remove --scope user agent-chat }
if (Has codex) { Run codex mcp remove agent-chat }

# Python 3.9+, through the py launcher when there is one
$python = $null
foreach ($try in @(@("py", "-3"), @("python"))) {
    if (Has $try[0]) {
        $exe = & $try[0] @($try[1..9] | Where-Object { $_ }) -c "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $python = $exe.Trim(); break }
    }
}
if (-not $python) { throw "bullpen needs Python 3.9 or newer (https://www.python.org/downloads/)" }
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Say "python: $python"

# bullpen.cmd on the user's PATH
New-Item -ItemType Directory -Force $bin | Out-Null
Set-Content -Encoding ascii (Join-Path $bin "bullpen.cmd") "@`"$python`" `"$chat`" %*"
if (((UserPath) -split ";") -notcontains $bin) {
    SetUserPath ((@((UserPath) -split ";" | Where-Object { $_ }) + $bin) -join ";")
    Say "added $bin to your PATH (new terminals see it)"
}
$env:Path = "$env:Path;$bin"

# Ollama: bullpen does not bundle one on Windows; use the Windows app's (or another) one
& $python $bullpen config ollama-url $OllamaUrl
if ($LASTEXITCODE -ne 0) { exit 2 }
Say "Ollama: $OllamaUrl (get it from https://ollama.com/download/windows)"

# the service: a task at logon, started now; pythonw keeps it without a console window
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$chat`" serve" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*$chat*serve*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Settings $settings `
    -Description "bullpen service (127.0.0.1:$port)" | Out-Null
Start-ScheduledTask -TaskName $task
$up = $false
foreach ($i in 1..30) {
    try {
        Invoke-WebRequest -UseBasicParsing -Headers @{ "X-Bullpen" = "1" } `
            "http://127.0.0.1:$port/api/projects" | Out-Null
        $up = $true; break
    } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $up) { throw "bullpen did not start on port $port (is something else using it?); run: bullpen serve" }
Say "service bullpen running on http://127.0.0.1:$port (Task Scheduler: $task)"

# the chat tools for every Claude Code and Codex session; re-registered in case the clone moved
foreach ($tool in "claude", "codex") {
    if (-not (Has $tool)) { Say "${tool} not found: skipped"; continue }
    if ($tool -eq "claude") {
        Run claude mcp remove --scope user bullpen
        & claude mcp add --scope user bullpen -- $python $bullpen mcp | Out-Null
    } else {
        Run codex mcp remove bullpen
        & codex mcp add bullpen -- $python $bullpen mcp | Out-Null
    }
    Say "${tool}: registered bullpen for all your sessions"
}
Write-Host "Done. Open http://127.0.0.1:$port and add a project, or run: bullpen add <folder>"
