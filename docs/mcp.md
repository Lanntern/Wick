# Wick for AI assistants (MCP)

Wick can be used by AI assistants such as Claude through the
[Model Context Protocol](https://modelcontextprotocol.io). The assistant can look for photos, videos, music and
documents that could be stored smaller, tell you what it found, and, when you ask and approve it, compress them or undo a
past run. It uses the same encoders and safety checks as the app.

It needs the Wick app for Windows (installed, or the portable zip). Everything happens on your computer.

## Set it up

**Claude Desktop:** install `Wick.mcpb` (from the
[latest release](https://github.com/Lanntern/Wick/releases/latest)) by opening it, or from Claude's
extension directory once it's listed there. Choose the folders Wick may use. That's all.

**Claude Code, or any other MCP client:** run the installed app with `mcp` and the folders it may use:

```bash
claude mcp add wick -- "C:\Program Files\Wick\Wick.exe" mcp "D:\Photos" "D:\Videos"
```

Options (before the folders): `--keep-originals` (save compressed copies next to the originals instead of moving
the originals to the Recycle Bin) and `--max-files=N` (the largest run allowed; 1000 by default).

## What the assistant can do

| Tool | What it does | Changes files? |
|---|---|---|
| `list_allowed_folders` | The folders it may use, what happens to originals, the presets | No |
| `plan_compression` | Finds what can be compressed in a folder and estimates the savings | No |
| `compress_planned_files` | Compresses exactly the files in a plan, **after you approve it** | Yes |
| `list_history` | Past runs in the allowed folders | No |
| `find_cleanup` | Lists what Clean Up would: duplicates, similar photos, large, broken or empty files, old installers and camera proxy files in the allowed folders, or app caches, installed apps and app leftovers on the whole computer | No |
| `undo_run` | Puts a run's originals back, **after you approve it** | Yes |

## Why it's safe

The worst case for a tool like this is an assistant that misunderstands, or is misled by something it read,
and loses your files. Wick is built to prevent that.

**Folders.** The assistant can only work in the folders you chose. Every path is fully resolved, following
symbolic links and junctions, and must be inside one of them. Windows, Program Files, ProgramData, AppData and
Wick's own History are always refused, even inside a chosen folder.

**Approval.** Planning doesn't change anything. Before files are compressed or a run is undone, Wick shows you
what will happen in its own window, which the assistant can't see or answer. If you cancel, close it or don't
answer within 10 minutes, nothing changes. Outside Windows, the assistant's app asks you instead, and if it
can't, nothing is changed.

**Plans.** Compressing takes a plan ID from a plan you could see, never a list of files. Plans expire after 30
minutes and can only be used once, and files that changed since the plan was made are left alone.

**Undo.** Originals go to the Recycle Bin. If a drive has no Recycle Bin, or a file is too big for it, that file
isn't changed. Every run is recorded in History and can be undone from the app or by asking the assistant. If
you've turned History off in Wick, originals are always kept. Undo never removes a compressed file you've
edited since.

**Limits.** Runs are capped at 1000 files by default, calls are rate-limited, and only one run happens at a
time. File names are passed on without hidden or control characters.

**Looking, not removing.** `find_cleanup` only lists things, with sizes and reasons. Nothing here removes them:
you do that yourself on Wick's Clean Up page. Its overviews of app caches, installed apps and app leftovers
cover the whole computer, so the assistant sees their names and sizes.

**Privacy.** Wick doesn't keep anything from the conversation. Its log notes which tool ran, not what was
asked. See [Privacy](#privacy).

## Examples

1. *"How much space could I save in my Pictures folder?"*: the assistant plans `Pictures` and reports the
   estimated savings, by kind of file, without changing anything.
2. *"Compress the screenshots in D:\Photos\Screenshots, losslessly."*: it plans the folder with the
   Screenshots preset, then asks to compress; you approve in Wick's dialog; it reports what was saved and
   the run ID.
3. *"Undo what you did to my screenshots yesterday."*: it finds the run with `list_history`, asks to undo it,
   and you approve; the originals come back from the Recycle Bin.

To try it without your own files: make a folder, put a few large PNG screenshots in it (or run
`python packaging/mcp/check_extension.py <folder> -- <command>`, which creates test pictures), and allow only
that folder.

## Troubleshooting

- **"Wick isn't installed"**: install the app, or, for the portable zip, set *Wick.exe location* in
  the extension's settings.
- **"…is outside the folders Wick may use"**: add the folder in the extension's settings (Claude
  Desktop: Settings → Extensions → Wick).
- **"No folders are allowed yet" / refused folders**: the chosen folder doesn't exist, or is a system, program
  or app-data location.
- **Nothing happens when compressing**: look for Wick's dialog (it may be behind other windows); it
  closes itself after 10 minutes.
- **"Too many … calls"**: wait the time it mentions.
- **Anything else**: Wick's log (Settings → Diagnostics → Open Folder) records each tool call and any
  error. Report problems at <https://github.com/Lanntern/Wick/issues>; security issues as in
  [SECURITY.md](../SECURITY.md).

## Privacy

The MCP server runs on your computer and sends nothing anywhere itself. What it returns to the assistant (the
paths, names and sizes of files it planned or compressed, the allowed folders, past runs) becomes part of your
conversation, so it goes wherever your AI app sends conversations (for Claude, to Anthropic, under its privacy
policy). Wick keeps nothing from the conversation. The full policy: [PRIVACY.md](../PRIVACY.md).
