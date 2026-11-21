# To do

Ideas deferred for later releases, roughly in order of value. Not promises.

## Safety
- **Isolate the rest of decoding.** Previews, thumbnails, Broken Files and Similar Photos already decode in a
  restricted worker (`wick/sandbox.py`). Still in Wick itself: compressing and converting (they write results
  next to the originals, which a low-integrity process can't), metadata scans, and the video preview's frames.
- **Code signing** (removes the "Windows protected your PC" warning). Keep the certificate in a GitHub
  environment that needs approval before a workflow can use it.

## Video
- GPU film-grain path (nlmeans_vulkan + NVENC + grav1synth); better denoising before encoding.
- Quality measures on the GPU; fewer probe encodes per scene; a fixed quality for grainy content.
- Close the gap between the probe encodes' quality and the final encode's.
- Benchmark the number of parts encoded side by side; a full GPU pipeline; pausing between parts.

## Distribution
- Microsoft Store (MSIX), winget.
- Submit the Claude extension to Anthropic's directory (packaging/mcp/DIRECTORY.md); let the extension find the
  Store version.

## App
- Faster start-up; a "Report a problem" button; translations.
