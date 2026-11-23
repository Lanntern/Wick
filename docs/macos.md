# Wick on macOS

macOS support is new, for Apple silicon Macs, and not signed by Apple yet.

## Installing

- **Disk image:** `Wick-macos-arm64.dmg` from the [Releases page](https://github.com/Lanntern/Wick/releases/latest)
  (or, between releases, from a build's Artifacts on GitHub Actions). Open it and drag Wick to Applications.
- **Homebrew:**

  ```bash
  brew tap lanntern/wick https://github.com/Lanntern/Wick
  ```

  ```bash
  brew install --cask wick
  ```

  Update with `brew upgrade --cask wick`; Wick's update notice says so when it was installed this way.

Because it isn't signed by Apple, the first time macOS says it can't check the developer: right-click Wick ›
Open › Open. Or, in Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/Wick.app
```

The encoders (FFmpeg, libjxl, gifsicle) are inside the app: nothing else to install.

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

Run this way, Wick uses Homebrew's encoders; if they're missing, Settings › Encoders › **Install Encoders** installs
them with [Homebrew](https://brew.sh).

## What's different on a Mac

- **App libraries are left alone.** Photos, Music, iMovie, Final Cut, Logic, GarageBand, Lightroom and Capture One
  libraries, and app bundles, are never looked inside: their apps keep a database of what's in them, and changing
  a file behind their back can break the library. Export from the app to shrink what's in one. Wick says when it
  left one alone.
- **iCloud Drive:** files kept only in iCloud ("Optimize Mac Storage") are skipped, never downloaded.
- **Videos** are encoded on the Mac's media engine (VideoToolbox) as HEVC. Macs have no AV1 encoder, so Automatic
  picks HEVC; choose Processor in the options for AV1 (smaller, much slower).
- **Compress Apps** uses APFS's own compression, as macOS does for its system files: apps keep working, and undo
  decompresses them. Apps installed for every user (owned by the system) need an administrator and are left as they
  are.
- **Duplicates** on APFS become clones: stored once, still separate files.
- **Big scans** (your home folder, a whole drive) ask Spotlight for the files, much faster than reading every
  folder. Thumbnails of HEIC and RAW photos come from Quick Look.
- **Clean Up › Mac:** Xcode's build data and old device support, caches, Mail's copies of opened attachments,
  iPhone update files, old installers, and data left by removed apps.
- **Finder:** Settings › Integration › Context menu adds Compress with Wick and Convert with Wick to the right-click
  menu (Quick Actions). Open With works for pictures, videos, music, documents and folders.
- **The Dock** shows a job's progress (‖ paused, ! failed); its menu has Pause and Stop. Notifications go to
  Notification Center; clicking one opens the result, with Undo there.
- **Menus and shortcuts:** the usual menu bar, and ⌘ where Windows uses Ctrl.
- **Start at sign-in** adds a LaunchAgent (`~/Library/LaunchAgents/com.lanntern.wick.plist`). History, logs and
  settings are in `~/Library/Application Support/Wick`.
- **Windows-only tools** say "Available on Windows": Apps, App Leftovers, the Windows clean-up and Installer packages.
