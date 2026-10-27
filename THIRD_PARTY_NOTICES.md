# Third-party notices

Zhuzi's own source code is MIT-licensed (see `LICENSE`). Binary releases bundle
the components below, each under its own license.

| Component | License | Source |
|---|---|---|
| Python 3.12 | PSF License | https://www.python.org/ |
| Qt 6 / PySide6 (QtCore, QtGui, QtWidgets) | LGPL-3.0 | https://code.qt.io/ · https://download.qt.io/official_releases/QtForPython/ |
| PyAV | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| FFmpeg (bundled by PyAV) | LGPL-3.0-or-later | https://ffmpeg.org/download.html |
| x264, x265 (linked into PyAV's FFmpeg; Zhuzi only decodes audio and never calls them) | GPL-2.0-or-later | https://code.videolan.org/videolan/x264 · https://bitbucket.org/multicoreware/x265_git |
| libopus, libvpx, dav1d, SVT-AV1, libwebp, zlib, libvmaf (bundled by PyAV) | BSD / zlib / ISC | see each project |
| NumPy (with OpenBLAS) | BSD-3-Clause | https://numpy.org/ |
| certifi (macOS builds: the CA certificates for HTTPS) | MPL-2.0 | https://github.com/certifi/python-certifi |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| sherpa-onnx | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| Silero VAD model (`assets/silero_vad_v6.onnx`) | MIT | https://github.com/snakers4/silero-vad |
| VAD logic in `src/vad.py`, adapted from faster-whisper | MIT (SYSTRAN) | https://github.com/SYSTRAN/faster-whisper |
| opencc-python-reimplemented | Apache-2.0 | https://github.com/yichen0831/opencc-python |
| pypinyin | MIT | https://github.com/mozillazg/python-pinyin |
| PyInstaller bootloader | GPL-2.0 with a linking exception | https://pyinstaller.org/ |

Because x264 and x265 are GPL-licensed, a binary release taken as a whole is
also distributable under the GPL; Zhuzi's MIT-licensed code is compatible with
that. The sources of every bundled component are available at the links above.

## Downloaded at run time, not bundled

| Component | License | Notes |
|---|---|---|
| SenseVoice-Small (int8, sherpa-onnx export) | FunASR Model License (commercial use permitted) | https://github.com/FunAudioLLM/SenseVoice · fetched on first offline use |

## Cloud services

Cloud transcription sends compressed audio to the provider the user chose,
under that provider's own terms: SiliconFlow (https://siliconflow.cn) or Groq
(https://groq.com). Zhuzi has no server of its own and collects nothing.
