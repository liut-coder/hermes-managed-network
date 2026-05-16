$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$defaultEnv = Join-Path $scriptDir 'node.env.ps1'
$envFile = if ($env:HMN_ENV_FILE) { $env:HMN_ENV_FILE } else { $defaultEnv }

if (-not (Test-Path -LiteralPath $envFile)) {
  throw "missing node env: $envFile"
}

. $envFile

if (-not $env:HERMES_MASTER_URL) { throw 'missing HERMES_MASTER_URL' }
if (-not $env:HERMES_NODE_ID) { throw 'missing HERMES_NODE_ID' }
if (-not $env:HERMES_NODE_FINGERPRINT) { throw 'missing HERMES_NODE_FINGERPRINT' }

function Test-Truthy([string]$value, [string]$default = '0') {
  $candidate = if ([string]::IsNullOrWhiteSpace($value)) { $default } else { $value }
  return @('1', 'true', 'yes', 'on') -contains $candidate.ToLower()
}

function Get-ProtocolVersion {
  try {
    $version = Invoke-RestMethod -Method Get -Uri (($env:HERMES_MASTER_URL.TrimEnd('/')) + '/api/v1/version')
    if ($version.worker_protocol_version) {
      return [string]$version.worker_protocol_version
    }
  } catch {
  }
  return '0.1'
}

function Invoke-HmnJsonPost {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Body
  )

  $headers = @{ 'Content-Type' = 'application/json' }
  $lastError = $null
  foreach ($master in $script:masterUrls) {
    try {
      return Invoke-RestMethod -Method Post -Uri ($master + $Path) -Headers $headers -Body $Body
    } catch {
      $lastError = $_
    }
  }
  if ($lastError) {
    throw $lastError
  }
  throw "request failed: $Path"
}

function Get-NodeFingerprintDerived {
  $hostnameCurrent = if ($env:HERMES_NODE_NAME) { $env:HERMES_NODE_NAME } else { $env:COMPUTERNAME }
  $machineGuid = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
  $fingerprintSeed = "$hostnameCurrent|$machineGuid"
  $fingerprintBytes = [System.Text.Encoding]::UTF8.GetBytes($fingerprintSeed)
  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  try {
    return 'sha256:' + [System.BitConverter]::ToString($sha256.ComputeHash($fingerprintBytes)).Replace('-', '').ToLower()
  } finally {
    $sha256.Dispose()
  }
}

function Get-TaskSignature {
  param(
    [Parameter(Mandatory = $true)][string]$TaskId,
    [Parameter(Mandatory = $true)][string]$Risk,
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string]$Fingerprint
  )

  $keyBytes = [System.Text.Encoding]::UTF8.GetBytes($Fingerprint)
  $message = "$TaskId`n$Risk`n$Command"
  $messageBytes = [System.Text.Encoding]::UTF8.GetBytes($message)
  $hmac = [System.Security.Cryptography.HMACSHA256]::new($keyBytes)
  try {
    $digest = [System.BitConverter]::ToString($hmac.ComputeHash($messageBytes)).Replace('-', '').ToLower()
    return 'hmac-sha256:' + $digest
  } finally {
    $hmac.Dispose()
  }
}

function Submit-TaskResult {
  param(
    [Parameter(Mandatory = $true)][string]$TaskId,
    [Parameter(Mandatory = $true)][int]$ExitCode,
    [string]$Stdout = '',
    [string]$Stderr = ''
  )

  $body = @{
    fingerprint = $env:HERMES_NODE_FINGERPRINT
    exit_code = $ExitCode
    stdout = $Stdout
    stderr = $Stderr
  } | ConvertTo-Json -Depth 6
  Invoke-HmnJsonPost -Path ('/api/v1/tasks/' + $TaskId + '/result') -Body $body | Out-Null
}

function Rotate-FingerprintFromTask {
  param(
    [Parameter(Mandatory = $true)][string]$TaskId,
    [Parameter(Mandatory = $true)][string]$NewFingerprint
  )

  $oldFingerprint = $env:HERMES_NODE_FINGERPRINT
  $body = @{
    fingerprint = $oldFingerprint
    new_fingerprint = $NewFingerprint
  } | ConvertTo-Json -Depth 4
  Invoke-HmnJsonPost -Path ('/api/v1/nodes/' + $env:HERMES_NODE_ID + '/rotate-fingerprint') -Body $body | Out-Null
  $env:HERMES_NODE_FINGERPRINT = $NewFingerprint
  $envLines = @(
    "$env:HERMES_MASTER_URL=$($env:HERMES_MASTER_URL)"
    "$env:HMN_MASTER_URLS=$($env:HMN_MASTER_URLS)"
    "$env:HERMES_NODE_ID=$($env:HERMES_NODE_ID)"
    "$env:HERMES_NODE_FINGERPRINT=$NewFingerprint"
    "$env:HMN_ENABLE_EXEC=$($env:HMN_ENABLE_EXEC)"
    "$env:HMN_WORKER_MODE=$($env:HMN_WORKER_MODE)"
    "$env:HMN_BEACON_ONLY=$($env:HMN_BEACON_ONLY)"
    "$env:HERMES_AUTO_CONFIRM=$($env:HERMES_AUTO_CONFIRM)"
  )
  Set-Content -Path $envFile -Value $envLines -Encoding UTF8
  Submit-TaskResult -TaskId $TaskId -ExitCode 0 -Stdout 'fingerprint rotated'
}

$masterUrls = @()
if ($env:HMN_MASTER_URLS) {
  $masterUrls = $env:HMN_MASTER_URLS.Split(',') | ForEach-Object { $_.Trim().TrimEnd('/') } | Where-Object { $_ }
}
if (-not $masterUrls -or $masterUrls.Count -eq 0) {
  $masterUrls = @($env:HERMES_MASTER_URL.TrimEnd('/'))
}

$beaconOnly = Test-Truthy $env:HMN_BEACON_ONLY '1'
$enableExec = Test-Truthy $env:HMN_ENABLE_EXEC '0'
$workerMode = if ($beaconOnly) { 'beacon' } else { 'worker' }
$workerVersion = if ($beaconOnly) { 'windows-beacon' } else { 'windows-worker' }
$taskPolicy = if ($beaconOnly) { 'heartbeat-only' } else { 'poll-tasks' }
$protocolVersion = Get-ProtocolVersion
$hostname = if ($env:HERMES_NODE_NAME) { $env:HERMES_NODE_NAME } else { $env:COMPUTERNAME }
$addresses = @(
  Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -and $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' } |
    Select-Object -ExpandProperty IPAddress
)
$fingerprintDerived = Get-NodeFingerprintDerived
if ($env:HERMES_NODE_FINGERPRINT -ne $fingerprintDerived) {
  $env:HERMES_NODE_FINGERPRINT = $fingerprintDerived
}

$facts = @{
  worker_protocol_version = $protocolVersion
  worker_version = $workerVersion
  worker_mode = $workerMode
  task_policy = $taskPolicy
  can_poll_tasks = (-not $beaconOnly)
  exec_enabled = ($enableExec -and -not $beaconOnly)
  hostname = $hostname
  addresses = $addresses
  capabilities = @{
    os_family = 'windows'
    has_powershell = $true
    has_sh = $false
    has_python3 = $false
    has_curl = [bool](Get-Command curl.exe -ErrorAction SilentlyContinue)
    has_wget = [bool](Get-Command wget.exe -ErrorAction SilentlyContinue)
    has_systemctl = $false
    has_openrc = $false
    has_procd = $false
    has_launchctl = $false
    has_crond = $false
    writable_etc = $false
    writable_tmp = $true
  }
  os_release = [System.Environment]::OSVersion.VersionString
}

$payload = @{
  fingerprint = $env:HERMES_NODE_FINGERPRINT
  status = 'ok'
  facts = $facts
} | ConvertTo-Json -Depth 6

Invoke-HmnJsonPost -Path ('/api/v1/nodes/' + $env:HERMES_NODE_ID + '/heartbeat') -Body $payload | Out-Null

if (-not $beaconOnly) {
  $taskRequest = @{
    fingerprint = $env:HERMES_NODE_FINGERPRINT
    worker_protocol_version = $protocolVersion
  } | ConvertTo-Json -Depth 3
  $taskResponse = Invoke-HmnJsonPost -Path ('/api/v1/nodes/' + $env:HERMES_NODE_ID + '/tasks/next') -Body $taskRequest
  if ($taskResponse -and $taskResponse.task_id) {
    $taskId = [string]$taskResponse.task_id
    $command = [string]$taskResponse.command
    $risk = if ($null -ne $taskResponse.risk) { [string]$taskResponse.risk } else { 'unknown' }
    $signature = if ($null -ne $taskResponse.signature) { [string]$taskResponse.signature } else { '' }
    if ($signature) {
      $expectedSignature = Get-TaskSignature -TaskId $taskId -Risk $risk -Command $command -Fingerprint $env:HERMES_NODE_FINGERPRINT
      if ($expectedSignature -ne $signature) {
        Submit-TaskResult -TaskId $taskId -ExitCode 127 -Stderr 'task signature mismatch'
        exit 0
      }
    }
    if ($command.StartsWith('hmn:rotate-fingerprint ')) {
      Rotate-FingerprintFromTask -TaskId $taskId -NewFingerprint $command.Substring('hmn:rotate-fingerprint '.Length)
      exit 0
    }
    if (-not $enableExec) {
      Submit-TaskResult -TaskId $taskId -ExitCode 126 -Stderr 'execution disabled; set HMN_ENABLE_EXEC=1'
      exit 0
    }
    $stderrText = ''
    try {
      $output = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $command 2>&1
      $exitCode = $LASTEXITCODE
      if ($null -eq $exitCode) { $exitCode = 0 }
      $stdoutText = (($output | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) | ForEach-Object { "$($_)" }) -join "`n"
      $stderrText = (($output | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] }) | ForEach-Object { "$($_)" }) -join "`n"
      Submit-TaskResult -TaskId $taskId -ExitCode ([int]$exitCode) -Stdout $stdoutText -Stderr $stderrText
    } catch {
      $stderrText = $_ | Out-String
      Submit-TaskResult -TaskId $taskId -ExitCode 1 -Stderr $stderrText.Trim()
    }
  }
}
