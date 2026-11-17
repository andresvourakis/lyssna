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
