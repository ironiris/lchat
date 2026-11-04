param(
    [string]$InstallDir,
    [switch]$Update
)

$ErrorActionPreference = 'Stop'

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
        & $candidate -c 'import sys' *> $null
        if ($LASTEXITCODE -eq 0) { $Python = $candidate }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidate = & py -3 -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($candidate)) {
            $Python = $candidate.Trim()
        }
    }
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    throw 'Python 3.9+ was not found. Install Python, then rerun this script.'
}

$versionText = & $Python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
if ($LASTEXITCODE -ne 0) { throw "Could not run Python at $Python" }
$version = [version]$versionText.Trim()
if ($version -lt [version]'3.9') { throw "Python 3.9+ is required; found $version." }
Write-Host "Using Python $version at $Python"

# Install the Windows curses compatibility module into the interpreter selected above.
& $Python -c 'import curses' *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing windows-curses into the selected Python environment...'
    & $Python -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        & $Python -m ensurepip --upgrade
        if ($LASTEXITCODE -ne 0) { throw 'Could not bootstrap pip for the selected Python.' }
    }

    $inVenv = & $Python -c 'import sys; print(int(sys.prefix != sys.base_prefix))'
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the selected Python environment.' }
    $pipArgs = @('install', 'windows-curses')
    if ($inVenv.Trim() -ne '1') { $pipArgs += '--user' }
    & $Python -m pip @pipArgs
    if ($LASTEXITCODE -ne 0) { throw 'Could not install windows-curses.' }
}
& $Python -c 'import curses, urllib.request'
if ($LASTEXITCODE -ne 0) { throw 'Required Python modules are still unavailable.' }

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
