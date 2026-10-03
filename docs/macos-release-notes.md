Apple Silicon macOS preview, version 1.2.0.

One native arm64 package targets compatible Apple Silicon Macs (M1, M2, M3, M4 and later M-series) running macOS 15.6 or newer. No model-specific CPU tuning. Intel Macs, Windows ARM and Linux are outside this release.

This is a **hardware-unverified candidate**. Native CI checks the dependency bundle, H.264 decoding, image pipeline, Qt UI launch, no-device behavior and ad-hoc signature integrity. It cannot verify Goggles 3 USB claims, live video or external HDMI on your Mac. The direct USB backend initializes userspace RNDIS, ARP and UDP; it does not detach/reset USB drivers or configure system networking.

Download the `Goggles-HDMI-macOS-arm64-1.2.0-preview.pkg` installer. It installs Goggles HDMI in Applications. The app is ad-hoc signed; the package is unsigned and neither is Apple notarized. Gatekeeper may block installation or launch. Use Apple's per-app Open Anyway action only if offered for this downloaded package/app; no security policy changes are required by the application. A Developer ID/notarized production release requires publisher credentials that are not available for this candidate.

Connect Goggles 3 directly using a USB data cable. Enable OTG wired computer connection and live-view sharing in the goggles, and close DJI Assistant or other device clients. In Goggles HDMI, use USB setup for instructions and then connect. Progress distinguishes USB device, interfaces, RNDIS, ARP, video packets and decoding. USB diagnostics omit device serials. A BUSY/ACCESS error is reported rather than forcibly capturing the composite device.

Use an extended external display and select it for output. Cmd+D / Ctrl+D and Escape release output while this app is active; the control window also has an output-stop button. These are app shortcuts, not macOS global hotkeys. Display sleep inhibition lasts only while output is active. Saved watermark authentication uses macOS Keychain; denial allows session-only authentication.

Windows 1.1.0 remains the repository's stable/latest release with its original download and update manifest. This Mac preview uses a separate signed arm64 update channel. Apple's Installer requires OS confirmation after a verified update download; it does not run silently.

Hardware acceptance still needed: USB enumeration → safe interface claim → RNDIS → ARP → DJI packets → decoded dimensions → external output; repeated connect/disconnect and quit/reopen; Keychain authentication; grading/stabilization; physical display modes; release action and a ten-minute live run. Measure input frame rate separately from output refresh.
