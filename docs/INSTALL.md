LYSSNA

Lyssna reads text aloud on your Mac or Windows PC and types
what you say.

The Mac steps come first. The Windows steps are further down,
under WINDOWS.

On a Mac you need macOS 14 or later, on Apple silicon (M1 or
newer) or on an Intel Mac.


INSTALL

1. Drag Lyssna.app into your Applications folder.
2. Open Terminal (press Command-Space, type Terminal, press
   Return). Paste this line and press Return:

  xattr -dr com.apple.quarantine /Applications/Lyssna.app

3. Open Lyssna.

Do not skip step 2. Without it, macOS says "Lyssna is damaged
and can't be opened" and only offers Move to Trash.


WHY macOS SAYS "DAMAGED"

The app is not damaged. macOS shows that message for any
downloaded app Apple has not checked. Getting Apple's check
needs a paid Apple developer membership, and Lyssna is a free
project.

The line in step 2 removes the "downloaded" mark from Lyssna,
and from nothing else. You only do it once.

If you already saw the message: click Cancel (not Move to
Trash), do step 2, and open Lyssna again.


OR INSTALL WITH ONE LINE

This does everything above for you. Paste it into Terminal
and press Return:

  curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/install.sh | sh

It downloads the latest version for your Mac, checks it,
puts it in Applications and opens it. There is no "damaged"
message this way.


FIRST LAUNCH

A setup window opens and walks you through it: allow
Accessibility, choose a voice, allow the microphone and
download the speech models.

Lyssna lives in the menu bar at the top right of the screen.
Click its icon to see the hotkeys and settings, or choose
Setup… to open the setup window again.


UPDATE

Run the one-line install command again. It replaces the old
version and keeps your settings.
Or download the new zip, replace Lyssna.app in Applications,
and repeat step 2.

If the hotkeys stop working after an update, open System
Settings > Privacy & Security > Accessibility and turn Lyssna
off and on again.


UNINSTALL

Paste this into Terminal:

  curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.sh | sh

This removes the app and keeps your settings. To also delete
settings, history, models and logs, add  -s -- --purge  to
the end of the line.

API keys you added stay in your Keychain. Remove them under
Settings > API Keys in the menu before you uninstall.


WINDOWS

You need 64-bit Windows 10 (version 1809 or later) or
Windows 11. Lyssna is built for x64 PCs. On a PC with an ARM
processor it runs through the x64 emulation built into
Windows.


INSTALL ON WINDOWS WITH ONE LINE

Open PowerShell (press the Windows key, type PowerShell, press
Enter). Paste this line and press Enter:

  irm https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/install.ps1 | iex

It looks up the latest Lyssna release on GitHub, downloads
Lyssna-<version>-windows-x64.zip, checks it against its
SHA-256 checksum, and installs Lyssna in

  %LOCALAPPDATA%\Programs\Lyssna

for your Windows account only. It needs no administrator
rights. It adds Lyssna to the Start menu and opens it. If the
download does not match its checksum, nothing is installed.


OR INSTALL FROM THE ZIP

1. Download Lyssna-<version>-windows-x64.zip from the latest
   release:

     https://github.com/andresvourakis/lyssna/releases/latest

2. Right-click the zip, choose Extract All, and pick a folder
   you will keep. The zip holds one folder, Lyssna.
3. Open the Lyssna folder and double-click Lyssna.exe.

The zip route adds no Start menu entry. To find Lyssna later,
open the same folder again.


WHY WINDOWS SAYS IT "PROTECTED YOUR PC"

The first time you open Lyssna, Windows may show a blue
window that says "Windows protected your PC". The app is not
harmful. Windows shows this for apps that are not signed with a
paid code signing certificate, and Lyssna is a free project.

Click More info, then Run anyway. You only do this once.


FIRST LAUNCH ON WINDOWS

A setup window opens and walks you through it: choose a voice,
check the microphone and download the speech models.

Lyssna lives by the clock at the bottom right of the screen.
If you do not see its icon, click the small arrow there. Click
the icon to see the hotkeys and settings, or choose Setup… to
open the setup window again.

Windows does not ask each app for the microphone. Lyssna can
use it when these switches are on in Settings > Privacy &
security > Microphone: microphone access, and the one that
lets desktop apps use the microphone. They are on unless
someone turned them off.

To start Lyssna when you sign in to Windows, click its icon
and turn on Settings > Start at Login.

To dictate, hold Ctrl+Win, speak, and let go. For hands-free
dictation, press Space while holding Ctrl+Win, or tap Ctrl+Win
twice quickly. Press Ctrl+Win again to finish.


UPDATE ON WINDOWS

Run the one-line install command again. It quits the running
Lyssna, replaces the old version and keeps your settings.
If you installed from the zip, quit Lyssna, then replace the
Lyssna folder with the one from the new zip.


UNINSTALL ON WINDOWS

Paste this into PowerShell:

  irm https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.ps1 | iex

This removes the app from %LOCALAPPDATA%\Programs\Lyssna, the
Start menu entry and the start at login entry. It keeps your
settings, history, models, logs and saved API keys.

To delete those too, paste this instead:

  $env:LYSSNA_PURGE = '1'; irm https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.ps1 | iex

That also deletes these two folders and the API keys Lyssna
saved in Windows Credential Manager:

  %APPDATA%\Lyssna         (settings)
  %LOCALAPPDATA%\Lyssna    (history, dictionary, models,
                            audio cache and logs)

If you installed from the zip, quit Lyssna, turn off Settings >
Start at Login, and delete the Lyssna folder yourself. To
remove your data by hand, delete the two folders above. Saved
API keys are in Control Panel > Credential Manager > Windows
Credentials, under names that start with
io.github.andresvourakis.lyssna/.


HELP

If something goes wrong, click the Lyssna icon and choose
Settings > Open Log. Then open an issue at

  https://github.com/andresvourakis/lyssna/issues
