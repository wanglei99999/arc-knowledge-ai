Set-StrictMode -Version Latest

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

Export-ModuleMember -Function @(
    'Invoke-IncipitDocker',
    'Test-IncipitCommand',
    'Test-IncipitPort',
    'Invoke-IncipitDoctor',
    'Get-IncipitOverallState'
)
