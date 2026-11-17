# Security

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private
reporting instead: on this repository, go to Security, then "Report a
vulnerability". You will get a reply within a week.

## What the app can access

- Accessibility permission: used to read the text you have selected and to
  listen for the global hotkeys. The mouse listener is listen-only. Nothing is
  recorded.
- Your API keys: stored in the macOS login Keychain (service
  `io.github.andresvourakis.lyssna`). If the Keychain refuses a key, it is
  saved in `~/.config/lyssna/config.toml` instead, readable only by your user
  account. Keys are never logged, and each key is sent only to the voice
  provider it belongs to. **Settings > API Keys** removes a saved key.
- The text you ask it to read: sent to the voice provider you selected. The
  local voice (Kokoro) and the macOS voice send nothing off your Mac. The log
  never contains it.
- The command line talks to the app through a Unix socket that only your user
  account can open.

Lyssna has no analytics, telemetry or update checks. See [PRIVACY.md](PRIVACY.md)
for what is stored where and what leaves the Mac.
