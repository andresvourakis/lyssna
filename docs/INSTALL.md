LYSSNA

Lyssna reads text aloud on your Mac and types what you say.
Select text in any app, press Control-Option-S, and listen.
Or hold fn, speak, and let go.

You need macOS 14 or later on Apple silicon (M1 or newer),
or macOS 13 or later on an Intel Mac.


INSTALL FROM THE ZIP

1. Unzip the download and drag Lyssna.app into your
   Applications folder.
2. Double-click Lyssna. macOS says it cannot verify the app.
   Click Done.
3. Open System Settings > Privacy & Security. Scroll down to
   the message about Lyssna and click Open Anyway. Confirm
   with your password if asked, then click Open.

You only do this once. macOS shows the warning because Lyssna
is not notarized by Apple. Notarizing needs a paid Apple
developer membership, and Lyssna is a free project. The source
code is public at https://github.com/andresvourakis/lyssna


OR INSTALL WITH ONE LINE

Paste this into Terminal and press Return:

  curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/install.sh | sh

It downloads the latest version for your Mac, checks it,
puts it in Applications and opens it. Installed this way,
there is no "cannot verify" warning.


FIRST LAUNCH

A setup window opens.

1. Allow Accessibility. Lyssna needs it to read the text you
   select and to hear its hotkeys. In System Settings, turn on
   Lyssna, then click Relaunch Lyssna. Lyssna does not record
   your keystrokes or your screen.
2. Choose a voice. The free voice that runs on your Mac is
   already selected. It downloads once (about 140 MB) and then
   works offline. Your text stays on your Mac.
   You can also paste an API key for a cloud voice instead.
3. Try it. Select some text anywhere and press
   Control-Option-S.
4. Dictate. Click Allow so Lyssna may use the microphone.
   macOS asks once; you can change it later in System
   Settings > Privacy & Security > Microphone.
   Then set the Globe key: open System Settings > Keyboard
   (the setup window has a button for it) and set
   "Press Globe key to" to Do Nothing. Otherwise holding fn
   also opens the emoji picker.
   Last, click Download for the speech models (about 1.1 GB,
   once), or paste your own OpenAI and Anthropic keys.

Lyssna lives in the menu bar at the top right of the screen.
To see the setup window again, click the Lyssna icon and
choose Setup…


HOTKEYS

  Control-Option-S        read the selected text
                          (the clipboard if nothing is selected)
  Control-Option-Space    pause or resume
  Control-Option-X        stop
  Control-Option-Right    next sentence
  Control-Option-Left     previous sentence
  Control-Option-=        faster
  Control-Option-minus    slower
  Control-Option-B        play button on or off

DICTATION

  Hold fn                 dictate while you hold it
  fn, then Space          keep dictating hands-free;
                          press fn again to finish
  Escape                  cancel a hands-free dictation

When you select text, a small play button appears above it.
Click it instead of using the hotkey if you like.
In an app that does not report its selection or copy it on
select, there is no button; the hotkey still works there.
While Lyssna reads, a small player floats on the screen.
Drag it to the left or right edge to tuck it away.
Settings > Start at Login in the menu opens Lyssna when you log in.


UPDATE

Run the one-line install command again. It replaces the old
version and keeps your settings.
Or download the new zip and replace Lyssna.app in
Applications.

If the hotkeys stop working after an update, open System
Settings > Privacy & Security > Accessibility and turn Lyssna
off and on again.


UNINSTALL

Paste this into Terminal:

  curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.sh | sh

This removes the app and keeps your settings. To also delete
settings, the audio cache, the dictation history, dictionary
and models, and the logs, use:

  curl -fsSL https://raw.githubusercontent.com/andresvourakis/lyssna/main/scripts/uninstall.sh | sh -s -- --purge

API keys you added stay in your Keychain. To remove them,
use Settings > API Keys in the menu before you uninstall,
or open Keychain Access and delete the items named
"Lyssna API key". You can also remove Lyssna from
System Settings > Privacy & Security > Accessibility and
Microphone.


HELP

If something goes wrong, click the Lyssna icon and choose
Settings > Open Log. Then open an issue at

  https://github.com/andresvourakis/lyssna/issues

Describe what happened, your macOS version and your Mac's
chip, and paste the relevant lines from the log.
