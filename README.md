# Goggles HDMI

Goggles 3 / Avata 2 live-view output with grading, stabilization and watermark authentication.

## Windows

The published Windows 10/11 x64 release is **1.1.0**. A Windows **1.2.0** installer adds copyable troubleshooting information after package validation.

[Download Windows installer](https://github.com/contentriumkorea/goggles-hdmi/releases/download/v1.1.0/Goggles-HDMI-Setup-1.1.0.exe) · [Windows release](https://github.com/contentriumkorea/goggles-hdmi/releases/tag/v1.1.0)

Existing Windows clients retain their signed `releases/latest/download/release.json` update channel. Mac assets never replace that manifest or the Windows download.

The **문제 정보 복사** button works before connecting and while retrying. It copies stable `GH-*` error codes, observed connection stages, software/OS details and counters for pasting into a support chat. It uses cached state, never sends data online, and excludes passwords, serial numbers, personal paths, raw logs and video data. Recent errors remain available after recovery.

## Apple Silicon macOS

The native **arm64 1.2.0 preview** targets compatible Apple Silicon Macs running **macOS 15.6 or newer**. One package serves M1/M2/M3/M4 and later compatible M-series chips; it does not imply every model was physically tested. Intel Macs, Windows ARM and Linux are outside this release.

[Apple Silicon preview release](https://github.com/contentriumkorea/goggles-hdmi/releases/tag/macos-arm64-v1.2.0-preview) · [Installation and hardware test details](docs/macos-release-notes.md)

**Hardware validation is still required.** Native CI validates the packaged application; no Mac/Goggles 3/external display combination has yet been tested. The app is ad-hoc signed, the pkg is unsigned, and this preview is not Apple notarized. macOS may require its per-app Open Anyway action when offered.

Enable OTG wired computer connection and live-view sharing on the goggles, connect directly using a data cable, and close other DJI USB clients. The macOS backend uses libusb RNDIS in userspace and never automatically detaches or resets drivers. Cmd+D/Ctrl+D/Escape release output while the application is active. Authentication persistence uses Keychain; updates open Apple's Installer after signature/hash/size checks and still require OS confirmation.

## Source and builds

The repository contains the selected application source, public password verifier and Ed25519 verification key, resources, tests and native build workflow. Private release signing keys, actual passwords, personal settings, footage, diagnostic captures and local backups are excluded. Publishing source does not add a new open-source license grant; third-party notices cover bundled dependencies.

For a native Apple Silicon build use Python 3.12 on macOS 15.6+, install Homebrew `libusb`, then install `requirements-macos.txt` with binary wheels and run `python -m pytest -q` and `python build_macos.py`. The workflow asserts arm64, checks all Mach-O slices/deployment targets/dependencies, verifies ad-hoc signing, runs frozen H.264/pipeline/UI/no-device smoke checks, and creates a separate prerelease pkg. No publisher signing private key is sent to CI.

Windows uses the separate `requirements.txt`, `GogglesHDMI.spec`, Inno Setup installer and local `build_release.py`. Existing Windows binaries are retained. Runtime libraries remain shared and their license notices are included in packaged builds.
