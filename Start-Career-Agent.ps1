param(
    [switch]$Demo,
    [switch]$NoOpen,
    [switch]$Check,
    [ValidateRange(1024,65534)][int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 redraws a progress bar for every downloaded chunk, which
# makes Invoke-WebRequest many times slower than the download itself. The steps
# below say what is happening instead.
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
$stage = 'setup'
try {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    $uvExe = if ($uvCommand) { $uvCommand.Source } else { Join-Path $PSScriptRoot '.tools\uv.exe' }
    if (-not (Test-Path -LiteralPath $uvExe)) {
        $stage = 'download'
        Write-Host '[1/3] First setup: downloading uv, the small tool that installs Python for you.'
        Write-Host '      This happens once. It needs internet but no administrator access.'
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
            throw 'The download was damaged on the way (its checksum did not match). Double-click the launcher again to retry.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $toolsDir)
    }
    $stage = 'install'
    Write-Host '[2/3] Checking Career Agent and Resume Tailor Beta. Python 3.12 is managed automatically.'
    Write-Host '      The first time this downloads everything they need and can take a few minutes.'
    Write-Host '      Later starts only check, and take seconds.'
    & $uvExe sync --locked --no-dev --python 3.12
    if ($LASTEXITCODE -ne 0) {
        throw 'Setup could not download what it needs. Check that this computer is online, then double-click the launcher again.'
    }
    $stage = 'start'
    Write-Host '[3/3] Starting. Your browser will open by itself.'
    $launchArgs = @('run','--no-sync','python','scripts/launch.py','--port',"$Port")
    if ($Demo) { $launchArgs += '--demo' }
    if ($NoOpen) { $launchArgs += '--no-open' }
    if ($Check) { $launchArgs += '--check' }
    & $uvExe @launchArgs
    exit $LASTEXITCODE
} catch {
    Write-Host ''
    Write-Host "Setup stopped: $($_.Exception.Message)"
    Write-Host 'Nothing you saved was removed or changed.'
    if ($stage -eq 'download' -or $stage -eq 'install') {
        Write-Host 'If this computer is offline, connect to the internet and start again. The first setup cannot finish offline.'
    }
    Write-Host 'More help: FIRST_RUN.md in this folder.'
    exit 1
}
