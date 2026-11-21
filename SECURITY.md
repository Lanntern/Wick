# Security policy

Wick opens files from anywhere (photos, videos, PDFs, archives, documents) with bundled decoders, so
security problems matter here.

## Reporting a vulnerability

Please **don't open a public issue.** Email **lanntern@proton.me** with:

- what the problem is and what an attacker could do with it,
- how to reproduce it (a sample file helps; mention it before sending anything large),
- the Wick version (Settings → About → About Wick → **Copy Versions**).

I aim to reply within a week. Please give a reasonable time to fix it (normally up to 90 days) before
disclosing it publicly; you'll be credited in the release notes unless you'd rather not be.

## Supported versions

Only the newest release gets security fixes. Installed copies check for a newer version (weekly by default; the
interval can be changed in Settings), and a release that fixes a security problem says so.

## Isolated decoding

Previews, thumbnails, Broken Files and Similar Photos open whatever is on the disk, so they decode in a separate
worker process that gives up rights first (`wick/sandbox.py`, `wick/_decoders.py`). On Windows it runs at low
integrity, so it can read files but not change them, your settings or other programs, and it's in a job object
(no clipboard, few processes, a memory limit). FFmpeg and djxl run inside it with the same limits. It replies
with plain data, never pickled objects, and a worker that crashes or hangs is replaced, so it can't take Wick
with it. Compressing and converting still decode in Wick itself: you chose those files, and the results are
written next to them.

## Bundled components

Many problems turn out to be in a bundled component (FFmpeg, libjxl, Pillow, pikepdf, Qt, …). Report those
here too; if a fix is needed upstream, it will be reported there and Wick updated once it's released.
Components are pinned and checked weekly for known vulnerabilities (see "How bundled components are kept up to
date" in [CONTRIBUTING.md](CONTRIBUTING.md)).
