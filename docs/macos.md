# Wick on macOS (developer build)

macOS support is new and not signed by Apple yet. It's meant for trying Wick out.

## The encoders

Wick uses the same command-line encoders on every system. On a Mac they come from [Homebrew](https://brew.sh):

```bash
brew install ffmpeg jpeg-xl gifsicle
```

Wick finds them in `/opt/homebrew/bin` (Apple silicon) or `/usr/local/bin` (Intel), also when it's opened from
Finder. Settings › Encoders shows what it found.

## The app

Each build on GitHub (Actions › Test and build › the run › Artifacts) has a `Wick-macos-arm64` zip for Apple
silicon Macs. Unzip it and move Wick.app to Applications. It's signed ad hoc, not with an Apple certificate, so
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
- **Videos** are encoded on the processor (x265 and SVT-AV1). The Mac's own video encoder (VideoToolbox) isn't
  used yet.
- **Phone Backups** finds the iPhone and iPad backups Finder makes (`~/Library/Application Support/MobileSync`).
- **Windows-only tools** say "Available on Windows" instead of running: Apps, Compress Apps (NTFS compression),
  the Windows clean-up and Installer packages.
