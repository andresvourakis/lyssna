# Privacy

Lyssna is a menu bar app that reads selected text aloud. This page lists everything it touches, where it keeps it, and what leaves the Mac. It describes the code in this repository.

## In short

- With the local voice (Kokoro, the default) or the macOS voice, text never leaves the Mac.
- With a cloud voice, each sentence goes to that provider over HTTPS, with the user's own key.
- No analytics, no telemetry, no crash reporting, no update checks. Lyssna makes no network requests other than the ones listed below.
- An administrator can force local-only mode on managed Macs.

## What Lyssna touches, and where it is kept

| Data | Where | How long |
| ---- | ----- | -------- |
| Selected text | In memory only, while it is read | Until the reading ends |
| Clipboard | Read only as a fallback (see below) | Not stored |
| API keys | macOS login Keychain, service `io.github.andresvourakis.lyssna`, one item per provider (`inworld`, `cartesia`, `elevenlabs`, `openai`) | Until removed in **Settings > API Keys** or in Keychain Access |
| Audio cache | `~/Library/Caches/Lyssna/` | Capped at 500 MB, or off (see below) |
| Usage counter | `~/Library/Application Support/Lyssna/usage.json` | Up to 12 months |
| Local voice model | `~/Library/Application Support/Lyssna/kokoro/` | Until uninstalled |
| Settings | `~/.config/lyssna/config.toml` (readable only by the user) | Until uninstalled |
| Logs | `~/Library/Logs/Lyssna/lyssna.log` | Rotated at 1 MB, two older files kept |

**Selected text.** Lyssna asks the front app for the selection through the macOS Accessibility API. If the app does not expose it, Lyssna sends Command-C, reads the copied text, and about half a second later puts the previous clipboard back as it was: every item in every format, such as rich text, images and files. If the clipboard was empty, it is left empty. If nothing is selected, Lyssna reads whatever text is on the clipboard.

**API keys.** Keys are stored in the Keychain and are never written to the log. Two exceptions put a key in the config file instead: if the Keychain refuses to store it, or if the user writes it there by hand. The config file and its folder are readable only by the user's account. Keys can also come from environment variables such as `INWORLD_API_KEY`. **Settings > API Keys** removes a saved key from the Keychain and from the config file.

**Audio cache.** Each synthesized sentence is saved as an audio file, named by a SHA-256 hash of the voice provider, voice and text. The text itself is not stored, but the audio is a recording of it. The cache is capped by `cache_max_mb` in the config (default 500 MB; 0 means no cap). Past the cap, the files used longest ago are deleted first. **Settings > Clear Audio Cache** empties it at once. Set `cache = false` at the top of the config file to turn the cache off: no audio is then written to or read from disk. Files cached before that stay until **Clear Audio Cache**.

**Usage counter.** The number of characters read per paid voice provider per month, used for the cost estimate in the menu. Free voices (Kokoro and the macOS voice) are not counted. Each update drops months older than the last 12. No text, no timestamps beyond the month.

## What leaves the Mac

**Local voice (Kokoro) or macOS system voice:** nothing. The one exception is the one-time download of the Kokoro model files (about 140 MB) from the kokoro-onnx project's releases on GitHub (github.com). Lyssna sends nothing in that request beyond a normal HTTPS download, and checks each file against a fixed SHA-256 hash.

**Cloud voice (Inworld, Cartesia, ElevenLabs, OpenAI):** the text of each sentence, the voice and model name, and the user's key go over HTTPS to that provider:

| Voice provider | Endpoint |
| ------ | -------- |
| Inworld TTS-2, Inworld TTS-2 Flash | `api.inworld.ai` |
| Cartesia Sonic | `api.cartesia.ai` |
| ElevenLabs | `api.elevenlabs.io` |
| OpenAI TTS | `api.openai.com`, or the `base_url` set under `[openai]` (https only; plain http is allowed only to localhost) |

Lyssna sends up to four sentences ahead of the one playing, not the whole text at once. Sentences skipped over or never reached are not sent. Text served from the audio cache is not sent again. What the provider does with the text is governed by that provider's terms and the user's account with them. Adding a key also sends one fixed test sentence ("Hello from Lyssna.") to check that the key works.

The menu always shows where text goes with the current voice provider: "Text stays on this Mac" or "Text is sent to" the provider.

**Installer and uninstaller scripts** download from github.com and raw.githubusercontent.com. The app itself never checks for updates.

## What the logs contain

The log records events, not content. It never contains the text being read, clipboard contents, API keys or a provider's error responses. A reading writes lines such as `speaking 412 chars from selection (accessibility)` and `synthesized chunk 3 (88 chars) in 0.41s via inworld`: lengths, sentence numbers, timings, and the provider name. When a reading fails, the log records the provider, the error type, the HTTP status if there is one and the length of the error message, for example `chunk 3 failed via inworld: ProviderError, HTTP 401, 87 chars of detail not logged`. The message itself is only shown briefly on screen. Other lines record settings changes (voice provider, voice id, play button on or off), player window positions, display names and sizes, download progress and file paths in the user's home folder.

## Permissions

- **Accessibility.** Needed to read the selected text and to receive the global hotkeys. The keyboard listener looks at each key press only to check whether it is one of Lyssna's hotkeys. It takes those hotkeys for itself and passes every other key through unchanged; keys are not stored or logged. The mouse listener is listen-only: it notices drags and double-clicks so the play button can appear, and cannot block or change input. Nothing is recorded.
- **Network.** Only for the requests above.
- **Login item.** Only if the user turns on **Settings > Start at Login**.

The command line talks to the app through a Unix socket in `~/Library/Application Support/Lyssna/` that only the user's account can open.

## Enforcing local-only

- Per user: turn on **Settings > Only Use Voices on This Mac**, or set `local_only = true` at the top of `~/.config/lyssna/config.toml`. Every cloud voice provider is hidden and blocked.
- Per organization: set the managed preference `LocalOnly` to true in the domain `io.github.andresvourakis.lyssna`, for example with a configuration profile from your MDM. This overrides the user's setting, and the menu shows it as set by the organization.

Local-only mode does not change the audio cache. To keep no audio on disk either, also set `cache = false` in the config file. There is no managed preference for the cache.

## Removing everything

The uninstall script with `--purge` deletes the app and the four folders listed above (see [docs/INSTALL.md](docs/INSTALL.md)). API keys stay in the Keychain until removed in **Settings > API Keys** before uninstalling, or deleted in Keychain Access (items named "Lyssna API key").
