LYSSNA

Lyssna reads text aloud on your Mac and types what you say.

You need macOS 14 or later on Apple silicon (M1 or newer),
or macOS 13 or later on an Intel Mac.


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


HELP

If something goes wrong, click the Lyssna icon and choose
Settings > Open Log. Then open an issue at

  https://github.com/andresvourakis/lyssna/issues
