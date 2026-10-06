param(
    [switch]$Demo,
    [switch]$NoOpen,
    [switch]$Check,
    [ValidateRange(1024,65534)][int]$Port = 8765,
    # Start with this local profile (its name). Optional.
    [string]$ProfileName = '',
    # Only make the Career Agent shortcut again (Desktop and Start menu), then stop.
    [switch]$Shortcuts
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 redraws a progress bar for every downloaded chunk, which
# makes Invoke-WebRequest many times slower than the download itself. The steps
# below say what is happening instead.
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot

# The "Career Agent" shortcut on the Desktop and in the Start menu. Windows
# says where those folders are (they may be redirected, for example to
# OneDrive). The shortcut opens scripts\desktop.py with this folder's own
# pythonw.exe, which shows no console window.
function New-CareerAgentShortcuts {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @($shell.SpecialFolders.Item('Desktop'), $shell.SpecialFolders.Item('Programs'))) {
        if (-not $folder) { continue }
        $link = $shell.CreateShortcut((Join-Path $folder 'Career Agent.lnk'))
        $link.TargetPath = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
        $link.Arguments = '"' + (Join-Path $PSScriptRoot 'scripts\desktop.py') + '"'
        $link.WorkingDirectory = $PSScriptRoot
        $link.IconLocation = (Join-Path $PSScriptRoot 'src\career_agent\web\static\career-agent.ico') + ',0'
        $link.Description = 'Open Career Agent'
        $link.Save()
    }
}

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
    Write-Host '[2/3] Checking Career Agent. Python 3.12 is managed automatically.'
    Write-Host '      The first time this downloads everything they need and can take a few minutes.'
    Write-Host '      Later starts only check, and take seconds.'
    & $uvExe sync --locked --no-dev --python 3.12
    if ($LASTEXITCODE -ne 0) {
        throw 'Setup could not download what it needs. Check that this computer is online, then double-click the launcher again.'
    }
    # Made after the first setup that succeeds, and made again when this
    # folder has moved since (the marker holds the folder it was made for).
    # Never during -Check, which also verifies copies the person does not use.
    # A failure here never stops Career Agent: this launcher works without it.
    $marker = Join-Path $PSScriptRoot 'data\.shortcuts-created'
    $madeFor = if (Test-Path -LiteralPath $marker) { (Get-Content -LiteralPath $marker -TotalCount 1) } else { '' }
    if ($Shortcuts -or (-not $Check -and $madeFor -ne $PSScriptRoot)) {
        try {
            New-CareerAgentShortcuts
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $marker) | Out-Null
            Set-Content -LiteralPath $marker -Encoding utf8 -Value $PSScriptRoot
            Write-Host '      A "Career Agent" shortcut is on your Desktop and in the Start menu. Use it from now on.'
        } catch {
            Write-Host "      The Career Agent shortcut could not be made: $($_.Exception.Message)"
            Write-Host '      Career Agent still works from this launcher. To try again, double-click Create-Career-Agent-Shortcuts.cmd.'
            if ($Shortcuts) { exit 1 }
        }
    }
    if ($Shortcuts) { exit 0 }
    $stage = 'start'
    Write-Host '[3/3] Starting. Your browser will open by itself.'
    $launchArgs = @('run','--no-sync','python','scripts/launch.py','--port',"$Port")
    if ($Demo) { $launchArgs += '--demo' }
    if ($NoOpen) { $launchArgs += '--no-open' }
    if ($ProfileName) { $launchArgs += @('--profile', $ProfileName) }
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
