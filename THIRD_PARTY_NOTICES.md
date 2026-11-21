# Third-party notices

Lyssna is licensed under the GNU General Public License, version 3 or later
(see `LICENSE`). The app bundle includes the components below, each under its
own license. Licenses were taken from each package's installed metadata or
license file unless noted.

## The voice on this Mac

| Component | Version | License | Notes |
|---|---|---|---|
| kokoro-onnx | 0.6.1 | MIT | Runs the Kokoro model |
| onnxruntime | 1.32.0 (Apple Silicon), 1.23.2 (Intel) | MIT | |
| phonemizer | 3.6.0 | GPL-3.0-or-later | Turns text into phonemes |
| espeak-ng (library and data, shipped in espeakng-loader) | 1.52.0 | GPL-3.0-or-later | License from the espeak-ng project |
| espeakng-loader | 0.2.4 | MIT | No license in the package metadata; MIT per its repository |
| numpy | 2.x | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | |
| protobuf | 7.36.2 | BSD-3-Clause | |
| flatbuffers | 25.12.19 | Apache-2.0 | |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | |
| joblib | 1.6.0 | BSD-3-Clause | |
| cloudpickle | 3.1.2 | BSD-3-Clause | |
| attrs | 26.1.0 | MIT | |
| dlinfo | 2.0.0 | MIT | |
| typing-extensions | 4.16.0 | PSF-2.0 | |
| coloredlogs, humanfriendly | 15.0.1, 10.0 | MIT | Intel builds only (onnxruntime 1.23.2); license from PyPI |
| sympy, mpmath | 1.14.0, 1.4.1 | BSD-3-Clause | Intel builds only (onnxruntime 1.23.2); license from PyPI |

The Kokoro-82M model weights (hexgrad/Kokoro-82M) are licensed Apache-2.0.
They are not bundled: Lyssna downloads the ONNX export of them
(`kokoro-v1.0.int8.onnx` and `voices-v1.0.bin` from the kokoro-onnx releases)
the first time the voice is used.

## Dictation on this Mac

| Component | Version | License | Notes |
|---|---|---|---|
| onnx-asr | 0.12.0 | MIT | Runs the Parakeet speech model on the onnxruntime above |
| llama-cpp-python | 0.3.37 | MIT | Runs the S1-mini cleanup model. Apple Silicon builds only |
| llama.cpp and ggml (built into llama-cpp-python) | as vendored by llama-cpp-python 0.3.37 | MIT | Copyright (c) 2023-2026 The ggml authors; license from the llama.cpp source |
| Jinja2 | 3.1.6 | BSD-3-Clause | Used by llama-cpp-python. Apple Silicon builds only |
| MarkupSafe | 3.0.4 | BSD-3-Clause | Used by Jinja2. Apple Silicon builds only |
| diskcache | 5.6.3 | Apache-2.0 | Used by llama-cpp-python. Apple Silicon builds only |
| Common English words list (`lyssna/dictation/data/common_words.txt`) | generated 2026-11-17 | ESDB (SCOWL) permissive license | Made with https://app.aspell.net/create from the English Speller Database, Copyright 2000-2026 by Kevin Atkinson. The full notice is in `lyssna/dictation/data/common_words.LICENSE` |

numpy (listed above) is also used directly: dictation records and processes
audio as numpy arrays.

The dictation models are not bundled. Lyssna downloads them from Hugging Face
the first time dictation on this Mac is used:

- **Parakeet TDT 0.6B v2** and **Parakeet TDT 0.6B v3** by NVIDIA, licensed
  CC-BY-4.0 (https://creativecommons.org/licenses/by/4.0/). Model cards:
  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2 and
  https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3. Lyssna uses ONNX
  exports with int8 weights published by istupakov
  (https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx and
  https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx): the model was
  converted to ONNX and its weights quantized. Lyssna does not change it
  further.
- **"S1-mini" by "Superwhisper"** (https://huggingface.co/superwhisper/s1-mini-GGUF),
  the GGUF file `s1-mini-q4_k_m.gguf`, licensed "Apache-2.0 with an additional
  naming term": the model must always be identified as "S1-mini" by
  "Superwhisper", with that exact capitalization. Its LICENSE and NOTICE files
  are downloaded next to the weights. Apple Silicon only.

## The app

| Component | Version | License |
|---|---|---|
| Python | 3.11 | PSF-2.0 |
| rumps | 0.4.0 | BSD-3-Clause |
| PyObjC (pyobjc-core and the framework wrappers) | 12.2.2 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| httpcore | 1.0.9 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| anyio | 4.15.1 | MIT |
| idna | 3.19 | BSD-3-Clause |
| certifi | 2026.7.22 | MPL-2.0 |

## Fonts (the Windows app)

The Mac app draws with Apple's system fonts. Apple's fonts may not be used on
Windows, so the Windows app bundles these free fonts, shipped unchanged with
their licences in `lyssna/ui/assets/fonts/`.

| Component | Version | License | Notes |
|---|---|---|---|
| Inter (Regular, Medium, SemiBold, Bold, Italic; hinted TrueType) | 4.1 | SIL Open Font License 1.1 | Copyright (c) 2016 The Inter Project Authors (https://github.com/rsms/inter). The full licence is in `Inter-LICENSE.txt` |
| Literata 36pt SemiBold | 3.103 | SIL Open Font License 1.1 | Copyright 2017 The Literata Project Authors (https://github.com/googlefonts/literata). The full licence is in `Literata-LICENSE.txt` |
