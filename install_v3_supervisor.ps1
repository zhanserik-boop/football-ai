$ErrorActionPreference = "Stop"

$taskName = "FootballAI-V3-Supervisor"
$projectDirectory = $PSScriptRoot
$supervisorScript = Join-Path $projectDirectory "v3_external_supervisor.py"

if (-not (Test-Path $supervisorScript)) {
    throw "Supervisor script not found: $supervisorScript"
}

$pythonCommand = Get-Command python -ErrorAction Stop
$pythonExecutable = $pythonCommand.Source
$pythonwExecutable = Join-Path (Split-Path $pythonExecutable) "pythonw.exe"
if (Test-Path $pythonwExecutable) {
    $taskExecutable = $pythonwExecutable
} else {
    $taskExecutable = $pythonExecutable
}

& $pythonExecutable $supervisorScript --check-only
if ($LASTEXITCODE -ne 0) {
    throw "Supervisor self-check failed with code $LASTEXITCODE"
}

$quotedScript = '"' + $supervisorScript + '"'
$action = New-ScheduledTaskAction `
    -Execute $taskExecutable `
    -Argument $quotedScript `
    -WorkingDirectory $projectDirectory

$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$currentUser = "$env:USERDOMAIN\$env:USERNAME"
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($repeatTrigger, $logonTrigger) `
    -Principal $principal `
    -Settings $settings `
    -Description "Independent Football AI V3 heartbeat supervisor"

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

Write-Host "Installed scheduled task: $taskName"
Write-Host "Interval: 5 minutes"
Write-Host "Project: $projectDirectory"
Write-Host "The task sends alerts only while V3 expects itself to be running."
