[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('doctor', 'start', 'stop', 'status', 'logs', 'smoke')]
    [string]$Command = 'status',
    [switch]$Full,
    [switch]$Json,
    [switch]$Follow,
    [string]$Service,
    [ValidateSet('L0', 'L1')]
    [string]$Level = 'L1'
)

$ErrorActionPreference = 'Stop'
Import-Module "$PSScriptRoot/scripts/runtime/Incipit.Runtime.psm1" -Force

Push-Location $PSScriptRoot
try {
    switch ($Command) {
        'doctor' { Invoke-IncipitDoctor -Json:$Json -Full:$Full }
        'start'  { Start-Incipit -Full:$Full }
        'stop'   { Stop-Incipit }
        'status' { Get-IncipitStatus -Json:$Json }
        'logs'   { Show-IncipitLogs -Service $Service -Follow:$Follow }
        'smoke'  { Invoke-IncipitSmoke -Level $Level }
    }
}
finally {
    Pop-Location
}
