$ErrorActionPreference = 'Stop'
# Do not inherit a PowerShell 7-only module path from a launcher or terminal.
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + ';' + (Join-Path $env:ProgramFiles 'WindowsPowerShell/Modules')
$stage = [IO.Path]::GetFullPath($PSScriptRoot)
$plan = Get-Content -LiteralPath (Join-Path $stage 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$target = [IO.Path]::GetFullPath($plan.target)
$source = [IO.Path]::GetFullPath($plan.source)
$root = [IO.Path]::GetFullPath($plan.root)
$backup = Join-Path $stage 'previous.exe'
$replaced = $false
$launched = $null
$safeCleanup = $false

function Start-App {
    if ($plan.testRoot -eq $root) {
        return Start-Process -FilePath $target -ArgumentList @('--test-root', ('"' + $root + '"')) -WorkingDirectory $root -WindowStyle Hidden -PassThru
    }
    return Start-Process -FilePath $target -WorkingDirectory $root -WindowStyle Hidden -PassThru
}

try {
    if ((Split-Path $target -Parent) -ne $root -or
        (Split-Path $stage -Parent) -ne $root -or
        (Split-Path $stage -Leaf) -notlike '.chatgpt-switch-update-*' -or
        $source -ne (Join-Path $stage 'update.exe') -or
        [IO.Path]::GetExtension($target) -ne '.exe') { throw 'Invalid update paths.' }
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $plan.sha256) { throw 'Checksum mismatch.' }
    Set-Content -LiteralPath (Join-Path $stage 'ready') -Value 'ready'
    $old = Get-Process -Id $plan.parentPid -ErrorAction SilentlyContinue
    if ($old -and -not $old.WaitForExit(60000)) { throw 'The old application did not exit.' }
    # PyInstaller's parent bootloader may hold the old EXE briefly after the child exits.
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    do {
        try { [IO.File]::Replace($source, $target, $backup); $replaced = $true; break }
        catch { if ([DateTime]::UtcNow -ge $deadline) { throw }; Start-Sleep -Milliseconds 300 }
    } while ($true)
    $launched = Start-App
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $healthy = $false
    do {
        try {
            $session = Get-Content -LiteralPath (Join-Path $root 'data/session.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            $uri = [Uri]$session.url
            if ($session.pid -ne $plan.parentPid -and $uri.Host -eq '127.0.0.1') {
                $status = Invoke-RestMethod -Uri ($uri.GetLeftPart([UriPartial]::Authority) + '/api/state') -Headers @{Authorization=('Bearer ' + $uri.Fragment.Substring(1))} -TimeoutSec 2
                if ($status.version -eq $plan.version -and $status.uiReady -and $status.tray -eq 'ready') { $healthy = $true; break }
            }
        } catch {}
        if ($launched.HasExited) { break }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    if (-not $healthy) { throw 'The new application did not become ready.' }
    $safeCleanup = $true
} catch {
    $failure = $_.Exception.Message
    if ($replaced) {
        # Stop only the process we launched and its direct PyInstaller child.
        if ($launched) {
            Get-CimInstance Win32_Process -Filter "ParentProcessId = $($launched.Id)" -ErrorAction SilentlyContinue |
                Where-Object { $_.ExecutablePath -eq $target } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            if (-not $launched.HasExited) { Stop-Process -Id $launched.Id -Force -ErrorAction SilentlyContinue }
        }
        try {
            $deadline = [DateTime]::UtcNow.AddSeconds(15)
            do {
                try { [IO.File]::Replace($backup, $target, (Join-Path $stage 'failed.exe')); break }
                catch { if ([DateTime]::UtcNow -ge $deadline) { throw }; Start-Sleep -Milliseconds 300 }
            } while ($true)
            $null = Start-App
            $safeCleanup = $true
        } catch { $failure += ' Rollback failed; the previous EXE is retained at: ' + $backup }
    } elseif (-not (Get-Process -Id $plan.parentPid -ErrorAction SilentlyContinue)) {
        try { $null = Start-App; $safeCleanup = $true } catch {}
    } else { $safeCleanup = $true }
    # An actionable result survives even if the browser cannot open.
    $failure | Set-Content -LiteralPath (Join-Path $root 'update-error.log') -Encoding UTF8
    if ($plan.testRoot -ne $root) {
        try { Start-Process 'https://github.com/yiyinfaith/chatgpt-switch/releases' -WindowStyle Hidden } catch {}
    }
} finally {
    if ($safeCleanup -and (Split-Path $stage -Parent) -eq $root -and
        (Split-Path $stage -Leaf) -like '.chatgpt-switch-update-*') {
        Set-Location -LiteralPath $root
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
