#!/usr/bin/env python3
"""
FrameDoctor — find and repair dropped and repeated frames, touching only the bad ones.

Detection
  Optical-flow motion is measured for every pair of consecutive frames and compared
  with the local median motion around it.
    * Dropped frame : motion jumps to ~2x normal (3x for two missing, etc.)
    * Repeated frame: motion falls to ~0 while the shot is moving
        - "held"     : the repeat replaced a real frame, so the next step jumps ~2x
        - "inserted" : an extra copy was added, the next step is normal
  Scene cuts are recognised (flow can't map one frame onto the other) and skipped.

Repair
  Every good original frame is written out untouched. Repeats are removed, and new
  frames are synthesized by bidirectional optical-flow warping only where motion was
  lost. A held repeat is replaced in place (length unchanged), a dropped frame is
  re-inserted (clip gets back the lost time), an inserted repeat is removed.

Usage
  framedoctor clip.mp4                       detect only, writes clip_report.csv
  framedoctor clip.mp4 --fix                 repair -> clip_fixed.mov (ProRes 422 HQ)
  framedoctor clip.mp4 --fix out.mp4         repair -> H.264 MP4
  framedoctor clip.mp4 --fix --sensitivity sensitive   catch subtler drops / slow openings
  framedoctor clip.mp4 --fix --sensitivity extra       catch the faintest (check the report)
"""
import argparse, csv, os, shutil, subprocess, sys

import cv2
import numpy as np

__version__ = "1.0.0"

PRESETS = {
    "normal":    dict(thresh=1.7,  min_motion=0.4,  window=12, repeat_ratio=0.2),
    "sensitive": dict(thresh=1.5,  min_motion=0.1,  window=5,  repeat_ratio=0.2),
    "extra":     dict(thresh=1.35, min_motion=0.05, window=4,  repeat_ratio=0.3),
}


# ---------------------------------------------------------------- utilities
def find_ffmpeg():
    env = os.environ.get("FRAMEDOCTOR_FFMPEG")
    if env and os.path.exists(env):
        return env
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.exists(p):
            return p
    sys.exit("FFmpeg not found. Install it (brew install ffmpeg) or pip install imageio-ffmpeg.")


class Progress:
    def __init__(self, enabled):
        self.enabled, self.last = enabled, -1

    def __call__(self, phase, cur, total):
        if not self.enabled or total <= 0:
            return
        pct = int(100 * cur / total)
        if pct != self.last:
            self.last = pct
            print(f"PROGRESS {phase} {pct}", file=sys.stderr, flush=True)


def tc(frame, fps):
    s = frame / fps
    ff = int(round((s % 1) * fps))
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d}:{ff:02d}"


def open_video(path):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"Can't open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    return cap, fps, count


def warp(img, fl, t):
    h, w = fl.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(img, gx + fl[..., 0] * t, gy + fl[..., 1] * t,
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def new_flow():
    return cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)


# ---------------------------------------------------------------- analysis
def analyse(path, scale, prog):
    """One streaming pass: motion and warp error for every consecutive pair."""
    cap, fps, count = open_video(path)
    dis = new_flow()
    mags, errs, prev, n = [], [], None, 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY)
        if prev is not None:
            fl = dis.calc(prev, g, None)
            mags.append(float(np.median(np.linalg.norm(fl, axis=2))) / scale)
            pred = warp(g, fl, 1.0)
            errs.append(float(np.mean(np.abs(pred.astype(np.float32) - prev.astype(np.float32)))))
        prev, n = g, n + 1
        prog("analysing", n, count)
    cap.release()
    return np.array(mags), np.array(errs), fps, n


def baseline(mags, i, window):
    lo, hi = max(0, i - window), min(len(mags), i + window + 1)
    neigh = np.concatenate([mags[lo:i], mags[i + 1:hi]])
    if len(neigh) < 3:
        return None
    med = np.median(neigh)
    moving = neigh[neigh > 0.3 * med]          # ignore other repeats in the window
    return float(np.median(moving)) if len(moving) >= 3 else float(med)


def detect(mags, errs, a):
    """Return events: start frame s -> dict(next=e1, repeats=k, synth=m, ...).
    Frames s+1 .. e1-1 are repeats to remove; m new frames go between s and e1."""
    n = len(mags)
    cut_level = (np.median(errs) + 1e-6) * 6 if n else 0
    events, i = [], 0
    while i < n:
        base = baseline(mags, i, a.window)
        if base is None or base < a.min_motion:
            i += 1
            continue
        if errs[i] > cut_level:
            if mags[i] / base >= a.thresh:
                events.append(dict(s=i, next=i + 1, repeats=0, synth=0, ratio=mags[i] / base,
                                   kind="scene cut (skipped)", fix=False))
            i += 1
            continue

        if a.repeats and mags[i] < a.repeat_ratio * base:
            j = i
            while j < n and mags[j] < a.repeat_ratio * base:
                j += 1
            k = j - i                                   # frames i+1 .. j are copies of i
            if j >= n or errs[j] > cut_level:          # run ends at clip end / a cut
                i = j + 1
                continue
            r = mags[j] / base
            lost = max(0, int(round(r)) - 1) if r >= 1.4 else 0
            if not a.drops:
                lost = min(lost, k)
            m = max(lost, k) if a.keep_length else lost
            if lost >= k and lost > 0:
                kind = "held repeat" if lost == k else "held repeat + drop"
            else:
                kind = "inserted repeat"
            events.append(dict(s=i, next=j + 1, repeats=k, synth=m, ratio=r, kind=kind, fix=True))
            i = j + 1
            continue

        r = mags[i] / base
        if a.drops and r >= a.thresh:
            m = max(1, int(round(r)) - 1)
            events.append(dict(s=i, next=i + 1, repeats=0, synth=m, ratio=r, kind="drop", fix=True))
        i += 1
    return events


# ---------------------------------------------------------------- repair
def synth(a, b, m, dis):
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    f_ab, f_ba = dis.calc(ga, gb, None), dis.calc(gb, ga, None)
    out = []
    for k in range(1, m + 1):
        t = k / (m + 1)
        out.append(cv2.addWeighted(warp(a, f_ba, t), 1 - t, warp(b, f_ab, 1 - t), t, 0))
    return out


def encoder_args(out_path):
    ext = os.path.splitext(out_path)[1].lower()
    if ext in (".mp4", ".m4v"):
        return ["-c:v", "libx264", "-crf", "14", "-preset", "slow", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart"]
    return ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
            "-vendor", "apl0"]


def repair(path, out_path, events, a, prog):
    ev = {e["s"]: e for e in events if e["fix"]}
    cap, fps, count = open_video(path)
    ok, first = cap.read()
    if not ok:
        sys.exit("No frames to read.")
    h, w = first.shape[:2]
    cmd = [find_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", f"{fps}", "-i", "-", *encoder_args(out_path), out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    dis = new_flow()
    if a.save_frames:
        os.makedirs(a.save_frames, exist_ok=True)

    written = 0
    def put(img, synthetic=False):
        nonlocal written
        proc.stdin.write(img.tobytes())
        if synthetic and a.save_frames:
            cv2.imwrite(os.path.join(a.save_frames, f"new_frame_{written:05d}.png"), img)
        written += 1

    pending, idx, f = None, 0, first
    try:
        while True:
            if pending is not None and idx == pending["next"]:
                for s in synth(pending["img"], f, pending["synth"], dis):
                    put(s, synthetic=True)
                pending = None
            if pending is None:                     # repeats inside a pending run are skipped
                put(f)
                if idx in ev:
                    pending = dict(ev[idx], img=f)
            prog("repairing", idx + 1, count)
            ok, f = cap.read()
            if not ok:
                break
            idx += 1
    except BrokenPipeError:
        sys.exit("FFmpeg stopped unexpectedly (check the output file name/extension).")
    cap.release()
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit("FFmpeg failed to write the output.")
    return idx + 1, written


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(prog="framedoctor",
                                 description="Find and repair dropped and repeated frames.")
    ap.add_argument("input")
    ap.add_argument("--fix", nargs="?", const="", metavar="OUT",
                    help="repair; .mov = ProRes 422 HQ, .mp4 = H.264 (default <name>_fixed.mov)")
    ap.add_argument("--sensitivity", choices=list(PRESETS), default="normal",
                    help="preset: normal (default), sensitive, extra; individual options below override it")
    ap.add_argument("--sensitive", dest="sensitivity", action="store_const", const="sensitive",
                    help="same as --sensitivity sensitive")
    ap.add_argument("--thresh", type=float, default=None,
                    help="motion ratio that counts as a drop (lower = more sensitive)")
    ap.add_argument("--repeat-ratio", type=float, default=None,
                    help="motion below this fraction of normal counts as a repeat")
    ap.add_argument("--window", type=int, default=None, help="frames each side for the baseline")
    ap.add_argument("--min-motion", type=float, default=None,
                    help="skip near-static stretches below this many px/frame")
    ap.add_argument("--no-drops", dest="drops", action="store_false", help="ignore dropped frames")
    ap.add_argument("--no-repeats", dest="repeats", action="store_false", help="ignore repeats")
    ap.add_argument("--keep-length", action="store_true",
                    help="replace inserted repeats with new frames instead of removing them")
    ap.add_argument("--report", help="CSV path (default <name>_report.csv)")
    ap.add_argument("--save-frames", metavar="DIR", help="also save each new frame as PNG")
    ap.add_argument("--dump", action="store_true",
                    help="write motion + ratio of every frame pair to <name>_motion.csv")
    ap.add_argument("--scale", type=float, default=0.5, help="analysis resolution (0.25-1)")
    ap.add_argument("--progress", action="store_true", help="machine-readable progress on stderr")
    ap.add_argument("--version", action="version", version=f"FrameDoctor {__version__}")
    a = ap.parse_args()
    for k, v in PRESETS[a.sensitivity].items():
        if getattr(a, k) is None:
            setattr(a, k, v)

    stem = os.path.splitext(a.input)[0]
    prog = Progress(a.progress)
    mags, errs, fps, n = analyse(a.input, a.scale, prog)
    print(f"{n} frames @ {fps:.3f} fps")
    events = detect(mags, errs, a)

    report = a.report or stem + "_report.csv"
    with open(report, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "timecode", "type", "repeats_removed", "frames_added", "motion_ratio"])
        for e in events:
            w.writerow([e["s"], tc(e["s"], fps), e["kind"], e["repeats"], e["synth"],
                        f"{e['ratio']:.2f}"])
            print(f"  frame {e['s']:>6}  {tc(e['s'], fps)}  {e['kind']:<20} "
                  f"-{e['repeats']} +{e['synth']}  ratio {e['ratio']:.2f}")
    fixes = [e for e in events if e["fix"]]
    print(f"Found {sum(e['kind'] == 'drop' for e in fixes)} drop(s), "
          f"{sum('repeat' in e['kind'] for e in fixes)} repeat(s). Report: {report}")

    if a.dump:
        dump = stem + "_motion.csv"
        with open(dump, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", "timecode", "motion_px", "local_median", "ratio"])
            for i in range(len(mags)):
                b = baseline(mags, i, a.window) or 0.0
                w.writerow([i, tc(i, fps), f"{mags[i]:.3f}", f"{b:.3f}",
                            f"{mags[i] / (b + 1e-6):.2f}"])
        print(f"Motion dump: {dump}")

    if a.fix is None:
        return
    out = a.fix or stem + "_fixed.mov"
    if os.path.abspath(out) == os.path.abspath(a.input):
        sys.exit("Output would overwrite the input; choose another name.")
    n_in, n_out = repair(a.input, out, events, a, prog)
    print(f"Fixed: {out}  ({n_in} -> {n_out} frames; "
          f"-{sum(e['repeats'] for e in fixes)} repeats, +{sum(e['synth'] for e in fixes)} new; "
          f"all other frames untouched)")
    print("Note: audio is not carried over.")


if __name__ == "__main__":
    main()
