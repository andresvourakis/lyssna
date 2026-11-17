# Choosing a voice

Lyssna can read with seven voice providers. Two run on your Mac and are free. Five are cloud services where you bring your own API key and pay the provider directly.

Switch providers from the menu bar under **Voice Provider**. For providers with a voice list (Kokoro and Inworld), pick the voice under **Voice** or from the player. For the others, set `voice` in that provider's section of the config file (**Settings > Edit Config File…**).

## How they compare

The Artificial Analysis speech arena ranks voices by blind human preference. The scores below are a snapshot from **September 2026**. Rankings move and prices change, so check the providers before you decide.

| Provider in Lyssna  | Model                  | Arena Elo (Sept 2026) | Price per 1M characters | Notes |
| ------------------- | ---------------------- | --------------------- | ----------------------- | ----- |
| Cartesia Sonic      | sonic-3.6              | 1275 (#1)             | about $50               | The most natural voice in the arena. |
| Inworld TTS-2       | inworld-tts-2          | 1244 (#2)             | $25                     | Nearly as good at half the price. |
| Inworld TTS-2 Flash | inworld-tts-2-flash    | 1211                  | $15                     | Cheaper and faster, a little flatter. Same key as Inworld TTS-2. |
| ElevenLabs          | eleven_v3 (default)    | 1166                  | $100                    | Expressive, and the most expensive. |
| ElevenLabs          | eleven_flash_v2_5      | 1078                  | about $50               | Set `model = "eleven_flash_v2_5"` under `[elevenlabs]`. |
| OpenAI TTS          | gpt-4o-mini-tts        | not in the snapshot   | about $12               | Cheap and steerable, less natural than Inworld or Cartesia. |
| OpenAI TTS          | tts-1-hd               | 1104                  | see OpenAI's pricing    | Set `model = "tts-1-hd"` under `[openai]`. |
| **Kokoro** (default) | Kokoro-82M, on your Mac | 1059                 | free                    | No account, no key, works offline. Text never leaves the Mac. |
| macOS system voice  | the system voice       | not ranked            | free                    | No setup at all. Fine for short reads. |

The prices are the ones Lyssna uses for its monthly estimate in the menu. They are list prices; plans and free tiers can make them lower.

## What a month costs

A heavy reader listens to about five hours a month, roughly 300,000 characters. At the prices above:

- Kokoro or the macOS system voice: $0
- OpenAI (gpt-4o-mini-tts): about $4
- Inworld TTS-2: about $7.50 (Inworld TTS-2 Flash: about $4.50)
- Cartesia Sonic: about $15
- ElevenLabs (eleven_v3): about $30

Lyssna only synthesizes up to four sentences ahead of the one playing. If you stop halfway through an article, you do not pay for the rest. Audio is cached, so reading the same text again with the same voice costs nothing.

## Getting a key

| Provider   | Where to get a key  | Environment variable (optional) |
| ---------- | ------------------- | ------------------------------- |
| Inworld    | platform.inworld.ai | `INWORLD_API_KEY`               |
| Cartesia   | play.cartesia.ai    | `CARTESIA_API_KEY`              |
| ElevenLabs | elevenlabs.io       | `ELEVENLABS_API_KEY`            |
| OpenAI     | platform.openai.com | `OPENAI_API_KEY`                |

Add a key from the menu bar with **Setup…** or **Settings > API Keys**. Lyssna tries a short test reading with it, then stores it in the macOS Keychain. Inworld TTS-2 and Inworld TTS-2 Flash share one key.

If the provider in your config file has no key when Lyssna starts, it switches to Kokoro.

## The voices on your Mac

**Kokoro** is the default. It runs the open Kokoro-82M model (Apache-2.0) on the CPU. The first time it reads, it downloads its model files once, about 140 MB, from the kokoro-onnx project on GitHub, and checks each file against a fixed SHA-256 hash. After that it works offline. Voices: Heart, Bella, Nicole, Michael and Fenrir (US English), Emma, Isabella and George (UK English). It has been tested on Apple silicon; the Intel build includes it but has not been tested on Intel hardware yet.

**The macOS system voice**, shown as **On this Mac (System Voice)** in the menu, uses the system speech voice. For a much better result, download a Premium voice in System Settings > Accessibility > Spoken Content > System Voice > Manage Voices. Lyssna picks the best installed voice by itself, or set `voice` under `[say]`.
