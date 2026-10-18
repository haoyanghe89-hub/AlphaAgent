param(
    [string]$BackupRoot,
    [ValidateRange(0,23)][int]$BackupHour = 21,
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$account = $identity.Name
if ($account -match 'CodexSandbox') { throw 'Install using the signed-in Windows account, not a sandbox account.' }
if (-not $BackupRoot) { $BackupRoot = Join-Path $projectRoot 'backups\alphaagent' }
$BackupRoot = [System.IO.Path]::GetFullPath($BackupRoot)
$systemShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
$backupScript = Join-Path $projectRoot 'scripts\backup_alphaagent.py'
$startupScript = Join-Path $projectRoot 'start-alphaagent.ps1'
$backgroundScript = Join-Path $projectRoot 'scripts\start_alphaagent_background.py'
foreach ($required in @($systemShell,$pythonWindowless,$backupScript,$startupScript,$backgroundScript)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required file missing: $required" }
}
$config = Get-Content -LiteralPath (Join-Path $projectRoot 'alphaagent.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$activeData = if ([System.IO.Path]::IsPathRooted($config.data)) { [System.IO.Path]::GetFullPath($config.data) } else { [System.IO.Path]::GetFullPath((Join-Path $projectRoot $config.data)) }
if (-not (Test-Path -LiteralPath (Join-Path $activeData 'metadata.sqlite') -PathType Leaf)) { throw 'Active database missing; refusing to install against empty history.' }
if ($BackupRoot.TrimEnd('\') -eq $activeData.TrimEnd('\') -or $BackupRoot.StartsWith($activeData.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase) -or $activeData.StartsWith($BackupRoot.TrimEnd('\') + '\',[StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup root and active data must be separate.'
}
$marker = "AlphaAgent managed local operation | $projectRoot"
$names = @('AlphaAgent-Service','AlphaAgent-Backup')
foreach ($name in $names) {
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing -and $existing.Description -ne $marker) { throw "An unrelated task already uses $name; it was not changed." }
}

# This directory holds private account history and a local configuration copy.
New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
$acl = Get-Acl -LiteralPath $BackupRoot
$allowedSids = @($identity.User.Value,'S-1-5-18','S-1-5-32-544')
$rules = @($acl.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]))
$isPrivate = $acl.AreAccessRulesProtected -and $rules.Count -eq 3
foreach ($rule in $rules) {
    if ($rule.IdentityReference.Value -notin $allowedSids -or $rule.AccessControlType -ne 'Allow' -or $rule.FileSystemRights -ne 'FullControl') { $isPrivate = $false }
}
if (-not $isPrivate) {
    # Modify only the existing DACL; leave auditing and owner privileges unchanged.
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($rule in $rules) { $acl.RemoveAccessRuleSpecific($rule) }
    foreach ($sid in $allowedSids) {
        $principalSid = New-Object System.Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($principalSid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $BackupRoot -AclObject $acl
}

$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
$serviceLogon = New-ScheduledTaskTrigger -AtLogOn -User $account
$serviceLogon.Delay = 'PT15S'
$serviceDaily = New-ScheduledTaskTrigger -Daily -At '09:00'
$backupLogon = New-ScheduledTaskTrigger -AtLogOn -User $account
$backupLogon.Delay = 'PT2M'
$backupDaily = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.AddHours($BackupHour))
$serviceSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2)
$serviceAction = New-ScheduledTaskAction -Execute $pythonWindowless -Argument ('"' + $backgroundScript + '"') -WorkingDirectory $projectRoot
$backupAction = New-ScheduledTaskAction -Execute $pythonWindowless -Argument ('"' + $backupScript + '" --scheduled --hour ' + $BackupHour + ' --backup-root "' + $BackupRoot + '"') -WorkingDirectory $projectRoot
$serviceTask = New-ScheduledTask -Action $serviceAction -Trigger @($serviceLogon,$serviceDaily) -Settings $serviceSettings -Principal $principal -Description $marker
$backupTask = New-ScheduledTask -Action $backupAction -Trigger @($backupLogon,$backupDaily) -Settings $backupSettings -Principal $principal -Description $marker
Register-ScheduledTask -TaskName $names[0] -InputObject $serviceTask -Force | Out-Null
Register-ScheduledTask -TaskName $names[1] -InputObject $backupTask -Force | Out-Null
$logRoot = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
[pscustomobject]@{ InstalledAt=(Get-Date).ToString('o'); User=$account; ServiceTask=$names[0]; BackupTask=$names[1]; BackupRoot=$BackupRoot; BackupHour=$BackupHour; ActiveData=$activeData } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot 'alphaagent-local-tasks.json') -Encoding UTF8
if ($StartNow) { foreach ($name in $names) { Start-ScheduledTask -TaskName $name } }
[pscustomobject]@{ ServiceTask=$names[0]; BackupTask=$names[1]; BackupAt=('{0:00}:00' -f $BackupHour); BackupRoot=$BackupRoot; ActiveData=$activeData; Started=[bool]$StartNow } | ConvertTo-Json
