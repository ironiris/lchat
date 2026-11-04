param(
    [string]$InstallDir,
    [switch]$Update
)

$ErrorActionPreference = 'Stop'

function Test-ExternalCommand {
    param([string]$Executable, [string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    & $Executable @Arguments *> $null
    return ($LASTEXITCODE -eq 0)
}

function Get-ExternalCommandOutput {
    param([string]$Executable, [string[]]$Arguments)
    $ErrorActionPreference = 'Continue'
    $output = & $Executable @Arguments 2>$null
    $exitCode = $LASTEXITCODE
    return [PSCustomObject]@{ Output = $output; ExitCode = $exitCode }
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        throw 'LOCALAPPDATA is not set. Specify -InstallDir explicitly.'
    }
    $InstallDir = Join-Path $env:LOCALAPPDATA 'lchat'
}

# Prefer an explicitly selected interpreter, then the active `python`, then the launcher.
$Python = $env:LCHAT_PYTHON
if ([string]::IsNullOrWhiteSpace($Python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $candidate = $pythonCommand.Source
        if (Test-ExternalCommand -Executable $candidate -Arguments @('-c', 'import sys')) {
            $Python = $candidate
        }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $probe = Get-ExternalCommandOutput -Executable $launcher.Source `
            -Arguments @('-3', '-c', 'import sys; print(sys.executable)')
        if ($probe.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($probe.Output)) {
            $Python = ([string]$probe.Output).Trim()
        }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    throw 'Python 3.9+ was not found. Install Python, then rerun this script.'
}

$versionProbe = Get-ExternalCommandOutput -Executable $Python `
    -Arguments @('-c', "import sys; print('%d.%d.%d' % sys.version_info[:3])")
if ($versionProbe.ExitCode -ne 0) { throw "Could not run Python at $Python" }
$versionText = ([string]$versionProbe.Output).Trim()
$version = [version]$versionText
if ($version -lt [version]'3.9') { throw "Python 3.9+ is required; found $version." }
Write-Host "Using Python $version at $Python"

# Install the Windows curses compatibility module into the interpreter selected above.
if (-not (Test-ExternalCommand -Executable $Python -Arguments @('-c', 'import curses'))) {
    Write-Host 'Installing windows-curses into the selected Python environment...'
    if (-not (Test-ExternalCommand -Executable $Python -Arguments @('-m', 'pip', '--version'))) {
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & $Python -m ensurepip --upgrade
            $ensurepipExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }
        if ($ensurepipExitCode -ne 0) { throw 'Could not bootstrap pip for the selected Python.' }
    }

    $venvProbe = Get-ExternalCommandOutput -Executable $Python `
        -Arguments @('-c', 'import sys; print(int(sys.prefix != sys.base_prefix))')
    if ($venvProbe.ExitCode -ne 0) { throw 'Could not inspect the selected Python environment.' }
    $pipArgs = @('install', 'windows-curses')
    if (([string]$venvProbe.Output).Trim() -ne '1') { $pipArgs += '--user' }
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Python -m pip @pipArgs
        $pipExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($pipExitCode -ne 0) { throw 'Could not install windows-curses.' }
}
if (-not (Test-ExternalCommand -Executable $Python -Arguments @('-c', 'import curses, urllib.request'))) {
    throw 'Required Python modules are still unavailable.'
}

if ((Test-Path $InstallDir) -and -not $Update) {
    throw "Install directory already exists: $InstallDir. Use -Update to replace the app files."
}

$tempDir = Join-Path ([IO.Path]::GetTempPath()) ('lchat-install-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $tempDir | Out-Null
    $archive = Join-Path $tempDir 'lchat.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri 'https://github.com/ironiris/lchat/archive/refs/heads/main.zip' `
        -OutFile $archive -UseBasicParsing
    Expand-Archive -Path $archive -DestinationPath $tempDir -Force
    $sourceDir = Join-Path $tempDir 'lchat-main'
    foreach ($file in @('lchat.py', 'lchat_web.py')) {
        if (-not (Test-Path (Join-Path $sourceDir $file))) {
            throw "Downloaded source is missing $file."
        }
    }

    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Copy-Item (Join-Path $sourceDir 'lchat.py') $InstallDir -Force
    Copy-Item (Join-Path $sourceDir 'lchat_web.py') $InstallDir -Force
} finally {
    Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Installed lchat to $InstallDir"
Write-Host 'Web hosting is disabled on Windows; the terminal UI is used.'
Write-Host 'Run this command in PowerShell:'
Write-Host ('  & "{0}" "{1}" --terminal-only' -f $Python, (Join-Path $InstallDir 'lchat.py'))
