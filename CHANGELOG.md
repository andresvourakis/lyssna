# Changelog

## 0.3.1

- The full player moves freely when you drag it. It no longer docks when it
  comes near a screen edge; use its tuck button for that.

## 0.3.0

- Dictation. Hold fn and speak, and Lyssna types what you said, cleaned up,
  wherever your cursor is. Press Space while holding fn to keep going
  hands-free. It runs on your Mac by default (Parakeet for speech, S1-mini by
  Superwhisper for the cleanup, a one-time 1.1 GB download). Online engines
  with your own keys are optional: OpenAI or Inworld for speech, Claude Haiku
  for the cleanup.
- A dictionary that learns: fix a name right after a dictation and Lyssna
  spells it right next time. Turn it off with `auto_learn = false`.
- A History window with what you dictated (kept 30 days by default) and the
  dictionary.
- A quiet way to support Lyssna, in three places you open yourself: a Support
  Lyssna row in the menu, a line under the totals in the History window (once
  you have dictated 1,000 words), and a line in the new What's New window.
  Nothing pops up. The links stay hidden until the website has an address
  (`SITE_URL` in `lyssna/support.py`).
- After an update the menu offers a What's New row with the highlights of that
  version. It stays until you open it once, and a first install never shows it.
- The setup window has a dictation step: the microphone, the Globe key
  setting, the model download, or your OpenAI and Anthropic keys, each checked
  with a request that costs nothing.
- The play button also appears in programs that copy what you select instead
  of reporting it, such as terminal apps. It then reads the clipboard.
- The play button no longer appears after a screenshot drag or other gestures
  that select nothing. It shows only when the app reports a new selection or
  copies what you select. Chrome, Slack and other apps that keep this
  information off are asked once to turn it on. An app that still reports
  nothing gets no button; Read Selection (Control-Option-S) works there.
- Launch no longer waits for the Keychain. After an update macOS may ask for
  your password before Lyssna can read its saved keys; the menu bar icon now
  appears at once, and your chosen voice is kept while the question is open.
- New name: Speechy is now Lyssna. On first launch Lyssna picks up the Speechy
  settings, usage figures and audio cache, so nothing needs to be set up again.
- A free voice that runs on your Mac (Kokoro) is now the default. No account or
  key needed; it downloads once (about 140 MB) and then works offline.
- A setup window on first launch: allow Accessibility, choose a voice, try it.
  Open it again from the menu with Setup….
- API keys are stored in the macOS Keychain. Keys already in the config file are
  moved there once.
- The audio cache has a size limit (500 MB by default). The audio played longest
  ago is deleted first.
- A one-line installer and updater, plus an uninstaller.
- Local-only mode: Settings > Keep Everything on This Mac hides and blocks every
  cloud voice and online dictation engine. Organizations can enforce it with a
  managed preference.
- The menu shows where your text goes with the current voice.
- The menu has two sections, Listen and Dictate, and each engine picker lists
  what runs on this Mac apart from the online engines. Pause, Stop and the
  sentence controls show only while Lyssna reads.
- The setup window matches the menu: a Listen / Dictate switch, the same three
  steps on each (permission, voice or engine, try it), and a Done button that
  stays in view at the bottom.
- When nothing plays, the pill rests at the screen edge as the wave from the
  Lyssna logo instead of a plain capsule.
- The resting wave hides until the cursor comes near its spot on the screen
  edge, then slides in. Turn it off with Settings > Auto-hide Squiggle.
- The resting wave waves now and then: about every 10 seconds a ripple runs
  along it while it sits in view. Not with Reduce Motion on in the system
  settings. Turn it off with Settings > Squiggle Wave (or `ui.wave = false`).
- The OpenAI voice provider accepts a custom `base_url`, for company gateways.
- License changed to GPL-3.0-or-later, because the local voice bundles GPL
  components.
- A shorter menu: Read Selection, Read Clipboard, Voice Provider, a Settings
  submenu and About Lyssna. When Accessibility is missing, the menu starts with
  Allow Accessibility Access….
- Saved API keys can be removed from Settings > API Keys.
- The audio cache can be turned off with `cache = false` in the config file.
- When Lyssna has to copy the selection with Command-C, it now puts the whole
  previous clipboard back (rich text, images, files), and an empty clipboard
  stays empty.
- The log never contains the text being read or a provider's error message. A
  failed reading logs the provider, the error type, the HTTP status and a length.
- The usage line appears only when a paid voice was used this month. Free voices
  are not counted, and only the last 12 months are kept.
- The installer checks for macOS 14 on Apple silicon and macOS 13 on Intel.

## 0.2.0

- Voice picker for engines that offer several voices.
- Intel build.
- Stable code signing, so macOS keeps the Accessibility grant across updates.
- The play button now appears only after a real text selection.

## 0.1.0

- First version: read the selection with a hotkey, floating player, menu bar
  controls, command line tool, several voice engines.
