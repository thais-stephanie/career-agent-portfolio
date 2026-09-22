param(
    [switch]$Demo,
    [switch]$NoOpen,
    [switch]$Check,
    [ValidateRange(1024,65534)][int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    $uvExe = if ($uvCommand) { $uvCommand.Source } else { Join-Path $PSScriptRoot '.tools\uv.exe' }
    if (-not (Test-Path -LiteralPath $uvExe)) {
        Write-Host 'First setup: downloading uv, the tool that installs Python for you.'
        Write-Host 'An internet connection is needed. No administrator access is required.'
        $toolsDir = Join-Path $PSScriptRoot '.tools'
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
        $asset = "uv-$arch-pc-windows-msvc.zip"
        $base = 'https://github.com/astral-sh/uv/releases/download/0.11.7'
        $zip = Join-Path $toolsDir $asset
        Invoke-WebRequest -UseBasicParsing "$base/$asset" -OutFile $zip
        $sumPath = Join-Path $toolsDir "$asset.sha256"
        Invoke-WebRequest -UseBasicParsing "$base/$asset.sha256" -OutFile $sumPath
        $expected = ((Get-Content -LiteralPath $sumPath -Raw).Trim() -split '\s+')[0]
        $hasher = [System.Security.Cryptography.SHA256]::Create()
        $stream = [System.IO.File]::OpenRead($zip)
        try { $actual = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLower() }
        finally { $stream.Dispose(); $hasher.Dispose() }
        if ($actual -ne $expected.ToLower()) {
            throw 'The download checksum did not match. Please try again.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $toolsDir)
    }
    Write-Host 'Preparing Career Agent and Resume Tailor Beta. Python 3.12 is managed automatically.'
    & $uvExe sync --locked --no-dev --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Setup could not finish. Check your internet connection and try again.' }
    $launchArgs = @('run','--no-sync','python','scripts/launch.py','--port',"$Port")
    if ($Demo) { $launchArgs += '--demo' }
    if ($NoOpen) { $launchArgs += '--no-open' }
    if ($Check) { $launchArgs += '--check' }
    & $uvExe @launchArgs
    exit $LASTEXITCODE
} catch {
    Write-Host "Setup stopped: $($_.Exception.Message)"
    Write-Host 'Your saved data has not been removed. See FIRST_RUN.md for help.'
    exit 1
}
