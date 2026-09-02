$modulePath = Join-Path $PSScriptRoot '../../scripts/runtime/Incipit.Runtime.psm1'
Import-Module $modulePath -Force

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path

Describe 'Get-IncipitOverallState' {
    It 'returns STOPPED when no checks ran' {
        Get-IncipitOverallState -Checks @() | Should Be 'STOPPED'
    }

    It 'returns UNHEALTHY when a required check fails' {
        $checks = @(
            [pscustomobject]@{ Name = 'docker'; Required = $true; State = 'FAIL'; Detail = 'stopped' }
            [pscustomobject]@{ Name = 'model'; Required = $false; State = 'WARN'; Detail = 'offline' }
        )

        Get-IncipitOverallState -Checks $checks | Should Be 'UNHEALTHY'
    }

    It 'returns DEGRADED when only optional checks warn or fail' {
        $checks = @(
            [pscustomobject]@{ Name = 'docker'; Required = $true; State = 'PASS'; Detail = 'ready' }
            [pscustomobject]@{ Name = 'model'; Required = $false; State = 'WARN'; Detail = 'offline' }
        )

        Get-IncipitOverallState -Checks $checks | Should Be 'DEGRADED'
    }

    It 'returns HEALTHY when every check passes' {
        $checks = @(
            [pscustomobject]@{ Name = 'docker'; Required = $true; State = 'PASS'; Detail = 'ready' }
            [pscustomobject]@{ Name = 'model'; Required = $false; State = 'PASS'; Detail = 'ready' }
        )

        Get-IncipitOverallState -Checks $checks | Should Be 'HEALTHY'
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

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $dockerCheck = $checks | Where-Object Name -eq 'docker-engine'

        $dockerCheck.Required | Should Be $true
        $dockerCheck.State | Should Be 'FAIL'
        $dockerCheck.Detail | Should Match 'not running'
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

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $memoryCheck = $checks | Where-Object Name -eq 'docker-memory'

        $memoryCheck.Required | Should Be $true
        $memoryCheck.State | Should Be 'FAIL'
    }

    It 'fails an occupied core port when the project does not own it' {
        Mock Test-IncipitPort {
            param([int]$Port)
            return $Port -eq 8000
        } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $portCheck = $checks | Where-Object Name -eq 'port:8000'

        $portCheck.Required | Should Be $true
        $portCheck.State | Should Be 'FAIL'
        $portCheck.Detail | Should Match 'another process'
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

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $portCheck = $checks | Where-Object Name -eq 'port:8000'

        $portCheck.State | Should Be 'PASS'
        $portCheck.Detail | Should Match 'project container'
    }

    It 'returns no required failures on a clean mocked machine' {
        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $requiredFailures = @($checks | Where-Object { $_.Required -and $_.State -eq 'FAIL' })

        $requiredFailures.Count | Should Be 0
    }

    It 'gives the exact recovery command when .env is missing' {
        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $envCheck = $checks | Where-Object Name -eq 'env-file'

        $envCheck.Required | Should Be $false
        $envCheck.State | Should Be 'WARN'
        $envCheck.Detail | Should Be 'Missing .env. Run: Copy-Item .env.example .env'
    }

    It 'fails when an existing .env omits required fields' {
        $testRoot = Join-Path $TestDrive 'missing-env-fields'
        $null = New-Item -ItemType Directory -Path $testRoot
        [System.IO.File]::WriteAllText((Join-Path $testRoot 'docker-compose.yml'), "services: {}`n")
        [System.IO.File]::WriteAllText((Join-Path $testRoot '.env.example'), "COMPOSE_PROJECT_NAME=incipit`n")
        [System.IO.File]::WriteAllText((Join-Path $testRoot '.env'), "MINIO_ACCESS_KEY=minioadmin`n")

        $checks = @(Invoke-IncipitDoctor -RootPath $testRoot -FrontendPath $repoRoot)
        $fieldsCheck = $checks | Where-Object Name -eq 'env-required-fields'

        $fieldsCheck.Required | Should Be $true
        $fieldsCheck.State | Should Be 'FAIL'
        $fieldsCheck.Detail | Should Match 'JWT_SECRET_KEY'
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

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $composeCheck = $checks | Where-Object Name -eq 'compose-config'

        $composeCheck.Required | Should Be $true
        $composeCheck.State | Should Be 'FAIL'
        $composeCheck.Detail | Should Match 'invalid compose interpolation'
    }

    It 'fails when available Docker disk is less than 20 GiB' {
        Mock Get-IncipitDockerDiskAvailableBytes { 19GB } -ModuleName Incipit.Runtime

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)
        $diskCheck = $checks | Where-Object Name -eq 'docker-disk'

        $diskCheck.Required | Should Be $true
        $diskCheck.State | Should Be 'FAIL'
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

        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot)

        ($checks | Where-Object Name -eq 'docker-memory').State | Should Be 'WARN'
        ($checks | Where-Object Name -eq 'docker-disk').State | Should Be 'WARN'
    }

    It 'maps host.docker.internal to loopback for host-side model probes' {
        $null = Invoke-IncipitDoctor -RootPath $repoRoot

        Assert-MockCalled Test-IncipitPort 2 -ModuleName Incipit.Runtime -ParameterFilter {
            $HostName -eq '127.0.0.1' -and $Port -eq 11434
        }
    }

    It 'warns about every empty offline cache in full mode' {
        $checks = @(Invoke-IncipitDoctor -RootPath $repoRoot -Full)
        $cacheWarnings = @($checks | Where-Object {
            $_.Name -in @('infinity-model-cache', 'paddleocr-model-cache', 'huggingface-model-cache') -and
            $_.State -eq 'WARN'
        })

        $cacheWarnings.Count | Should Be 3
        ($cacheWarnings.Detail -join ' ') | Should Match 'bge-reranker-v2-m3'
        ($cacheWarnings.Detail -join ' ') | Should Match 'paddleocr'
        ($cacheWarnings.Detail -join ' ') | Should Match 'huggingface'
    }

    It 'emits parseable JSON in JSON mode' {
        $json = Invoke-IncipitDoctor -RootPath $repoRoot -Json
        $payload = @($json | ConvertFrom-Json)

        $payload.Count | Should BeGreaterThan 0
        ($payload[0].PSObject.Properties.Name -contains 'Name') | Should Be $true
        ($payload[0].PSObject.Properties.Name -contains 'Required') | Should Be $true
        ($payload[0].PSObject.Properties.Name -contains 'State') | Should Be $true
        ($payload[0].PSObject.Properties.Name -contains 'Detail') | Should Be $true
    }
}
