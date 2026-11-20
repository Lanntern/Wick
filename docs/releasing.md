# Releasing

1. Bump `__version__` in `wick/__init__.py`, merge, and push a tag: `git tag v0.1.1 && git push origin v0.1.1`.
2. The build attaches `Wick-setup.exe`, `Wick-windows.zip`, `Wick.mcpb` and `SHA256SUMS.txt` to a **draft**
   release. Check it, write the notes and publish it.
3. Publishing runs `.github/workflows/package-managers.yml`:
   - **Scoop:** `bucket/wick.json` is pointed at the new zip (checksum from `SHA256SUMS.txt`) and committed to
     `main`. People who added the bucket get it with `scoop update wick`.
   - **winget:** a pull request with the new version is opened on
     [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs); Microsoft's checks and a moderator merge
     it, usually within a day or two.

   If either fails, fix the cause and run the workflow by hand (Actions › package managers › Run workflow, with
   the tag).

## Scoop

The bucket is this repository (Scoop reads manifests from `bucket/`):

```
scoop bucket add wick https://github.com/Lanntern/Wick
scoop install wick
```

`scoop install wick` puts `wick` (the command line) on the PATH and a Start menu shortcut in "Scoop Apps".
Settings and History live in the user profile, not the app folder, so updates keep them.

If `main` requires pull requests for every change, allow `github-actions[bot]` to push, or the Scoop step fails
(the manifest can then be updated by hand: `python scripts/update_scoop.py 0.1.1 SHA256SUMS.txt`).

## winget: one-time setup

winget only updates packages it already has, so the first version is submitted by hand:

1. Fork [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs) to the Lanntern account.
2. Submit the manifests in `packaging/winget/` (v0.1.0, with the release's real checksums). From Windows:
   ```
   winget validate packaging\winget
   winget install --manifest packaging\winget      # try it (needs "winget settings --enable LocalManifestFiles")
   wingetcreate submit packaging\winget
   ```
   (`wingetcreate` is Microsoft's tool: `winget install Microsoft.WingetCreate`. Its `submit` asks for a GitHub
   sign-in and opens the pull request.)
3. Once that pull request is merged, create a classic personal access token with the `public_repo` scope and add
   it to this repository as the Actions secret `WINGET_TOKEN` (Settings › Secrets and variables › Actions).
   Every later release then goes to winget automatically. Without the secret, the workflow skips winget and says
   so.

Unsigned installers are accepted, but Microsoft's automated checks run SmartScreen and Defender on them; a new,
unsigned app can be held for manual review.
