# Wick on macOS (developer build)

macOS support is new and not signed by Apple yet. It's meant for trying Wick out.

## The encoders

The app from GitHub has them inside (FFmpeg, libjxl and gifsicle): nothing to install. Run from its source, Wick
finds Homebrew's copies (`/opt/homebrew/bin`, `/usr/local/bin`), and if they're missing, Settings › Encoders has
**Install Encoders**, which installs them with [Homebrew](https://brew.sh). Installing Homebrew itself needs your
password in Terminal, so Wick leaves that to you and says so.

## The app

Each build on GitHub (Actions › Test and build › the run › Artifacts) has a `Wick-macos-arm64` zip for Apple
silicon Macs, encoders included. Unzip it and move Wick.app to Applications. It's signed ad hoc, not with an Apple certificate, so
the first time macOS says it can't check the developer: right-click Wick.app › Open › Open. Or, in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/Wick.app
```

## From the source

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install --require-hashes -r requirements.txt
```

```bash
.venv/bin/pip install --no-deps -e .
```

```bash
.venv/bin/python -m wick
```

## What's different on a Mac

- **Open With:** Finder lists Wick for pictures, videos, music, PDFs, Office files and folders. There's no
  right-click submenu like on Windows. With Settings › Integration › Compress right away, files opened with Wick
  are compressed without opening the window.
- **Start at sign-in** adds a LaunchAgent (`~/Library/LaunchAgents/com.lanntern.wick.plist`).
- **History, logs and settings** are in `~/Library/Application Support/Wick`.
- **Videos** are encoded on the Mac's media engine (VideoToolbox) as HEVC, many times faster than on the
  processor. Macs have no AV1 encoder, so Automatic picks HEVC there; choose Processor in the options for AV1
  (smaller, much slower). Convert's MP4 (H.264) uses the media engine too.
- **Phone Backups** finds the iPhone and iPad backups Finder makes (`~/Library/Application Support/MobileSync`).
- **Windows-only tools** say "Available on Windows" instead of running: Apps, Compress Apps (NTFS compression),
  the Windows clean-up and Installer packages.
