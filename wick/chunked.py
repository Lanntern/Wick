"""Scene-by-scene video encoding: the video is split at scene changes into parts that are encoded side by side
(faster on many-core processors), each part at the quality its own scenes need when Smart quality is on (quiet
scenes stop wasting space, busy ones get enough), and the parts are joined and the soundtrack, subtitles and
chapters taken from the original.

Finished parts are kept (in a hidden folder next to the video) until the whole video is done, so a video that's
stopped, or interrupted by a crash or a restart, carries on where it was the next time it's compressed with the
same settings. Parts left behind are removed after PARTS_KEEP_DAYS.

Processor encoders only (a graphics card has one encoder: nothing to run side by side). A part is exactly the
frames from one cut to the next: each starts and ends halfway between two frames, and ffmpeg's accurate seeking
keeps precisely the frames inside.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path

from . import steps, tools
from .fileops import remove_quietly
from .formats import plural
from .probe import VideoInfo, probe_video
from .scanner import PARTS_PREFIX
from .settings import Settings
from .video import drops_repeats, has_filter, is_gpu

PARTS_VERSION = 2
PARTS_KEEP_DAYS = 14
MIN_VIDEO_SECONDS = 90        # shorter videos are encoded as a whole
MIN_PART_SECONDS = 10.0       # parts at least this long (more parts cost more start-ups and seeks)...
MAX_PART_SECONDS = 45.0       # ...and at most this long (long scenes are split evenly)
MAX_PARTS = 600
SCENE_THRESHOLD = 12          # ffmpeg scdet: how different two frames must be to count as a cut (0-100)


def applies(info: VideoInfo, s: Settings, encoder: str) -> bool:

    return (s.video_chunked and not is_gpu(encoder) and info.duration >= MIN_VIDEO_SECONDS
            and info.fps > 0 and not drops_repeats(info, s))


# ---------------------------------------------------------------- where the cuts go
def scene_cuts(src: Path, info: VideoInfo, cancel: threading.Event | None = None) -> list[float]:
    """Times (seconds) of the first frame of each new scene, found on a small copy of the picture."""

    if not has_filter("scdet"):
        return []
    ffmpeg = tools.require("ffmpeg")
    args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-i", str(src), "-map", f"0:{info.main_video_index}",
            "-an", "-sn", "-dn", "-vf", f"scale=-2:180,scdet=threshold={SCENE_THRESHOLD}", "-f", "null", "-"]
    if cancel is not None and cancel.is_set():
        raise tools.Cancelled()
    proc = tools.run(args, timeout=max(600.0, info.duration * 3), check=False)
    return sorted({float(t) for t in re.findall(r"lavfi\.scd\.time:\s*([0-9.]+)", proc.stderr)})


def plan_parts(duration: float, fps: float, cuts: list[float]) -> list[tuple[float, float]]:
    """(start, end) of each part, in seconds; boundaries halfway between frames, the last part open-ended
    (end 0: to the end of the video)."""
    half = 0.5 / fps
    shortest = max(MIN_PART_SECONDS, duration / MAX_PARTS)
    edges = [0.0]
    for cut in cuts:
        at = cut - half
        if at - edges[-1] >= shortest and duration - at >= shortest:
            edges.append(at)
    edges.append(duration)
    starts: list[float] = []
    for a, b in zip(edges, edges[1:], strict=False):
        pieces = max(1, math.ceil((b - a) / MAX_PART_SECONDS - 1e-6))
        step = (b - a) / pieces
        for k in range(pieces):
            # (split points inside a scene fall halfway between frames too)
            starts.append(a if k == 0 else (round((a + k * step) * fps) - 0.5) / fps)
    return [(start, starts[i + 1] if i + 1 < len(starts) else 0.0) for i, start in enumerate(starts)]


def first_frames(src: Path, info: VideoInfo, parts: list[tuple[float, float]]) -> list[float]:
    """The time of each part's first frame in the original: parts are joined at exactly these moments (so
    nothing drifts against the soundtrack, whatever the frame rate, steady or variable)."""
    ffprobe = tools.require("ffprobe")
    out = [0.0]
    for start, _end in parts[1:]:
        proc = tools.run([ffprobe, "-v", "error", "-select_streams", f"{_video_stream_number(info)}",
                          "-read_intervals", f"{max(0.0, start - 2):.3f}%+4", "-show_entries", "frame=pts_time",
                          "-of", "csv=p=0", str(src)], timeout=300, check=False)
        times = sorted(float(t) for t in re.findall(r"^\s*([0-9.]+)", proc.stdout, re.M))
        later = [t for t in times if t >= start]
        out.append(later[0] if later else start + 0.5 / info.fps)
    return out


def _video_stream_number(info: VideoInfo) -> str:
    """ffprobe's selector for the main video stream (v:N among video streams)."""
    videos = [st.index for st in info.streams if st.kind == "video"]
    return f"v:{videos.index(info.main_video_index)}" if info.main_video_index in videos else "v:0"


def part_workers(info: VideoInfo, s: Settings) -> tuple[int, int]:
    """(parts encoded at once, encoder threads each): about 4-8 threads a part, fewer parts when memory is short
    (an SVT-AV1 instance needs roughly 1.5 GB at 1080p, 5 GB at 4K) or when several videos run at once."""
    from .video import parallel_videos

    cores = os.cpu_count() or 4
    pixels = max(1, info.width * info.height)
    per_part_gb = 0.5 + 1.0 * pixels / (1920 * 1080)
    by_memory = max(1, int(tools.total_memory_gb() * 0.6 / per_part_gb))
    workers = max(1, min(cores // 4, 6, by_memory) // max(1, parallel_videos(s)))
    return workers, max(2, cores // workers)


# ---------------------------------------------------------------- the parts folder
def _registry() -> Path:
    from .journal import data_dir

    return data_dir() / "video-parts.json"


def _note_folder(folder: Path) -> None:
    try:
        reg = json.loads(_registry().read_text("utf-8"))
    except (OSError, ValueError):
        reg = {}
    reg[str(folder)] = time.time()
    try:
        _registry().write_text(json.dumps(reg), "utf-8")
    except OSError:
        pass


def _forget_folder(folder: Path) -> None:
    try:
        reg = json.loads(_registry().read_text("utf-8"))
        reg.pop(str(folder), None)
        _registry().write_text(json.dumps(reg), "utf-8")
    except (OSError, ValueError):
        pass


def prune(max_age_days: float = PARTS_KEEP_DAYS) -> int:
    """Remove parts of videos that weren't carried on within ``max_age_days``. Returns how many folders."""
    try:
        reg = json.loads(_registry().read_text("utf-8"))
    except (OSError, ValueError):
        return 0
    now, removed = time.time(), 0
    for folder, used in list(reg.items()):
        if now - used > max_age_days * 86400:
            path = Path(folder)
            if path.name.startswith(PARTS_PREFIX):          # (only ever our own folders)
                shutil.rmtree(path, ignore_errors=True)
            reg.pop(folder)
            removed += 1
    try:
        _registry().write_text(json.dumps(reg), "utf-8")
    except OSError:
        pass
    return removed


def parts_folder(src: Path, s: Settings, encoder: str, grain: int) -> Path:
    """Next to the video (same drive as the result, so space is checked where it's needed), named after
    everything that shapes the parts, so changed settings or a changed file start afresh."""
    st = src.stat()
    shaping = {k: v for k, v in s.to_dict().items() if k.startswith(("video_", "smart_")) and k != "video_jobs"}
    shaping.update(require_savings=s.require_savings, min_savings_pct=s.min_savings_pct)
    key = json.dumps([PARTS_VERSION, str(src.resolve()), st.st_size, st.st_mtime_ns, encoder, grain, shaping],
                     sort_keys=True, default=str)
    return src.parent / (PARTS_PREFIX + hashlib.sha256(key.encode()).hexdigest()[:16])


def _hide(folder: Path) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(folder), 0x2)  # type: ignore[attr-defined]  # HIDDEN


# ---------------------------------------------------------------- encoding
def encode(src: Path, out: Path, info: VideoInfo, s: Settings, encoder: str, grain: int,
           on_progress: Callable[[float], None] | None = None,
           cancel: threading.Event | None = None, fonts=None) -> tuple[Path, list[str]]:
    """Encode ``src`` scene by scene into ``out`` (with all the other tracks; ``fonts``: a subfonts.FontPlan).
    Returns (out, notes)."""
    job = _SceneEncode(src, info, s, encoder, grain, on_progress, cancel or threading.Event())
    job.encode_parts()
    job.join(out, fonts)
    return out, job.notes()


class _SceneEncode:
    """A video encoded in parts cut at scene changes, several at a time, each at the quality its scene needs.
    The parts are kept in a folder (parts_folder) until the whole video is done, so a stopped job carries on."""

    def __init__(self, src: Path, info: VideoInfo, s: Settings, encoder: str, grain: int,
                 on_progress: Callable[[float], None] | None, cancel: threading.Event):
        from . import quality
        from .video import frame_size, measures_quality

        self.src, self.info, self.s, self.encoder, self.grain = src, info, s, encoder, grain
        self.on_progress, self.cancel = on_progress, cancel
        self.stop = threading.Event()          # set by the user's cancel, or when a part fails: the others stop too
        self.lock = threading.Lock()
        prune()
        self.folder = parts_folder(src, s, encoder, grain)
        self.folder.mkdir(exist_ok=True)
        _hide(self.folder)
        _note_folder(self.folder)
        self.state_path = self.folder / "parts.json"
        try:
            self.state = json.loads(self.state_path.read_text("utf-8"))
        except (OSError, ValueError):
            self.state = {}
        if not self.state.get("parts"):
            steps.report("Finding the scene changes")
            planned = plan_parts(info.duration, info.fps, scene_cuts(src, info, cancel))
            self.state = {"parts": planned, "starts": first_frames(src, info, planned), "q": {}, "done": []}
            self.save()
        self.parts: list[tuple[float, float]] = [tuple(p) for p in self.state["parts"]]  # type: ignore[misc]
        self.done: set[int] = {i for i in self.state.get("done", []) if _part_ok(self.folder, i)}
        self.resumed = len(self.done)
        self.workers, self.threads = part_workers(info, s)
        self.smart = measures_quality(s)
        self.metric = quality.video_metric(s.video_metric)
        self.size = frame_size(info, s)
        self.lengths = [(b or info.duration) - a for a, b in self.parts]
        self.progress = {i: 1.0 for i in self.done}

    def save(self) -> None:
        with self.lock:
            self.state_path.write_text(json.dumps(self.state), "utf-8")

    def report(self) -> None:
        if self.on_progress:
            with self.lock:
                got = sum(self.lengths[i] * f for i, f in self.progress.items())
            self.on_progress(min(1.0, got / max(0.001, self.info.duration)))

    def _segment(self, i: int) -> tuple[float, float]:
        start, end = self.parts[i]
        return start, end - start if end else 0

    def choose(self, i: int) -> tuple[int, int]:
        """(quality setting, grain level) for part ``i``. A part whose target can't be reached with grain
        re-created (some textures don't survive the denoising) is encoded with its grain kept instead."""
        from .video import SMART_STEPS, encoder_quality

        if not self.smart:
            return encoder_quality(self.s, self.encoder), self.grain
        if str(i) in self.state["q"]:
            return int(self.state["q"][str(i)]), int(self.state.get("grain", {}).get(str(i), self.grain))
        level = self.grain
        chosen = self._search(i, level)
        if chosen is None and level:
            level = 0
            chosen = self._search(i, 0)
        if chosen is None:
            from .images import NotWorthIt
            from .video import source_rate_limit

            if source_rate_limit(self.info, self.s, self.encoder):
                raise NotWorthIt("Requested video quality cannot be reached within the source bitrate budget; original kept")
        q = chosen[0] if chosen else SMART_STEPS[self.encoder][0]
        with self.lock:
            self.state["q"][str(i)] = q
            self.state.setdefault("grain", {})[str(i)] = level
        self.save()
        return q, level

    def _search(self, i: int, level: int):
        """The quality setting that meets the target for part ``i`` (encoding short probes of it)."""
        from . import quality
        from .video import SMART_STEPS, build_args, crop_filter

        s, start = self.s, self.parts[i][0]
        probe = self.folder / f"probe-{i:04d}.mkv"
        target = "identical" if s.video_quality == "identical" else s.smart_target

        def attempt(q: int):
            if self.stop.is_set():
                raise tools.Cancelled()
            try:
                tools.run(build_args(self.src, probe, self.info, s, self.encoder, q, segment=self._segment(i),
                                     progress=False, grain=level, threads=self.threads, probe=True), timeout=3600)
                score = quality.score_video_segment(self.src, start, self.lengths[i], probe, self.size,
                                                    crop_filter(self.info), self.metric, level, subsample=4)
            finally:
                remove_quietly(probe)
            return score.meets(target), score

        return quality.search(SMART_STEPS[self.encoder], attempt)

    def one(self, i: int) -> None:
        """Encode part ``i`` (checked for its length) and mark it done."""
        from .video import build_args

        if self.stop.is_set():
            raise tools.Cancelled()
        q, level = self.choose(i)
        target = self.folder / f"part-{i:04d}.mkv"
        tmp = self.folder / f"part-{i:04d}.tmp.mkv"

        def moved(f: float) -> None:
            with self.lock:
                self.progress[i] = f
            self.report()

        tools.run_ffmpeg_progress(build_args(self.src, tmp, self.info, self.s, self.encoder, q,
                                             segment=self._segment(i), grain=level, threads=self.threads),
                                  self.lengths[i], moved, self.stop)
        got = probe_video(tmp)
        if got is None or abs(got.duration - self.lengths[i]) > 1.5 / self.info.fps:
            remove_quietly(tmp)
            raise tools.ToolError(["part"], 1, f"Part {i + 1} came out {got.duration if got else 0:.2f}s long, "
                                               f"expected {self.lengths[i]:.2f}s")
        os.replace(tmp, target)
        (self.folder / f"part-{i:04d}.ok").write_text(str(target.stat().st_size), "utf-8")
        with self.lock:
            self.progress[i] = 1.0
            self.state["done"] = sorted(set(self.state.get("done", [])) | {i})
        self.save()
        self.report()

    def encode_parts(self) -> None:
        """The parts not done yet, several at a time; the first failure (or Stop) stops the others."""
        todo = [i for i in range(len(self.parts)) if i not in self.done]
        steps.report(f"Encoding {len(self.parts)} parts, {self.workers} at a time"
                     + (f" ({self.resumed} already done earlier)" if self.resumed else "")
                     + (": choosing the quality scene by scene" if self.smart else ""))
        self.report()
        if not todo:
            return

        def watch() -> None:                 # the user's Stop reaches every part
            while not self.stop.wait(0.2):
                if self.cancel.is_set():
                    self.stop.set()

        threading.Thread(target=watch, daemon=True).start()
        try:
            with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="part") as pool:
                futures = [pool.submit(self.one, i) for i in todo]
                wait(futures, return_when=FIRST_EXCEPTION)
                if any(f.done() and not f.cancelled() and f.exception() is not None for f in futures):
                    self.stop.set()                       # one part failed (or Stop): the rest stop too
                    for f in futures:
                        f.cancel()
                    wait(futures)
                    errors = [f.exception() for f in futures if f.done() and not f.cancelled()
                              and f.exception() is not None]
                    real = [e for e in errors if not isinstance(e, tools.Cancelled)]
                    raise real[0] if real and not self.cancel.is_set() else tools.Cancelled()  # type: ignore[misc]
        finally:
            self.stop.set()

    def join(self, out: Path, fonts) -> None:
        """The parts joined into one video, then the soundtrack, subtitles and chapters added from the source."""
        from .video import build_args

        steps.report("Joining the parts")
        listing = self.folder / "parts.txt"
        starts = self.state["starts"]
        n = len(self.parts)
        listing.write_text("ffconcat version 1.0\n" + "".join(
            f"file '{(self.folder / f'part-{i:04d}.mkv').as_posix()}'\n"
            + (f"duration {starts[i + 1] - starts[i]:.6f}\n" if i + 1 < n else "")
            for i in range(n)), "utf-8")
        joined = self.folder / "joined.mkv"
        ffmpeg = tools.require("ffmpeg")
        tools.run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i",
                   str(listing), "-c", "copy", str(joined)], timeout=3600)
        steps.report("Adding the soundtrack, subtitles and chapters")
        tools.run_ffmpeg_progress(build_args(self.src, out, self.info, self.s, self.encoder, video_from=joined,
                                             grain=self.grain, fonts=fonts), self.info.duration, None, self.cancel)

    def notes(self) -> list[str]:
        notes = [f"encoded scene by scene in {len(self.parts)} parts"
                 + (f", {self.resumed} carried over" if self.resumed else "")]
        kept = sum(1 for v in self.state.get("grain", {}).values() if self.grain and not int(v))
        if kept:
            notes.append(f"grain kept in {plural(kept, 'part')} (needed for the quality target)")
        if self.smart and self.state["q"]:
            from .video import quality_label

            qs = [int(v) for v in self.state["q"].values()]
            notes.append(f"{quality_label(self.encoder)} {min(qs)}–{max(qs)} by scene" if min(qs) != max(qs)
                         else f"{quality_label(self.encoder)} {qs[0]}")
        return notes


def _part_ok(folder: Path, i: int) -> bool:
    part, mark = folder / f"part-{i:04d}.mkv", folder / f"part-{i:04d}.ok"
    try:
        return part.stat().st_size == int(mark.read_text("utf-8"))
    except (OSError, ValueError):
        return False


def finished(src: Path, s: Settings, encoder: str, grain: int) -> None:
    """The video is done (or was encoded another way): its parts aren't needed any more."""
    folder = parts_folder(src, s, encoder, grain)
    shutil.rmtree(folder, ignore_errors=True)
    _forget_folder(folder)


__all__ = ["applies", "encode", "finished", "prune", "plan_parts", "scene_cuts", "PARTS_PREFIX"]
