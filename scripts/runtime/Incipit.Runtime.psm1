Set-StrictMode -Version Latest

$script:CoreInfrastructureServices = @(
    'postgres', 'minio', 'etcd', 'milvus', 'elasticsearch',
    'redis', 'temporal', 'temporal-ui'
)
$script:OptionalServices = @(
    'infinity', 'paddleocr', 'mineru', 'prometheus', 'grafana', 'phoenix'
)
$script:ApplicationServices = @('api', 'worker', 'web')

function New-IncipitCheck {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [bool]$Required,
        [Parameter(Mandatory)] [ValidateSet('PASS', 'WARN', 'FAIL')] [string]$State,
        [Parameter(Mandatory)] [string]$Detail
    )

    [pscustomobject][ordered]@{
        Name = $Name
        Required = $Required
        State = $State
        Detail = $Detail
    }
}

function Invoke-IncipitDocker {
    param([Parameter(Mandatory)] [string[]]$Arguments)

    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
    }
    return $output
}

function Test-IncipitCommand {
    param([Parameter(Mandatory)] [string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-IncipitPort {
    param(
        [Parameter(Mandatory)] [int]$Port,
        [string]$HostName = '127.0.0.1',
        [int]$TimeoutMilliseconds = 300
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync($HostName, $Port)
        return $connection.Wait($TimeoutMilliseconds) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-IncipitOverallState {
    param([AllowEmptyCollection()] [object[]]$Checks = @())

    if ($Checks.Count -eq 0) {
        return 'STOPPED'
    }
    if ($Checks | Where-Object { $_.Required -and $_.State -eq 'FAIL' }) {
        return 'UNHEALTHY'
    }
    if ($Checks | Where-Object { $_.State -in @('WARN', 'FAIL') }) {
        return 'DEGRADED'
    }
    return 'HEALTHY'
}

function Read-IncipitEnvFile {
    param([Parameter(Mandatory)] [string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
            continue
        }
        $parts = $trimmed.Split('=', 2)
        $values[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
    return $values
}

function Get-IncipitDockerDiskAvailableBytes {
    param([AllowNull()] [object]$DockerInfo)

    $candidate = $null
    if ($null -ne $DockerInfo -and $DockerInfo.PSObject.Properties.Name -contains 'DockerRootDir') {
        $candidate = [string]$DockerInfo.DockerRootDir
    }
    if (-not $candidate -or -not (Test-Path -LiteralPath $candidate)) {
        $candidate = $env:LOCALAPPDATA
    }
    if (-not $candidate) {
        return $null
    }

    try {
        $item = Get-Item -LiteralPath $candidate -ErrorAction Stop
        $drive = Get-PSDrive -Name $item.PSDrive.Name -ErrorAction Stop
        return [int64]$drive.Free
    }
    catch {
        return $null
    }
}

function Test-IncipitDirectoryHasContent {
    param([Parameter(Mandatory)] [string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return $false
    }
    return $null -ne (Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Invoke-IncipitDoctor {
    [CmdletBinding()]
    param(
        [switch]$Full,
        [switch]$Json,
        [string]$RootPath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path,
        [string]$FrontendPath
    )

    if (-not $FrontendPath) {
        $FrontendPath = Join-Path (Split-Path $RootPath -Parent) 'arc-knowledge-web'
    }

    $checks = [System.Collections.Generic.List[object]]::new()
    $composePath = Join-Path $RootPath 'docker-compose.yml'
    $exampleEnvPath = Join-Path $RootPath '.env.example'
    $envPath = Join-Path $RootPath '.env'
    $exampleEnv = Read-IncipitEnvFile -Path $exampleEnvPath
    $actualEnv = Read-IncipitEnvFile -Path $envPath
    $effectiveEnv = @{}
    foreach ($key in $exampleEnv.Keys) { $effectiveEnv[$key] = $exampleEnv[$key] }
    foreach ($key in $actualEnv.Keys) { $effectiveEnv[$key] = $actualEnv[$key] }

    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $checks.Add((New-IncipitCheck 'powershell' $true 'PASS' "PowerShell $($PSVersionTable.PSVersion)"))
    }
    else {
        $checks.Add((New-IncipitCheck 'powershell' $true 'FAIL' 'PowerShell 7 or newer is required'))
    }

    $dockerCommandAvailable = Test-IncipitCommand -Name 'docker'
    if ($dockerCommandAvailable) {
        $checks.Add((New-IncipitCheck 'docker-command' $true 'PASS' 'docker command found'))
    }
    else {
        $checks.Add((New-IncipitCheck 'docker-command' $true 'FAIL' 'docker command not found'))
    }

    if (Test-Path -LiteralPath $composePath -PathType Leaf) {
        $checks.Add((New-IncipitCheck 'compose-file' $true 'PASS' $composePath))
    }
    else {
        $checks.Add((New-IncipitCheck 'compose-file' $true 'FAIL' "missing $composePath"))
    }

    if (Test-Path -LiteralPath $FrontendPath -PathType Container) {
        $checks.Add((New-IncipitCheck 'frontend-directory' $true 'PASS' $FrontendPath))
    }
    else {
        $checks.Add((New-IncipitCheck 'frontend-directory' $true 'FAIL' "missing sibling frontend directory: $FrontendPath"))
    }

    if (Test-Path -LiteralPath $envPath -PathType Leaf) {
        $checks.Add((New-IncipitCheck 'env-file' $true 'PASS' $envPath))
        $requiredNames = @(
            'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY', 'JWT_SECRET_KEY',
            'LLM_BASE_URL', 'LLM_MODEL', 'EMBEDDING_BASE_URL',
            'EMBEDDING_MODEL', 'EMBEDDING_DIMENSIONS'
        )
        $missingNames = @($requiredNames | Where-Object {
            -not $actualEnv.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$actualEnv[$_])
        })
        if ($missingNames.Count -eq 0) {
            $checks.Add((New-IncipitCheck 'env-required-fields' $true 'PASS' 'all required fields are set'))
        }
        else {
            $checks.Add((New-IncipitCheck 'env-required-fields' $true 'FAIL' ("missing: " + ($missingNames -join ', '))))
        }
    }
    else {
        $checks.Add((New-IncipitCheck 'env-file' $false 'WARN' 'Missing .env. Run: Copy-Item .env.example .env'))
    }

    $dockerInfo = $null
    if ($dockerCommandAvailable) {
        try {
            $dockerInfoText = Invoke-IncipitDocker -Arguments @('info', '--format', '{{json .}}')
            $dockerInfo = (($dockerInfoText | Out-String).Trim() | ConvertFrom-Json -ErrorAction Stop)
            $checks.Add((New-IncipitCheck 'docker-engine' $true 'PASS' 'Docker engine is reachable'))
        }
        catch {
            $checks.Add((New-IncipitCheck 'docker-engine' $true 'FAIL' $_.Exception.Message))
        }

        try {
            $composeVersion = (Invoke-IncipitDocker -Arguments @('compose', 'version') | Out-String).Trim()
            $checks.Add((New-IncipitCheck 'docker-compose' $true 'PASS' $composeVersion))
        }
        catch {
            $checks.Add((New-IncipitCheck 'docker-compose' $true 'FAIL' $_.Exception.Message))
        }
    }
    else {
        $checks.Add((New-IncipitCheck 'docker-engine' $true 'FAIL' 'cannot test engine without docker command'))
        $checks.Add((New-IncipitCheck 'docker-compose' $true 'FAIL' 'cannot test Compose without docker command'))
    }

    if ($null -ne $dockerInfo -and $dockerInfo.PSObject.Properties.Name -contains 'MemTotal') {
        $memoryGiB = [math]::Round(([double]$dockerInfo.MemTotal / 1GB), 1)
        if ($memoryGiB -lt 8) {
            $checks.Add((New-IncipitCheck 'docker-memory' $true 'FAIL' "$memoryGiB GiB available; at least 8 GiB required"))
        }
        elseif ($memoryGiB -lt 12) {
            $checks.Add((New-IncipitCheck 'docker-memory' $false 'WARN' "$memoryGiB GiB available; 12 GiB recommended"))
        }
        else {
            $checks.Add((New-IncipitCheck 'docker-memory' $true 'PASS' "$memoryGiB GiB available"))
        }
    }

    $diskBytes = Get-IncipitDockerDiskAvailableBytes -DockerInfo $dockerInfo
    if ($null -eq $diskBytes) {
        $checks.Add((New-IncipitCheck 'docker-disk' $false 'WARN' 'unable to determine available Docker disk space'))
    }
    else {
        $diskGiB = [math]::Round(([double]$diskBytes / 1GB), 1)
        if ($diskGiB -lt 20) {
            $checks.Add((New-IncipitCheck 'docker-disk' $true 'FAIL' "$diskGiB GiB available; at least 20 GiB required"))
        }
        elseif ($diskGiB -lt 40) {
            $checks.Add((New-IncipitCheck 'docker-disk' $false 'WARN' "$diskGiB GiB available; 40 GiB recommended"))
        }
        else {
            $checks.Add((New-IncipitCheck 'docker-disk' $true 'PASS' "$diskGiB GiB available"))
        }
    }

    if ($dockerCommandAvailable -and (Test-Path -LiteralPath $composePath -PathType Leaf)) {
        try {
            $composeArguments = @('compose', '-f', $composePath)
            if (Test-Path -LiteralPath $envPath -PathType Leaf) {
                $composeArguments += @('--env-file', $envPath)
            }
            $null = Invoke-IncipitDocker -Arguments ($composeArguments + @('config', '--quiet'))
            $checks.Add((New-IncipitCheck 'compose-config' $true 'PASS' 'Compose configuration is valid'))
        }
        catch {
            $checks.Add((New-IncipitCheck 'compose-config' $true 'FAIL' $_.Exception.Message))
        }
    }

    $projectName = if ($actualEnv.ContainsKey('COMPOSE_PROJECT_NAME')) {
        $actualEnv['COMPOSE_PROJECT_NAME']
    }
    elseif ($env:COMPOSE_PROJECT_NAME) {
        $env:COMPOSE_PROJECT_NAME
    }
    else {
        Split-Path $RootPath -Leaf
    }
    $projectPortsText = ''
    if ($dockerCommandAvailable) {
        try {
            $projectPortsText = (Invoke-IncipitDocker -Arguments @(
                'ps', '--filter', "label=com.docker.compose.project=$projectName", '--format', '{{.Ports}}'
            ) | Out-String)
        }
        catch {
            $projectPortsText = ''
        }
    }
    $projectPorts = @([regex]::Matches($projectPortsText, '(?::|^)(?<port>\d+)->') | ForEach-Object {
        [int]$_.Groups['port'].Value
    })

    $portDefaults = [ordered]@{
        POSTGRES_HOST_PORT = 5432
        MINIO_API_HOST_PORT = 9000
        MINIO_CONSOLE_HOST_PORT = 9001
        MILVUS_HOST_PORT = 19530
        ELASTICSEARCH_HOST_PORT = 9200
        REDIS_HOST_PORT = 6379
        TEMPORAL_HOST_PORT = 7233
        TEMPORAL_UI_HOST_PORT = 8233
        API_HOST_PORT = 8000
        WEB_HOST_PORT = 3300
    }
    foreach ($entry in $portDefaults.GetEnumerator()) {
        $port = if ($effectiveEnv.ContainsKey($entry.Key)) { [int]$effectiveEnv[$entry.Key] } else { [int]$entry.Value }
        $occupied = Test-IncipitPort -Port $port
        if (-not $occupied) {
            $checks.Add((New-IncipitCheck "port:$port" $true 'PASS' 'available'))
        }
        elseif ($projectPorts -contains $port) {
            $checks.Add((New-IncipitCheck "port:$port" $true 'PASS' 'occupied by this project container'))
        }
        else {
            $checks.Add((New-IncipitCheck "port:$port" $true 'FAIL' 'occupied by another process'))
        }
    }

    foreach ($endpointName in @('LLM_BASE_URL', 'EMBEDDING_BASE_URL')) {
        if (-not $effectiveEnv.ContainsKey($endpointName)) {
            continue
        }
        $originalUrl = [string]$effectiveEnv[$endpointName]
        try {
            $uri = [uri]$originalUrl
            $probeHost = if ($uri.Host -eq 'host.docker.internal') { '127.0.0.1' } else { $uri.Host }
            $probePort = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq 'https') { 443 } else { 80 } } else { $uri.Port }
            if (Test-IncipitPort -Port $probePort -HostName $probeHost) {
                $checks.Add((New-IncipitCheck "model-endpoint:$endpointName" $false 'PASS' "$originalUrl is reachable"))
            }
            else {
                $checks.Add((New-IncipitCheck "model-endpoint:$endpointName" $false 'WARN' "$originalUrl is unreachable from the host probe"))
            }
        }
        catch {
            $checks.Add((New-IncipitCheck "model-endpoint:$endpointName" $false 'WARN' "invalid URL: $originalUrl"))
        }
    }

    if ($Full) {
        $cachePaths = [ordered]@{
            'infinity-model-cache' = (Join-Path $RootPath 'models/bge-reranker-v2-m3')
            'paddleocr-model-cache' = (Join-Path $RootPath '.cache/paddleocr')
            'huggingface-model-cache' = (Join-Path $RootPath '.cache/huggingface')
        }
        foreach ($entry in $cachePaths.GetEnumerator()) {
            if (Test-IncipitDirectoryHasContent -Path $entry.Value) {
                $checks.Add((New-IncipitCheck $entry.Key $false 'PASS' $entry.Value))
            }
            else {
                $checks.Add((New-IncipitCheck $entry.Key $false 'WARN' "missing or empty cache path: $($entry.Value)"))
            }
        }
    }

    $result = $checks.ToArray()
    if ($Json) {
        return ($result | ConvertTo-Json -Depth 5)
    }

    Write-Host (($result | Format-Table Name, Required, State, Detail -AutoSize | Out-String).TrimEnd())
    Write-Host "Overall: $(Get-IncipitOverallState -Checks $result)"
    return $result
}

function Get-IncipitProfileArguments {
    param([switch]$Full)

    if (-not $Full) {
        return @()
    }
    return @('--profile', 'rerank', '--profile', 'ocr', '--profile', 'observe')
}

function Get-IncipitOptionalServices {
    param([switch]$Full)

    if ($Full) {
        return 'rerank,ocr,observe'
    }
    return ''
}

function Get-IncipitConfiguredValue {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Default,
        [string]$RootPath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
    )

    $processValue = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue
    }

    $fileValues = Read-IncipitEnvFile -Path (Join-Path $RootPath '.env')
    if ($fileValues.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace([string]$fileValues[$Name])) {
        return [string]$fileValues[$Name]
    }
    return $Default
}

function Invoke-IncipitHttpRequest {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [int]$TimeoutSeconds = 5
    )

    return Invoke-WebRequest `
        -Uri $Uri `
        -UseBasicParsing `
        -TimeoutSec $TimeoutSeconds `
        -SkipHttpErrorCheck
}

function Wait-IncipitHttp {
    param(
        [Parameter(Mandatory)] [string]$Uri,
        [int]$TimeoutSeconds = 180,
        [int[]]$AcceptedStatus = @(200)
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-IncipitHttpRequest -Uri $Uri -TimeoutSeconds 5
            if ([int]$response.StatusCode -in $AcceptedStatus) {
                return $response
            }
        }
        catch {
            # A bounded wait treats connection failures as a not-ready state.
        }

        if ((Get-Date) -ge $deadline) {
            break
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Uri after $TimeoutSeconds seconds"
}

function ConvertFrom-IncipitComposePs {
    param([AllowEmptyString()] [object]$InputObject)

    $text = (($InputObject | Out-String).Trim())
    if (-not $text) {
        return @()
    }

    try {
        $parsed = $text | ConvertFrom-Json -ErrorAction Stop
        return @($parsed)
    }
    catch {
        $records = @()
        foreach ($line in ($text -split "`r?`n")) {
            $candidate = $line.Trim()
            if ($candidate.StartsWith('{') -or $candidate.StartsWith('[')) {
                try {
                    $records += @($candidate | ConvertFrom-Json -ErrorAction Stop)
                }
                catch {
                    # Keep looking: native Docker warnings can be mixed into output.
                }
            }
        }
        if ($records.Count -eq 0) {
            throw "Compose ps did not return parseable JSON: $text"
        }
        return @($records)
    }
}

function Get-IncipitComposeRecords {
    param([string[]]$ProfileArguments = @())

    $arguments = @('compose') + @($ProfileArguments) + @('ps', '--format', 'json')
    $output = Invoke-IncipitDocker -Arguments $arguments
    return @(ConvertFrom-IncipitComposePs -InputObject $output)
}

function Test-IncipitContainerRecordReady {
    param([Parameter(Mandatory)] [object]$Record)

    if ([string]$Record.State -ne 'running') {
        return $false
    }
    $healthProperty = $Record.PSObject.Properties['Health']
    if ($null -eq $healthProperty -or [string]::IsNullOrWhiteSpace([string]$Record.Health)) {
        return $true
    }
    return [string]$Record.Health -eq 'healthy'
}

function Show-IncipitWaitDiagnostics {
    param(
        [string[]]$FailedServices,
        [string[]]$ProfileArguments = @()
    )

    try {
        $arguments = @('compose') + @($ProfileArguments) + @('ps')
        $snapshot = (Invoke-IncipitDocker -Arguments $arguments | Out-String).TrimEnd()
        if ($snapshot) {
            Write-Host $snapshot
        }
    }
    catch {
        Write-Host "Unable to collect Compose state: $($_.Exception.Message)"
    }

    foreach ($service in $FailedServices) {
        try {
            $arguments = @('compose') + @($ProfileArguments) + @('logs', '--tail', '100', $service)
            $logs = (Invoke-IncipitDocker -Arguments $arguments | Out-String).TrimEnd()
            if ($logs) {
                Write-Host $logs
            }
        }
        catch {
            Write-Host "Unable to collect logs for ${service}: $($_.Exception.Message)"
        }
    }
}

function Wait-IncipitServices {
    param(
        [Parameter(Mandatory)] [string[]]$Services,
        [string[]]$ProfileArguments = @(),
        [int]$TimeoutSeconds = 180,
        [switch]$Optional
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $failedServices = @($Services)
    do {
        try {
            $records = @(Get-IncipitComposeRecords -ProfileArguments $ProfileArguments)
            $failedServices = @($Services | Where-Object {
                $service = $_
                $record = $records | Where-Object { $_.Service -eq $service } | Select-Object -First 1
                $null -eq $record -or -not (Test-IncipitContainerRecordReady -Record $record)
            })
        }
        catch {
            $failedServices = @($Services)
        }

        if ($failedServices.Count -eq 0) {
            return @()
        }
        if ((Get-Date) -ge $deadline) {
            break
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    Show-IncipitWaitDiagnostics -FailedServices $failedServices -ProfileArguments $ProfileArguments
    if ($Optional) {
        return @($failedServices | ForEach-Object {
            New-IncipitCheck "container:$_" $false 'WARN' "optional service did not become healthy within $TimeoutSeconds seconds"
        })
    }

    throw "Timed out waiting for required services: $($failedServices -join ', ')"
}

function Wait-IncipitInfrastructure {
    param(
        [switch]$Full,
        [string[]]$ProfileArguments = @(Get-IncipitProfileArguments -Full:$Full),
        [int]$TimeoutSeconds = 180
    )

    $null = Wait-IncipitServices `
        -Services $script:CoreInfrastructureServices `
        -ProfileArguments $ProfileArguments `
        -TimeoutSeconds $TimeoutSeconds

    if ($Full) {
        return @(Wait-IncipitServices `
            -Services $script:OptionalServices `
            -ProfileArguments $ProfileArguments `
            -TimeoutSeconds $TimeoutSeconds `
            -Optional)
    }
    return @()
}

function Build-IncipitImages {
    param([string[]]$ProfileArguments = @())

    $arguments = @('compose') + @($ProfileArguments) + @('build', 'api', 'web')
    $null = Invoke-IncipitDocker -Arguments $arguments
}

function Start-IncipitInfrastructure {
    param(
        [switch]$Full,
        [string[]]$ProfileArguments = @()
    )

    $arguments = @('compose') + @($ProfileArguments) + @('up', '-d') + $script:CoreInfrastructureServices
    $null = Invoke-IncipitDocker -Arguments $arguments

    if ($Full) {
        $arguments = @('compose') + @($ProfileArguments) + @('up', '-d') + $script:OptionalServices
        $null = Invoke-IncipitDocker -Arguments $arguments
    }
}

function Invoke-IncipitMigration {
    param([string[]]$ProfileArguments = @())

    $arguments = @('compose') + @($ProfileArguments) + @('run', '--rm', 'migrate')
    $null = Invoke-IncipitDocker -Arguments $arguments
}

function Wait-IncipitWorker {
    param(
        [string[]]$ProfileArguments = @(),
        [int]$TimeoutSeconds = 180
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastDetail = 'no workflow poller'
    do {
        try {
            $arguments = @('compose') + @($ProfileArguments) + @(
                'exec', '-T', 'api', 'python', 'scripts/runtime/runtime_probe.py', 'worker', '--json'
            )
            $output = (Invoke-IncipitDocker -Arguments $arguments | Out-String).Trim()
            $probe = $output | ConvertFrom-Json -ErrorAction Stop
            if ($probe.ok) {
                return $probe
            }
            $lastDetail = [string]$probe.detail
        }
        catch {
            $lastDetail = $_.Exception.Message
        }

        if ((Get-Date) -ge $deadline) {
            break
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    Show-IncipitWaitDiagnostics -FailedServices @('worker') -ProfileArguments $ProfileArguments
    throw "Timed out waiting for Temporal worker after $TimeoutSeconds seconds: $lastDetail"
}

function Start-IncipitApplications {
    param(
        [string[]]$ProfileArguments = @(),
        [string]$RootPath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
    )

    $apiPort = Get-IncipitConfiguredValue -Name 'API_HOST_PORT' -Default '8000' -RootPath $RootPath
    $webPort = Get-IncipitConfiguredValue -Name 'WEB_HOST_PORT' -Default '3300' -RootPath $RootPath

    $arguments = @('compose') + @($ProfileArguments) + @('up', '-d', 'api')
    $null = Invoke-IncipitDocker -Arguments $arguments
    $null = Wait-IncipitHttp -Uri "http://127.0.0.1:$apiPort/ready" -AcceptedStatus @(200)

    $arguments = @('compose') + @($ProfileArguments) + @('up', '-d', 'worker')
    $null = Invoke-IncipitDocker -Arguments $arguments
    $null = Wait-IncipitWorker -ProfileArguments $ProfileArguments

    $arguments = @('compose') + @($ProfileArguments) + @('up', '-d', 'web')
    $null = Invoke-IncipitDocker -Arguments $arguments
    $null = Wait-IncipitHttp -Uri "http://127.0.0.1:$webPort/"
}

function Start-Incipit {
    [CmdletBinding()]
    param([switch]$Full)

    $doctor = @(Invoke-IncipitDoctor -Full:$Full)
    if ($doctor | Where-Object { $_.Required -and $_.State -eq 'FAIL' }) {
        throw 'Doctor found required failures. Fix them before startup.'
    }

    $profileArguments = @(Get-IncipitProfileArguments -Full:$Full)
    $env:INCIPIT_OPTIONAL_SERVICES = Get-IncipitOptionalServices -Full:$Full

    Build-IncipitImages -ProfileArguments $profileArguments
    Start-IncipitInfrastructure -Full:$Full -ProfileArguments $profileArguments
    $startupWarnings = @(Wait-IncipitInfrastructure -Full:$Full -ProfileArguments $profileArguments)
    Invoke-IncipitMigration -ProfileArguments $profileArguments
    Start-IncipitApplications -ProfileArguments $profileArguments
    Get-IncipitStatus -AdditionalChecks $startupWarnings
}

function Stop-Incipit {
    $arguments = @('compose', 'down', '--remove-orphans')
    $null = Invoke-IncipitDocker -Arguments $arguments
}

function Add-IncipitHttpStatusCheck {
    param(
        [Parameter(Mandatory)] [System.Collections.Generic.List[object]]$Checks,
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Uri,
        [bool]$Required = $true
    )

    try {
        $response = Invoke-IncipitHttpRequest -Uri $Uri -TimeoutSeconds 5
        if ([int]$response.StatusCode -eq 200) {
            $Checks.Add((New-IncipitCheck $Name $Required 'PASS' 'HTTP 200'))
        }
        else {
            $state = if ($Required) { 'FAIL' } else { 'WARN' }
            $Checks.Add((New-IncipitCheck $Name $Required $state "HTTP $($response.StatusCode)"))
        }
        return $response
    }
    catch {
        $state = if ($Required) { 'FAIL' } else { 'WARN' }
        $Checks.Add((New-IncipitCheck $Name $Required $state $_.Exception.Message))
        return $null
    }
}

function Get-IncipitStatus {
    [CmdletBinding()]
    param(
        [switch]$Json,
        [string]$RootPath = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path,
        [object[]]$AdditionalChecks = @()
    )

    $records = @()
    $composeError = $null
    try {
        $records = @(Get-IncipitComposeRecords)
    }
    catch {
        $composeError = $_.Exception.Message
    }

    if ($null -ne $composeError) {
        $failedCheck = New-IncipitCheck 'compose-state' $true 'FAIL' $composeError
        $failedResult = [ordered]@{
            status = 'UNHEALTHY'
            checks = @([ordered]@{
                name = $failedCheck.Name
                required = $failedCheck.Required
                state = $failedCheck.State
                detail = $failedCheck.Detail
            })
        }
        if ($Json) {
            return ($failedResult | ConvertTo-Json -Depth 5)
        }
        Write-Host 'INCIPIT: UNHEALTHY'
        Write-Host (($failedCheck | Format-Table Name, Required, State, Detail -AutoSize | Out-String).TrimEnd())
        return
    }

    if ($records.Count -eq 0) {
        $emptyResult = [ordered]@{ status = 'STOPPED'; checks = @() }
        if ($Json) {
            return ($emptyResult | ConvertTo-Json -Depth 5)
        }
        Write-Host 'INCIPIT: STOPPED'
        return
    }

    $checks = [System.Collections.Generic.List[object]]::new()
    foreach ($additionalCheck in $AdditionalChecks) {
        $checks.Add($additionalCheck)
    }
    $requiredServices = $script:CoreInfrastructureServices + $script:ApplicationServices
    foreach ($service in $requiredServices) {
        $record = $records | Where-Object { $_.Service -eq $service } | Select-Object -First 1
        if ($null -eq $record) {
            $checks.Add((New-IncipitCheck "container:$service" $true 'FAIL' 'container is missing'))
        }
        elseif (Test-IncipitContainerRecordReady -Record $record) {
            $detail = if ($record.PSObject.Properties['Health'] -and $record.Health) { $record.Health } else { $record.State }
            $checks.Add((New-IncipitCheck "container:$service" $true 'PASS' ([string]$detail)))
        }
        else {
            $detail = "state=$($record.State)"
            if ($record.PSObject.Properties['Health'] -and $record.Health) { $detail += ", health=$($record.Health)" }
            $checks.Add((New-IncipitCheck "container:$service" $true 'FAIL' $detail))
        }
    }

    foreach ($record in ($records | Where-Object { $_.Service -in $script:OptionalServices })) {
        if (Test-IncipitContainerRecordReady -Record $record) {
            $checks.Add((New-IncipitCheck "container:$($record.Service)" $false 'PASS' ([string]$record.State)))
        }
        else {
            $health = if ($record.PSObject.Properties['Health']) { [string]$record.Health } else { 'none' }
            $checks.Add((New-IncipitCheck "container:$($record.Service)" $false 'WARN' "state=$($record.State), health=$health"))
        }
    }

    $apiPort = Get-IncipitConfiguredValue -Name 'API_HOST_PORT' -Default '8000' -RootPath $RootPath
    $webPort = Get-IncipitConfiguredValue -Name 'WEB_HOST_PORT' -Default '3300' -RootPath $RootPath
    $null = Add-IncipitHttpStatusCheck -Checks $checks -Name 'api' -Uri "http://127.0.0.1:$apiPort/health"
    $readyResponse = Add-IncipitHttpStatusCheck -Checks $checks -Name 'api-ready' -Uri "http://127.0.0.1:$apiPort/ready"
    if ($null -ne $readyResponse -and $readyResponse.Content) {
        try {
            $readyPayload = $readyResponse.Content | ConvertFrom-Json -ErrorAction Stop
            foreach ($item in @($readyPayload.checks)) {
                $itemRequired = [bool]$item.required
                if ([string]$item.status -eq 'ok') {
                    $checks.Add((New-IncipitCheck ([string]$item.name) $itemRequired 'PASS' ([string]$item.detail)))
                }
                else {
                    $state = if ($itemRequired) { 'FAIL' } else { 'WARN' }
                    $checks.Add((New-IncipitCheck ([string]$item.name) $itemRequired $state ([string]$item.detail)))
                }
            }
        }
        catch {
            $checks.Add((New-IncipitCheck 'api-ready-payload' $true 'FAIL' $_.Exception.Message))
        }
    }

    try {
        $arguments = @('compose', 'exec', '-T', 'api', 'python', 'scripts/runtime/runtime_probe.py', 'worker', '--json')
        $workerOutput = (Invoke-IncipitDocker -Arguments $arguments | Out-String).Trim()
        $workerProbe = $workerOutput | ConvertFrom-Json -ErrorAction Stop
        if ($workerProbe.ok) {
            $checks.Add((New-IncipitCheck 'worker-poller' $true 'PASS' ([string]$workerProbe.detail)))
        }
        else {
            $checks.Add((New-IncipitCheck 'worker-poller' $true 'FAIL' ([string]$workerProbe.detail)))
        }
    }
    catch {
        $checks.Add((New-IncipitCheck 'worker-poller' $true 'FAIL' $_.Exception.Message))
    }

    $null = Add-IncipitHttpStatusCheck -Checks $checks -Name 'web' -Uri "http://127.0.0.1:$webPort/"
    $resultChecks = $checks.ToArray()
    $overall = Get-IncipitOverallState -Checks $resultChecks
    $serializableChecks = @($resultChecks | ForEach-Object {
        [ordered]@{
            name = $_.Name
            required = $_.Required
            state = $_.State
            detail = $_.Detail
        }
    })
    $result = [ordered]@{ status = $overall; checks = $serializableChecks }

    if ($Json) {
        return ($result | ConvertTo-Json -Depth 5)
    }
    Write-Host "INCIPIT: $overall"
    Write-Host (($resultChecks | Format-Table Name, Required, State, Detail -AutoSize | Out-String).TrimEnd())
}

function Show-IncipitLogs {
    [CmdletBinding()]
    param(
        [string]$Service,
        [switch]$Follow
    )

    if ($Service) {
        $arguments = @('compose', 'logs', '--tail', '200')
        if ($Follow) {
            $arguments += '--follow'
        }
        $arguments += $Service
        Invoke-IncipitDocker -Arguments $arguments
        return
    }

    $services = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @('api', 'worker', 'web', 'temporal')) {
        $services.Add($name)
    }
    try {
        foreach ($record in @(Get-IncipitComposeRecords)) {
            if (-not (Test-IncipitContainerRecordReady -Record $record) -and -not $services.Contains([string]$record.Service)) {
                $services.Add([string]$record.Service)
            }
        }
    }
    catch {
        # Base service logs are still useful when Compose state cannot be parsed.
    }

    $arguments = @('compose', 'logs', '--tail', '100') + $services.ToArray()
    if ($Follow) {
        $arguments = @('compose', 'logs', '--tail', '100', '--follow') + $services.ToArray()
    }
    Invoke-IncipitDocker -Arguments $arguments
}

Export-ModuleMember -Function @(
    'Invoke-IncipitDocker',
    'Test-IncipitCommand',
    'Test-IncipitPort',
    'Invoke-IncipitDoctor',
    'Get-IncipitOverallState',
    'Get-IncipitProfileArguments',
    'Get-IncipitOptionalServices',
    'Invoke-IncipitHttpRequest',
    'Wait-IncipitHttp',
    'ConvertFrom-IncipitComposePs',
    'Get-IncipitComposeRecords',
    'Wait-IncipitServices',
    'Wait-IncipitInfrastructure',
    'Build-IncipitImages',
    'Start-IncipitInfrastructure',
    'Invoke-IncipitMigration',
    'Wait-IncipitWorker',
    'Start-IncipitApplications',
    'Start-Incipit',
    'Stop-Incipit',
    'Get-IncipitStatus',
    'Show-IncipitLogs'
)
