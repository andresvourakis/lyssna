# Privacy

Lyssna is a menu bar app that reads selected text aloud and types what you dictate. This page lists everything it touches, where it keeps it, and what leaves the Mac. It describes the code in this repository.

## In short

- With the local voice (Kokoro, the default) or the macOS voice, text never leaves the Mac.
- With a cloud voice, each sentence goes to that provider over HTTPS, with the user's own key.
- Dictation runs on the Mac by default: the recording and the text never leave it. With an online engine, the recording or the text goes to that provider, with the user's own key.
- The microphone is on only while you dictate. Recordings stay in memory and are never saved or logged.
- No analytics, no telemetry, no crash reporting, no update checks. Lyssna makes no network requests other than the ones listed below.
- The Support Lyssna links open the Lyssna website in your browser, and only when you click one. The link carries nothing about you or your usage, and the app itself sends nothing.
- An administrator can force local-only mode on managed Macs. It covers reading and dictation.

## What Lyssna touches, and where it is kept

| Data | Where | How long |
| ---- | ----- | -------- |
| Selected text | In memory only, while it is read | Until the reading ends |
| Clipboard | Read as a fallback, to spot a copied selection, and used to paste dictation (see below) | Not stored |
| Microphone recording | In memory while you dictate; a failed one is saved in `~/Library/Application Support/Lyssna/dictation/failed/` (readable only by the user) | Until it is turned into text; a cancelled one is kept a few seconds for Undo; a failed one until it is retried, deleted or 24 hours old |
| Dictation history | `~/Library/Application Support/Lyssna/dictation/history.json` (readable only by the user) | 30 days by default (see below) |
| Dictionary | `~/Library/Application Support/Lyssna/dictation/vocabulary.json` (readable only by the user) | Until removed in **History > Dictionary** |
| Dictation models | `~/Library/Application Support/Lyssna/dictation/models/` | Until uninstalled |
| API keys | macOS login Keychain, service `io.github.andresvourakis.lyssna`, one item per provider (`inworld`, `cartesia`, `elevenlabs`, `openai`, `anthropic`) | Until removed in **Settings > API Keys** (the Anthropic key only in Keychain Access) or in Keychain Access |
| Audio cache | `~/Library/Caches/Lyssna/` | Capped at 500 MB, or off (see below) |
| Usage counter | `~/Library/Application Support/Lyssna/usage.json` | Up to 12 months |
| Local voice model | `~/Library/Application Support/Lyssna/kokoro/` | Until uninstalled |
| Settings | `~/.config/lyssna/config.toml` (readable only by the user) | Until uninstalled |
| Logs | `~/Library/Logs/Lyssna/lyssna.log` | Rotated at 1 MB, two older files kept |

**Selected text.** Lyssna asks the front app for the selection through the macOS Accessibility API. If the app does not expose it, Lyssna sends Command-C, reads the copied text, and about half a second later puts the previous clipboard back as it was: every item in every format, such as rich text, images and files. If the clipboard was empty, it is left empty. If nothing is selected, Lyssna reads whatever text is on the clipboard.

**The play button and the clipboard.** Some programs, such as terminal apps, copy what you select and do not tell the Accessibility API about it. So when you press the mouse, Lyssna notes the clipboard's change counter (a number, not the contents). After a drag or a double-click, if another app changed the clipboard meanwhile, Lyssna reads the clipboard's text to see whether it holds a word, and shows the play button if it does. Clicking that button reads the clipboard. Lyssna's own clipboard changes never count. The text is not stored, and the log records only its length.

**Microphone.** Lyssna turns the microphone on when you start a dictation and off when it ends, so the orange indicator in the menu bar shows only while you dictate. At launch it prepares the audio engine without turning the microphone on. The recording is kept in memory as audio samples, turned into text, then dropped. It is never logged. If a dictation fails, its recording is saved on this Mac so you can retry it. It is deleted when the retry works, when you delete the entry or clear History, or after 24 hours, whichever comes first. With history turned off it is never saved. When you cancel a dictation, the recording stays in memory for a few seconds so Undo can bring it back.

**Pasting dictated text.** macOS has no general way to type text into another app, so Lyssna puts the text on the clipboard, presses Command-V, and about half a second later puts the previous clipboard back (every item in every format), unless something else was copied in the meantime. The dictated text is marked on the clipboard as transient and concealed (the nspasteboard.org convention), so clipboard managers that follow it neither record nor show it. When no text field is in front, the text is left on the clipboard as a normal entry for you to paste.

**Learning corrections.** Right after a paste, Lyssna watches the text field it pasted into, so it can learn a name or word you correct. It reads that one field's text through the Accessibility API a few times a second, for at most a minute, and stops as soon as you move to another field, another app comes to the front or another dictation starts. The field's text stays in memory. Only the corrected word and the form it replaced (1 to 3 words each) are saved, in the dictionary. Turn this off with `auto_learn = false` under `[dictation]` in the config file.

**Dictation history.** Each dictation is kept so you can find it again in the **History** window: the pasted text, the speech model's transcript before any correction, the number of words, the seconds of audio and the engines used. Audio is kept only for a failed dictation, so you can retry it (see Microphone). Entries are deleted after 30 days; `history_days` under `[dictation]` changes that (0 keeps nothing and deletes what is there, -1 keeps everything). **Clear History…** in the History window deletes every entry. Totals for the window (words, dictations, seconds) are counts only and stay after the entries are deleted.

**Dictionary.** Names and words the speech model gets wrong, with the exact spelling and the short wrong forms they were heard as. Words come from you (typed in the History window) or from corrections Lyssna learned. A word you delete is remembered as deleted, so it is not learned again.

**API keys.** Keys are stored in the Keychain and are never written to the log. Two exceptions put a key in the config file instead: if the Keychain refuses to store it, or if the user writes it there by hand. The config file and its folder are readable only by the user's account. Keys can also come from environment variables such as `INWORLD_API_KEY`. **Settings > API Keys** removes a saved key from the Keychain and from the config file.

**Audio cache.** Each synthesized sentence is saved as an audio file, named by a SHA-256 hash of the voice provider, voice and text. The text itself is not stored, but the audio is a recording of it. The cache is capped by `cache_max_mb` in the config (default 500 MB; 0 means no cap). Past the cap, the files used longest ago are deleted first. **Settings > Clear Audio Cache** empties it at once. Set `cache = false` at the top of the config file to turn the cache off: no audio is then written to or read from disk. Files cached before that stay until **Clear Audio Cache**.

**Usage counter.** The number of characters read per paid voice provider per month, and for paid dictation engines the seconds of audio, the token counts and the estimated cost per month, used for the cost estimate in the menu. Free voices and the dictation engines on the Mac are not counted. Each update drops months older than the last 12. No text, no timestamps beyond the month.

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

**Dictation on this Mac (Parakeet and S1-mini, the default):** nothing. The one exception is the one-time download of the model files (about 1.1 GB on Apple silicon, about 660 MB on Intel, where only the speech model runs) from huggingface.co, which hands the large files to its download network (hosts under `cdn.hf.co`, such as `us.aws.cdn.hf.co`). Lyssna sends nothing in those requests beyond a normal HTTPS download, and checks each file against a fixed SHA-256 hash.

**Online dictation engines:** with the user's own key, over HTTPS:

| Engine | What is sent | Endpoint |
| ------ | ------------ | -------- |
| OpenAI (gpt-transcribe), speech to text | The recording as a WAV file, the model name, the language and up to 100 dictionary words as hints | `api.openai.com/v1/audio/transcriptions`, or the `base_url` set under `[openai]` |
| Inworld STT, speech to text | The recording as WAV audio, the model name, the language and up to 100 dictionary words as hints | `api.inworld.ai/stt/v1/transcribe` |
| Claude Haiku 5.5 (Anthropic), cleanup | The transcript, the dictionary words and Lyssna's fixed cleanup instructions and examples | `api.anthropic.com/v1/messages` |

Saving a dictation key in the setup window checks it with one request that costs nothing and sends no recording or text: a list of models, from `api.openai.com/v1/models` (or the `base_url`) or `api.anthropic.com/v1/models`. What the provider does with the audio or text is governed by that provider's terms and the user's account with them.

The menu shows where dictation goes: "Speech stays on this Mac", or "Speech is sent to" a provider, plus "text is sent to" a provider when an online engine cleans it up.

**Installer and uninstaller scripts** download from github.com and raw.githubusercontent.com. The app itself never checks for updates.

## What the logs contain

The log records events, not content. It never contains the text being read, dictated text or audio, clipboard contents, API keys or a provider's error responses. A reading writes lines such as `speaking 412 chars from selection (accessibility)` and `synthesized chunk 3 (88 chars) in 0.41s via inworld`: lengths, sentence numbers, timings, and the provider name. When a reading fails, the log records the provider, the error type, the HTTP status if there is one and the length of the error message, for example `chunk 3 failed via inworld: ProviderError, HTTP 401, 87 chars of detail not logged`. The message itself is only shown briefly on screen. A dictation writes lines with the seconds of audio, the number of words, the engines, timings and token counts. Other lines record settings changes (voice provider, voice id, play button on or off, dictation engines), player window positions, display names and sizes, download progress and file paths in the user's home folder.

## Permissions

- **Accessibility.** Needed to read the selected text, to receive the global hotkeys and the dictation key, to paste dictated text and to notice corrections right after a paste. The keyboard listener looks at each key press only to check whether it is one of Lyssna's hotkeys. It takes those hotkeys for itself and passes every other key through unchanged; keys are not stored or logged. The mouse listener is listen-only: it notices drags and double-clicks so the play button can appear, and cannot block or change input. Nothing is recorded.
- **Microphone.** Needed to dictate. macOS asks the first time you dictate, or when you click **Allow** in the setup window. The microphone is on only while you dictate.
- **Network.** Only for the requests above.
- **Login item.** Only if the user turns on **Settings > Start at Login**.

The command line talks to the app through a Unix socket in `~/Library/Application Support/Lyssna/` that only the user's account can open.

## Enforcing local-only

- Per user: turn on **Settings > Keep Everything on This Mac**, or set `local_only = true` at the top of `~/.config/lyssna/config.toml`. Every cloud voice provider and every online dictation engine is hidden and blocked, so the text you read, your recordings and your dictated text stay on the Mac.
- Per organization: set the managed preference `LocalOnly` to true in the domain `io.github.andresvourakis.lyssna`, for example with a configuration profile from your MDM. This overrides the user's setting, and the menu shows it as set by the organization.

Local-only mode does not change the audio cache or the dictation history. To keep no audio on disk either, also set `cache = false` in the config file; to keep no dictated text, set `history_days = 0` under `[dictation]`. There is no managed preference for either.

## Removing everything

The uninstall script with `--purge` deletes the app and its four folders (`~/.config/lyssna`, `~/Library/Application Support/Lyssna` with the dictation history, dictionary and models, `~/Library/Caches/Lyssna` and `~/Library/Logs/Lyssna`); see [docs/INSTALL.md](docs/INSTALL.md). API keys stay in the Keychain until removed in **Settings > API Keys** before uninstalling, or deleted in Keychain Access (items named "Lyssna API key").
