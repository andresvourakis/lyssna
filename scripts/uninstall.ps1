# Remove Lyssna from Windows.
#   irm https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.ps1 | iex
# To also delete your settings, downloaded voices, audio cache, logs and saved API keys:
#   $env:LYSSNA_PURGE = '1'; irm https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.ps1 | iex
#   or, run as a file: powershell -File uninstall.ps1 -Purge
# LYSSNA_INSTALL_DIR names the folder to remove (default %LOCALAPPDATA%\Programs\Lyssna).
# Runs on Windows PowerShell 5.1 and PowerShell 7. The twin of scripts/uninstall.sh.
param([switch]$Purge)

function Stop-LyssnaUninstall([string]$Message) {
    throw [System.Exception]::new($Message)
}

function Get-LyssnaProcesses([string]$Exe) {
    # Only the Lyssna.exe that runs from this exact file, never a copy installed elsewhere.
    $wanted = [System.IO.Path]::GetFullPath($Exe)
    return @(Get-Process -Name Lyssna -ErrorAction SilentlyContinue | Where-Object {
        $path = $null
        try { $path = $_.Path } catch { $path = $null }
        $path -and ([string]::Equals([System.IO.Path]::GetFullPath($path), $wanted, [StringComparison]::OrdinalIgnoreCase))
    })
}

function Stop-LyssnaRunning([string]$Dir) {
    # Ask the Lyssna running from $Dir to quit, wait up to 5 seconds, then stop what is left.
    $exe = Join-Path $Dir 'Lyssna.exe'
    if ((Get-LyssnaProcesses $exe).Count -eq 0) { return }
    Write-Host 'Quitting the running Lyssna...'
    $cli = Join-Path $Dir 'lyssna-cli.exe'
    if (Test-Path -LiteralPath $cli) {
        try {
            $quit = Start-Process -FilePath $cli -ArgumentList 'quit' -WindowStyle Hidden -PassThru
            $null = $quit.WaitForExit(5000)
        } catch { }
    }
    for ($i = 0; $i -lt 50; $i++) {
        if ((Get-LyssnaProcesses $exe).Count -eq 0) { return }
        Start-Sleep -Milliseconds 100
    }
    foreach ($p in (Get-LyssnaProcesses $exe)) {
        try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { }
    }
    Start-Sleep -Milliseconds 500
}

function Remove-LyssnaKeys {
    # Saved API keys live in Windows Credential Manager under io.github.andresvourakis.lyssna/<name>
    # (lyssna/win/credentials.py). cmdkey prints them as "LegacyGeneric:target=<name>" or "<name>",
    # with labels in the language of Windows, so the names are matched, not the labels.
    # No 2> redirection: on PowerShell 5.1 with ErrorActionPreference Stop it turns any line on
    # standard error into a terminating error.
    $listing = (& cmdkey.exe /list) -join "`n"
    $names = @([regex]::Matches($listing, 'io\.github\.andresvourakis\.lyssna/\S+') | ForEach-Object { $_.Value } | Sort-Object -Unique)
    foreach ($name in $names) {
        $null = & cmdkey.exe "/delete:$name"
        if ($LASTEXITCODE -ne 0) {
            $null = & cmdkey.exe "/delete:LegacyGeneric:target=$name"
        }
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Removed the saved key $name"
        } else {
            Write-Host "Could not remove the saved key $name. Remove it in Control Panel > Credential Manager > Windows Credentials."
        }
    }
}

function Invoke-LyssnaUninstall([bool]$PurgeAll) {
    $dir = $env:LYSSNA_INSTALL_DIR
    if (-not $dir) { $dir = Join-Path $env:LOCALAPPDATA 'Programs\Lyssna' }
    $dir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($dir).TrimEnd('\')

    if (Test-Path -LiteralPath (Join-Path $dir 'Lyssna.exe')) {
        Stop-LyssnaRunning $dir
        try {
            Remove-Item -LiteralPath $dir -Recurse -Force
        } catch {
            Stop-LyssnaUninstall "Could not remove $dir. A file in it may still be in use. Quit Lyssna and run this again."
        }
        Write-Host "Removed $dir"
    } elseif (Test-Path -LiteralPath $dir) {
        Write-Host "$dir does not hold Lyssna, so it was left alone."
    } else {
        Write-Host 'Lyssna was not found.'
    }

    $programs = [Environment]::GetFolderPath('Programs')
    if (-not $programs) { $programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs' }
    $shortcut = Join-Path $programs 'Lyssna.lnk'
    if (Test-Path -LiteralPath $shortcut) {
        Remove-Item -LiteralPath $shortcut -Force
        Write-Host "Removed the Start menu shortcut $shortcut"
    }

    # Start at login (lyssna/win/autostart.py): the Lyssna value of the per-user Run key.
    $run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $value = Get-ItemProperty -Path $run -Name Lyssna -ErrorAction SilentlyContinue
    if ($null -ne $value) {
        Remove-ItemProperty -Path $run -Name Lyssna
        Write-Host 'Removed Lyssna from the programs that start at login.'
    }

    if ($PurgeAll) {
        # The same folders lyssna/host.py uses on Windows.
        foreach ($path in @((Join-Path $env:APPDATA 'Lyssna'), (Join-Path $env:LOCALAPPDATA 'Lyssna'))) {
            if (Test-Path -LiteralPath $path) {
                try {
                    Remove-Item -LiteralPath $path -Recurse -Force
                    Write-Host "Removed $path"
                } catch {
                    Write-Host "Could not remove $path completely. Delete it by hand."
                }
            }
        }
        Remove-LyssnaKeys
    } else {
        Write-Host 'Your settings, voices and saved keys were kept. Run again with LYSSNA_PURGE=1 (or -Purge) to delete them too.'
    }
}

function Uninstall-Lyssna([bool]$PurgeAll) {
    $ErrorActionPreference = 'Stop'
    try {
        Invoke-LyssnaUninstall ($PurgeAll -or $env:LYSSNA_PURGE -eq '1')
    } catch {
        Write-Host "Lyssna uninstall: $($_.Exception.Message)"
        # Run as a file, fail with exit code 1. Through irm | iex, exit would close the window.
        if ($PSCommandPath) { exit 1 }
    }
}

Uninstall-Lyssna ([bool]$Purge)
