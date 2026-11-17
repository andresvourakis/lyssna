# Changelog

## Unreleased

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
- Local-only mode: Settings > Only Use Voices on This Mac hides and blocks every
  cloud voice. Organizations can enforce it with a managed preference.
- The menu shows where your text goes with the current voice.
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
