# Install or update Lyssna on Windows, the app that reads selected text aloud.
#   irm https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/install.ps1 | iex
# Run it again at any time to update. No administrator rights are needed: Lyssna is installed
# for the current user only, in %LOCALAPPDATA%\Programs\Lyssna.
# Overrides, mainly for testing:
#   LYSSNA_ZIP_URL      zip to install instead of the latest release (a URL or a file path)
#   LYSSNA_SHA256       expected SHA-256 of that zip (default: read from <zip>.sha256)
#   LYSSNA_INSTALL_DIR  the folder Lyssna goes in (default %LOCALAPPDATA%\Programs\Lyssna)
#   LYSSNA_NO_OPEN=1    do not start Lyssna afterwards
# Runs on Windows PowerShell 5.1 and PowerShell 7. The twin of scripts/install.sh.

# Everything happens inside functions, and the only call is on the last line, so a download cut
# short by the network runs nothing at all.

function Stop-LyssnaInstall([string]$Message) {
    throw [System.Exception]::new($Message)
}

function Get-LyssnaBuild {
    # The build number tells Windows versions apart (17763 is Windows 10 version 1809).
    $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $build = 0
    try { $build = [int](Get-ItemProperty -Path $key -Name CurrentBuildNumber).CurrentBuildNumber } catch { $build = 0 }
    if ($build -eq 0) { $build = [Environment]::OSVersion.Version.Build }
    return $build
}

function Test-LyssnaArm64 {
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { return $true }
    try {
        $key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
        return ((Get-ItemProperty -Path $key -Name PROCESSOR_ARCHITECTURE).PROCESSOR_ARCHITECTURE -eq 'ARM64')
    } catch {
        return $false
    }
}

function Get-LyssnaLatestTag([string]$Repo) {
    # The latest-release page redirects to .../releases/tag/v<version>. Reading the redirect
    # avoids the GitHub API and its rate limit.
    $url = "https://github.com/$Repo/releases/latest"
    try {
        $request = [System.Net.WebRequest]::Create($url)
        $request.Method = 'HEAD'
        $request.AllowAutoRedirect = $true
        $request.UserAgent = 'lyssna-install'
        $response = $request.GetResponse()
        $final = $response.ResponseUri.AbsoluteUri
        $response.Close()
    } catch {
        Stop-LyssnaInstall "Could not reach https://github.com/$Repo/releases. Check your internet connection and try again."
    }
    $tag = $final.TrimEnd('/').Split('/')[-1]
    if ($tag -notmatch '^v[0-9]+(\.[0-9]+)*$') {
        Stop-LyssnaInstall "No Lyssna release is published yet at https://github.com/$Repo/releases."
    }
    return $tag
}

function Get-LyssnaLocalPath([string]$Source) {
    # A file path, or a file:// URL, as a local path. Anything else returns $null.
    if ($Source -match '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
        $uri = [Uri]$Source
        if ($uri.IsFile) { return $uri.LocalPath }
        return $null
    }
    return $Source
}

function Save-LyssnaFile([string]$Source, [string]$Target) {
    # Download a URL, or copy a local file, to $Target. Returns $false when it cannot.
    $local = Get-LyssnaLocalPath $Source
    try {
        if ($null -ne $local) {
            Copy-Item -LiteralPath $local -Destination $Target -Force
        } else {
            Invoke-WebRequest -Uri $Source -OutFile $Target -UseBasicParsing -UserAgent 'lyssna-install'
        }
        return $true
    } catch {
        return $false
    }
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
    $running = Get-LyssnaProcesses $exe
    if ($running.Count -eq 0) { return }
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
    for ($i = 0; $i -lt 30; $i++) {
        if ((Get-LyssnaProcesses $exe).Count -eq 0) { return }
        Start-Sleep -Milliseconds 100
    }
    Stop-LyssnaInstall "Could not quit the running Lyssna in $Dir. Quit it from its icon by the clock, then run this again."
}

function Find-LyssnaApp([string]$Folder) {
    # The release zip holds a Lyssna folder with the app; accept the app at the top level too.
    foreach ($candidate in @((Join-Path $Folder 'Lyssna'), $Folder)) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'Lyssna.exe')) { return $candidate }
    }
    return $null
}

function New-LyssnaShortcut([string]$Exe, [string]$Dir) {
    $programs = [Environment]::GetFolderPath('Programs')  # %APPDATA%\Microsoft\Windows\Start Menu\Programs
    if (-not $programs) { $programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs' }
    $null = New-Item -ItemType Directory -Path $programs -Force
    $path = Join-Path $programs 'Lyssna.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $Exe
    $link.WorkingDirectory = $Dir
    $link.IconLocation = "$Exe,0"
    $link.Description = 'Read selected text aloud'
    $link.Save()
    return $path
}

function Invoke-LyssnaInstall {
    $repo = 'andresvourakis/lyssna'  # the public GitHub repo; scripts/release.sh has the same value

    $build = Get-LyssnaBuild
    if (-not [Environment]::Is64BitOperatingSystem) {
        Stop-LyssnaInstall 'Lyssna needs 64-bit Windows 10 or later. This PC runs 32-bit Windows.'
    }
    if ($build -lt 17763) {
        Stop-LyssnaInstall "Lyssna needs Windows 10 version 1809 or later. This PC has Windows build $build. Update Windows in Settings > Windows Update, then run this again."
    }
    if (Test-LyssnaArm64) {
        Write-Host 'This PC has an ARM processor. Lyssna runs on it through the x64 emulation built into Windows.'
    }

    $zipUrl = $env:LYSSNA_ZIP_URL
    if (-not $zipUrl) {
        Write-Host 'Looking up the latest Lyssna release...'
        $tag = Get-LyssnaLatestTag $repo
        $version = $tag.Substring(1)
        $zipUrl = "https://github.com/$repo/releases/download/$tag/Lyssna-$version-windows-x64.zip"
    }
    $zipName = ($zipUrl -replace '\\', '/').TrimEnd('/').Split('/')[-1]
    if (-not $zipName.EndsWith('.zip', [StringComparison]::OrdinalIgnoreCase)) { $zipName = 'Lyssna.zip' }

    $dest = $env:LYSSNA_INSTALL_DIR
    if (-not $dest) { $dest = Join-Path $env:LOCALAPPDATA 'Programs\Lyssna' }
    # Resolved against PowerShell's current folder (.NET keeps a different one).
    $dest = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($dest).TrimEnd('\')
    $parent = Split-Path -Parent $dest
    if ((Test-Path -LiteralPath $dest) -and -not (Test-Path -LiteralPath (Join-Path $dest 'Lyssna.exe'))) {
        if (@(Get-ChildItem -LiteralPath $dest -Force).Count -gt 0) {
            Stop-LyssnaInstall "The folder $dest exists and does not hold Lyssna. Nothing was installed. Empty it or choose another folder with LYSSNA_INSTALL_DIR."
        }
    }

    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('lyssna-install-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $tmp
    $unpack = $null
    try {
        Write-Host "Downloading $zipName..."
        $zip = Join-Path $tmp $zipName
        if (-not (Save-LyssnaFile $zipUrl $zip)) {
            Stop-LyssnaInstall "Could not download $zipUrl. The release may not include a Windows build yet."
        }

        $expected = $env:LYSSNA_SHA256
        if (-not $expected) {
            $sumFile = Join-Path $tmp 'sum'
            if (-not (Save-LyssnaFile "$zipUrl.sha256" $sumFile)) {
                Stop-LyssnaInstall "Could not download the checksum file $zipUrl.sha256."
            }
            $expected = ((Get-Content -LiteralPath $sumFile -Raw).Trim() -split '\s+')[0]
        }
        $actual = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        $expected = $expected.Trim().ToLowerInvariant()
        if ($actual -ne $expected) {
            Stop-LyssnaInstall "The download is damaged or was changed (SHA-256 $actual, expected $expected). Nothing was installed."
        }

        # Unpack next to the destination, then swap folders, so a failed unpack leaves the old
        # install as it was.
        $null = New-Item -ItemType Directory -Path $parent -Force
        $unpack = Join-Path $parent ('.Lyssna-installing-' + [Guid]::NewGuid().ToString('N'))
        try {
            try { Add-Type -AssemblyName System.IO.Compression.FileSystem } catch { }
            [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $unpack)
        } catch {
            # The slower built-in way, in case the .NET one is not available.
            try {
                Remove-Item -LiteralPath $unpack -Recurse -Force -ErrorAction SilentlyContinue
                Expand-Archive -LiteralPath $zip -DestinationPath $unpack -Force
            } catch {
                Stop-LyssnaInstall "Could not unpack $zipName into $parent. Nothing was installed."
            }
        }
        $src = Find-LyssnaApp $unpack
        if (-not $src) { Stop-LyssnaInstall "$zipName does not contain Lyssna.exe. Nothing was installed." }

        Stop-LyssnaRunning $dest

        $old = $null
        if (Test-Path -LiteralPath $dest) {
            $old = Join-Path $parent ('.Lyssna-old-' + [Guid]::NewGuid().ToString('N'))
            try {
                [System.IO.Directory]::Move($dest, $old)
            } catch {
                Stop-LyssnaInstall "Could not replace $dest. A file in it may still be in use. Close Lyssna and run this again."
            }
        }
        try {
            [System.IO.Directory]::Move($src, $dest)
        } catch {
            if ($old) { try { [System.IO.Directory]::Move($old, $dest) } catch { } }
            Stop-LyssnaInstall "Could not move Lyssna into $dest. The previous install was kept."
        }
        if ($old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
    } finally {
        if ($unpack) { Remove-Item -LiteralPath $unpack -Recurse -Force -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    $exe = Join-Path $dest 'Lyssna.exe'
    Write-Host "Installed Lyssna in $dest"
    $shortcut = New-LyssnaShortcut $exe $dest
    Write-Host "Added Lyssna to the Start menu ($shortcut)"

    $opened = $env:LYSSNA_NO_OPEN -ne '1'
    if ($opened) {
        Start-Process -FilePath $exe -WorkingDirectory $dest
    }
    Write-Host ''
    Write-Host 'Next:'
    $n = 1
    if (-not $opened) {
        Write-Host "$n. Open Lyssna from the Start menu."
        $n++
    }
    Write-Host "$n. Look for the Lyssna icon by the clock. It may be hidden behind the small arrow there."
    $n++
    Write-Host "$n. Pick a voice in the setup window."
    $n++
    Write-Host "$n. Select text anywhere and press Ctrl+Alt+S to hear it."
    Write-Host ''
    Write-Host 'To update Lyssna later, run the same line again.'
}

function Install-Lyssna {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'  # the progress bar makes downloads many times slower on 5.1
    # Windows PowerShell 5.1 may not offer TLS 1.2 by default, and GitHub requires it.
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-LyssnaInstall
    } catch {
        Write-Host "Lyssna install: $($_.Exception.Message)"
        # Run as a file (powershell -File install.ps1), fail with exit code 1. Through irm | iex,
        # exit would close the PowerShell window, so it only stops here.
        if ($PSCommandPath) { exit 1 }
    }
}

Install-Lyssna
