BeforeAll {
    $modulePath = Join-Path $PSScriptRoot '../../scripts/runtime/Incipit.Runtime.psm1'
    Import-Module $modulePath -Force

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
}

Describe 'Get-IncipitOverallState' {
    It 'returns STOPPED when no checks ran' {
        Get-IncipitOverallState -Checks @() | Should -Be 'STOPPED'
    }

    It 'returns UNHEALTHY when a required check fails' {
        $checks = @(
            [pscustomobject]@{ Name = 'docker'; Required = $true; State = 'FAIL'; Detail = 'stopped' }
            [pscustomobject]@{ Name = 'model'; Required = $false; State = 'WARN'; Detail = 'offline' }
        )

        Get-IncipitOverallState -Checks $checks | Should -Be 'UNHEALTHY'
    }

    It 'returns DEGRADED when only optional checks warn or fail' {
        $checks = @(
            [pscustomobject]@{ Name = 'docker'; Required = $true; State = 'PASS'; Detail = 'ready' }
            [pscustomobject]@{ Name = 'model'; Required = $false; State = 'WARN'; Detail = 'offline' }
        )

        Get-IncipitOverallState -Checks $checks | Should -Be 'DEGRADED'
    }

    It 'returns HEALTHY when every check passes' {
        $checks = @(
            [pscustomobject]@{ Name = 'docker'; Required = $true; State = 'PASS'; Detail = 'ready' }
            [pscustomobject]@{ Name = 'model'; Required = $false; State = 'PASS'; Detail = 'ready' }
        )

        Get-IncipitOverallState -Checks $checks | Should -Be 'HEALTHY'
    }
}

Describe 'Invoke-IncipitDoctor' {
    BeforeEach {
        Mock Test-IncipitCommand { $true } -ModuleName Incipit.Runtime
        Mock Test-IncipitPort { $false } -ModuleName Incipit.Runtime
        Mock Get-IncipitDockerDiskAvailableBytes { 50GB } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)

            switch ($Arguments[0]) {
                'info' { return '{"MemTotal":17179869184}' }
                'ps' { return '' }
                default { return 'ok' }
            }
        } -ModuleName Incipit.Runtime
    }

    It 'reports a stopped Docker engine as a required failure' {
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)

            if ($Arguments[0] -eq 'info') {
                throw 'Docker Desktop is not running'
            }
            return 'ok'
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $dockerCheck = $checks | Where-Object Name -eq 'docker-engine'

        $dockerCheck.Required | Should -Be $true
        $dockerCheck.State | Should -Be 'FAIL'
        $dockerCheck.Detail | Should -Match 'not running'
    }

    It 'fails when Docker has less than 8 GiB of memory' {
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)

            switch ($Arguments[0]) {
                'info' { return '{"MemTotal":6442450944}' }
                'ps' { return '' }
                default { return 'ok' }
            }
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $memoryCheck = $checks | Where-Object Name -eq 'docker-memory'

        $memoryCheck.Required | Should -Be $true
        $memoryCheck.State | Should -Be 'FAIL'
    }

    It 'fails an occupied core port when the project does not own it' {
        Mock Test-IncipitPort {
            param([int]$Port)
            return $Port -eq 8000
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $portCheck = $checks | Where-Object Name -eq 'port:8000'

        $portCheck.Required | Should -Be $true
        $portCheck.State | Should -Be 'FAIL'
        $portCheck.Detail | Should -Match 'another process'
    }

    It 'accepts an occupied core port when this Compose project owns it' {
        Mock Test-IncipitPort {
            param([int]$Port)
            return $Port -eq 8000
        } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)

            switch ($Arguments[0]) {
                'info' { return '{"MemTotal":17179869184}' }
                'ps' { return '127.0.0.1:8000->8000/tcp' }
                default { return 'ok' }
            }
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $portCheck = $checks | Where-Object Name -eq 'port:8000'

        $portCheck.State | Should -Be 'PASS'
        $portCheck.Detail | Should -Match 'project container'
    }

    It 'returns no required failures on a clean mocked machine' {
        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $requiredFailures = @($checks | Where-Object { $_.Required -and $_.State -eq 'FAIL' })

        $requiredFailures.Count | Should -Be 0
    }

    It 'gives the exact recovery command when .env is missing' {
        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $envCheck = $checks | Where-Object Name -eq 'env-file'

        $envCheck.Required | Should -Be $false
        $envCheck.State | Should -Be 'WARN'
        $envCheck.Detail | Should -Be 'Missing .env. Run: Copy-Item .env.example .env'
    }

    It 'fails when an existing .env omits required fields' {
        $testRoot = Join-Path $TestDrive 'missing-env-fields'
        $null = New-Item -ItemType Directory -Path $testRoot
        [System.IO.File]::WriteAllText((Join-Path $testRoot 'docker-compose.yml'), "services: {}`n")
        [System.IO.File]::WriteAllText((Join-Path $testRoot '.env.example'), "COMPOSE_PROJECT_NAME=incipit`n")
        [System.IO.File]::WriteAllText((Join-Path $testRoot '.env'), "MINIO_ACCESS_KEY=minioadmin`n")

        $checks = @(Invoke-IncipitDoctor -RootPath $testRoot -FrontendPath $repoRoot)
        $fieldsCheck = $checks | Where-Object Name -eq 'env-required-fields'

        $fieldsCheck.Required | Should -Be $true
        $fieldsCheck.State | Should -Be 'FAIL'
        $fieldsCheck.Detail | Should -Match 'JWT_SECRET_KEY'
    }

    It 'reports an invalid Compose configuration as a required failure' {
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)

            if ($Arguments -contains 'config') {
                throw 'invalid compose interpolation'
            }
            switch ($Arguments[0]) {
                'info' { return '{"MemTotal":17179869184}' }
                'ps' { return '' }
                default { return 'ok' }
            }
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $composeCheck = $checks | Where-Object Name -eq 'compose-config'

        $composeCheck.Required | Should -Be $true
        $composeCheck.State | Should -Be 'FAIL'
        $composeCheck.Detail | Should -Match 'invalid compose interpolation'
    }

    It 'fails when available Docker disk is less than 20 GiB' {
        Mock Get-IncipitDockerDiskAvailableBytes { 19GB } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)
        $diskCheck = $checks | Where-Object Name -eq 'docker-disk'

        $diskCheck.Required | Should -Be $true
        $diskCheck.State | Should -Be 'FAIL'
    }

    It 'warns when Docker memory and disk meet minimums but not recommendations' {
        Mock Get-IncipitDockerDiskAvailableBytes { 30GB } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)

            switch ($Arguments[0]) {
                'info' { return '{"MemTotal":10737418240}' }
                'ps' { return '' }
                default { return 'ok' }
            }
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot)

        ($checks | Where-Object Name -eq 'docker-memory').State | Should -Be 'WARN'
        ($checks | Where-Object Name -eq 'docker-disk').State | Should -Be 'WARN'
    }

    It 'maps host.docker.internal to loopback for host-side model probes' {
        $null = Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot

        Should -Invoke Test-IncipitPort -Times 2 -Exactly -ModuleName Incipit.Runtime -ParameterFilter {
            $HostName -eq '127.0.0.1' -and $Port -eq 11434
        }
    }

    It 'warns about every empty offline cache in full mode' {
        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot -Full)
        $cacheWarnings = @($checks | Where-Object {
            $_.Name -in @('infinity-model-cache', 'paddleocr-model-cache', 'huggingface-model-cache') -and
            $_.State -eq 'WARN'
        })

        $cacheWarnings.Count | Should -Be 3
        ($cacheWarnings.Detail -join ' ') | Should -Match 'bge-reranker-v2-m3'
        ($cacheWarnings.Detail -join ' ') | Should -Match 'paddleocr'
        ($cacheWarnings.Detail -join ' ') | Should -Match 'huggingface'
    }

    It 'emits parseable JSON in JSON mode' {
        $json = Invoke-IncipitDoctor -RootPath $repoRoot -FrontendPath $repoRoot -Json
        $payload = @($json | ConvertFrom-Json)

        $payload.Count | Should -BeGreaterThan 0
        ($payload[0].PSObject.Properties.Name -contains 'Name') | Should -Be $true
        ($payload[0].PSObject.Properties.Name -contains 'Required') | Should -Be $true
        ($payload[0].PSObject.Properties.Name -contains 'State') | Should -Be $true
        ($payload[0].PSObject.Properties.Name -contains 'Detail') | Should -Be $true
    }
}

Describe 'Task 6 profile arguments' {
    It 'uses no optional profile arguments for core startup' {
        @(Get-IncipitProfileArguments).Count | Should -Be 0
        Get-IncipitOptionalServices | Should -Be ''
    }

    It 'maps Full to every optional profile' {
        $arguments = @(Get-IncipitProfileArguments -Full)

        ($arguments -join ' ') | Should -Be '--profile rerank --profile ocr --profile observe'
        Get-IncipitOptionalServices -Full | Should -Be 'rerank,ocr,observe'
    }
}

Describe 'Start-Incipit state machine' {
    BeforeEach {
        $global:IncipitStartEvents = [System.Collections.Generic.List[string]]::new()
        Mock Invoke-IncipitDoctor {
            $global:IncipitStartEvents.Add('doctor')
            return [pscustomobject]@{ Required = $true; State = 'PASS' }
        } -ModuleName Incipit.Runtime
        Mock Build-IncipitImages { $global:IncipitStartEvents.Add('build') } -ModuleName Incipit.Runtime
        Mock Start-IncipitInfrastructure { $global:IncipitStartEvents.Add('start-infrastructure') } -ModuleName Incipit.Runtime
        Mock Wait-IncipitInfrastructure { $global:IncipitStartEvents.Add('wait-infrastructure') } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitMigration { $global:IncipitStartEvents.Add('migrate') } -ModuleName Incipit.Runtime
        Mock Start-IncipitApplications { $global:IncipitStartEvents.Add('start-applications') } -ModuleName Incipit.Runtime
        Mock Get-IncipitStatus { $global:IncipitStartEvents.Add('status') } -ModuleName Incipit.Runtime
    }

    It 'runs migration only after infrastructure is healthy and before applications' {
        Start-Incipit

        ($global:IncipitStartEvents -join ',') | Should -Be 'doctor,build,start-infrastructure,wait-infrastructure,migrate,start-applications,status'
        Should -Invoke Invoke-IncipitMigration -Times 1 -Exactly -ModuleName Incipit.Runtime -Scope It
        Should -Invoke Start-IncipitApplications -Times 1 -Exactly -ModuleName Incipit.Runtime -Scope It
    }

    It 'stops before building when doctor has a required failure' {
        Mock Invoke-IncipitDoctor {
            return [pscustomobject]@{ Required = $true; State = 'FAIL' }
        } -ModuleName Incipit.Runtime

        { Start-Incipit } | Should -Throw '*Doctor found required failures*'
        Should -Invoke Build-IncipitImages -Times 0 -Exactly -ModuleName Incipit.Runtime -Scope It
    }

    It 'forwards every optional profile during Full startup' {
        $global:IncipitFullProfileArguments = @()
        Mock Build-IncipitImages {
            param([string[]]$ProfileArguments)
            $global:IncipitFullProfileArguments = $ProfileArguments
            $global:IncipitStartEvents.Add('build')
        } -ModuleName Incipit.Runtime

        Start-Incipit -Full

        ($global:IncipitFullProfileArguments -join ' ') | Should -Be '--profile rerank --profile ocr --profile observe'
        $env:INCIPIT_OPTIONAL_SERVICES | Should -Be 'rerank,ocr,observe'
    }
}

Describe 'Start-IncipitApplications ordering' {
    BeforeEach {
        $global:IncipitApplicationEvents = [System.Collections.Generic.List[string]]::new()
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)
            $global:IncipitApplicationEvents.Add(($Arguments -join ' '))
        } -ModuleName Incipit.Runtime
        Mock Wait-IncipitHttp {
            param([string]$Uri)
            if ($Uri -match '/ready$') {
                $global:IncipitApplicationEvents.Add('wait-api')
            }
            else {
                $global:IncipitApplicationEvents.Add('wait-web')
            }
        } -ModuleName Incipit.Runtime
        Mock Wait-IncipitWorker {
            $global:IncipitApplicationEvents.Add('wait-worker')
        } -ModuleName Incipit.Runtime
    }

    It 'waits for API before worker and worker before web' {
        Start-IncipitApplications -ProfileArguments @()

        ($global:IncipitApplicationEvents -join ',') | Should -Be 'compose up -d api,wait-api,compose up -d worker,wait-worker,compose up -d web,wait-web'
    }
}

Describe 'Task 6 bounded waits' {
    It 'returns immediately for an accepted HTTP status' {
        Mock Invoke-IncipitHttpRequest {
            return [pscustomobject]@{ StatusCode = 200; Content = '{}' }
        } -ModuleName Incipit.Runtime

        (Wait-IncipitHttp -Uri 'http://127.0.0.1:8000/ready' -TimeoutSeconds 0).StatusCode | Should -Be 200
    }

    It 'throws after the HTTP deadline instead of waiting forever' {
        Mock Invoke-IncipitHttpRequest { throw 'connection refused' } -ModuleName Incipit.Runtime

        { Wait-IncipitHttp -Uri 'http://127.0.0.1:8000/ready' -TimeoutSeconds 0 } | Should -Throw '*Timed out waiting for*'
    }

    It 'parses both JSON arrays and line-delimited Compose records' {
        $arrayRecords = @(ConvertFrom-IncipitComposePs '[{"Service":"postgres","State":"running","Health":"healthy"}]')
        $lineRecords = @(ConvertFrom-IncipitComposePs "{`"Service`":`"postgres`",`"State`":`"running`",`"Health`":`"healthy`"}`n{`"Service`":`"redis`",`"State`":`"running`",`"Health`":`"healthy`"}")

        $arrayRecords.Count | Should -Be 1
        $lineRecords.Count | Should -Be 2
        $lineRecords[1].Service | Should -Be 'redis'
    }

    It 'ignores native Docker warnings around valid JSON records' {
        $output = "WARNING: credential helper unavailable`n{`"Service`":`"postgres`",`"State`":`"running`",`"Health`":`"healthy`"}"

        $records = @(ConvertFrom-IncipitComposePs $output)

        $records.Count | Should -Be 1
        $records[0].Service | Should -Be 'postgres'
    }

    It 'turns an optional service timeout into a named warning' {
        Mock Get-IncipitComposeRecords { return @() } -ModuleName Incipit.Runtime
        Mock Show-IncipitWaitDiagnostics {} -ModuleName Incipit.Runtime

        $warnings = @(Wait-IncipitServices -Services @('infinity') -TimeoutSeconds 0 -Optional)

        $warnings.Count | Should -Be 1
        $warnings[0].Name | Should -Be 'container:infinity'
        $warnings[0].Required | Should -Be $false
        $warnings[0].State | Should -Be 'WARN'
    }

    It 'terminates on a required service timeout' {
        Mock Get-IncipitComposeRecords { return @() } -ModuleName Incipit.Runtime
        Mock Show-IncipitWaitDiagnostics {} -ModuleName Incipit.Runtime

        { Wait-IncipitServices -Services @('postgres') -TimeoutSeconds 0 } | Should -Throw '*required services: postgres*'
    }

    It 'accepts a running healthy worker probe' {
        Mock Invoke-IncipitDocker {
            return '{"name":"worker","ok":true,"detail":"1 workflow poller"}'
        } -ModuleName Incipit.Runtime

        $probe = Wait-IncipitWorker -TimeoutSeconds 0

        $probe.ok | Should -Be $true
        $probe.detail | Should -Be '1 workflow poller'
    }
}

Describe 'Stop-Incipit' {
    It 'removes containers and orphans without deleting named volumes' {
        $global:IncipitStopArguments = @()
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)
            $global:IncipitStopArguments = $Arguments
        } -ModuleName Incipit.Runtime

        Stop-Incipit

        ($global:IncipitStopArguments -join ' ') | Should -Be 'compose down --remove-orphans'
        ($global:IncipitStopArguments -contains '--volumes') | Should -Be $false
        ($global:IncipitStopArguments -contains '-v') | Should -Be $false
    }
}

Describe 'Get-IncipitStatus' {
    It 'returns parseable STOPPED JSON when no managed containers exist' {
        Mock Get-IncipitComposeRecords { return @() } -ModuleName Incipit.Runtime

        $status = Get-IncipitStatus -Json | ConvertFrom-Json

        $status.status | Should -Be 'STOPPED'
        @($status.checks).Count | Should -Be 0
    }

    It 'reports a Compose query failure as UNHEALTHY instead of STOPPED' {
        Mock Get-IncipitComposeRecords { throw 'Docker engine unavailable' } -ModuleName Incipit.Runtime

        $status = Get-IncipitStatus -Json | ConvertFrom-Json

        $status.status | Should -Be 'UNHEALTHY'
        $status.checks[0].name | Should -Be 'compose-state'
        $status.checks[0].state | Should -Be 'FAIL'
    }

    It 'combines containers HTTP readiness dependencies worker and web' {
        $requiredServices = @(
            'postgres', 'minio', 'etcd', 'milvus', 'elasticsearch', 'redis',
            'temporal', 'temporal-ui', 'api', 'worker', 'web'
        )
        $global:IncipitStatusRecords = @($requiredServices | ForEach-Object {
            [pscustomobject]@{ Service = $_; State = 'running'; Health = 'healthy' }
        })
        Mock Get-IncipitComposeRecords { return $global:IncipitStatusRecords } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitHttpRequest {
            param([string]$Uri)
            if ($Uri -match '/ready$') {
                return [pscustomobject]@{
                    StatusCode = 200
                    Content = '{"status":"degraded","checks":[{"name":"llm","required":false,"status":"failed","detail":"connection refused"}]}'
                }
            }
            return [pscustomobject]@{ StatusCode = 200; Content = '{"status":"ok"}' }
        } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitDocker {
            return '{"name":"worker","ok":true,"detail":"1 workflow poller"}'
        } -ModuleName Incipit.Runtime

        $status = Get-IncipitStatus -Json | ConvertFrom-Json
        $llmCheck = $status.checks | Where-Object name -eq 'llm'
        $workerCheck = $status.checks | Where-Object name -eq 'worker-poller'

        $status.status | Should -Be 'DEGRADED'
        $llmCheck.required | Should -Be $false
        $llmCheck.state | Should -Be 'WARN'
        $workerCheck.state | Should -Be 'PASS'
    }
}

Describe 'Show-IncipitLogs' {
    It 'maps a followed service log request to tail 200 and follow' {
        $global:IncipitLogArguments = @()
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)
            $global:IncipitLogArguments = $Arguments
        } -ModuleName Incipit.Runtime

        Show-IncipitLogs -Service api -Follow

        ($global:IncipitLogArguments -join ' ') | Should -Be 'compose logs --tail 200 --follow api'
    }

    It 'shows base services plus unhealthy containers when no service is named' {
        $global:IncipitLogArguments = @()
        Mock Get-IncipitComposeRecords {
            return @(
                [pscustomobject]@{ Service = 'api'; State = 'running'; Health = 'healthy' }
                [pscustomobject]@{ Service = 'postgres'; State = 'exited'; Health = 'unhealthy' }
            )
        } -ModuleName Incipit.Runtime
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)
            $global:IncipitLogArguments = $Arguments
        } -ModuleName Incipit.Runtime

        Show-IncipitLogs

        ($global:IncipitLogArguments -join ' ') | Should -Be 'compose logs --tail 100 api worker web temporal postgres'
    }
}

Describe 'incipit.ps1 dispatcher' {
    It 'exposes the stable runtime commands' {
        $source = Get-Content (Join-Path $repoRoot 'incipit.ps1') -Raw

        $source | Should -Match "ValidateSet\('doctor', 'start', 'stop', 'status', 'logs', 'smoke'\)"
        $source | Should -Match "'start'\s+\{ Start-Incipit"
        $source | Should -Match "'logs'\s+\{ Show-IncipitLogs"
    }
}

Describe 'Invoke-IncipitSmoke' {
    It 'runs L0 inside the API container with service-network URLs' {
        $global:IncipitSmokeArguments = @()
        Mock Invoke-IncipitDocker {
            param([string[]]$Arguments)
            $global:IncipitSmokeArguments = $Arguments
            return '{"level":"L0","status":"PASS"}'
        } -ModuleName Incipit.Runtime

        $output = Invoke-IncipitSmoke -Level L0

        ($global:IncipitSmokeArguments -join ' ') | Should -Be 'compose exec -T api python scripts/runtime/smoke.py --level l0 --api-base-url http://api:8000 --web-base-url http://web'
        $output | Should -Match '"status":"PASS"'
    }

    It 'passes smoke credentials to the API container through Compose' {
        $compose = Get-Content (Join-Path $repoRoot 'docker-compose.yml') -Raw

        $compose | Should -Match 'SMOKE_TENANT_ID:'
        $compose | Should -Match 'SMOKE_EMAIL:'
        $compose | Should -Match 'SMOKE_PASSWORD:'
    }
}
