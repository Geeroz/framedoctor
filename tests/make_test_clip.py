#!/usr/bin/env python3
"""Build a synthetic test clip with dropped and repeated frames at known positions,
run FrameDoctor on it, and check detection plus repair quality against ground truth.

    python3 tests/make_test_clip.py [workdir]
"""
import os, subprocess, sys
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "framedoctor.py")
work = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_out")
os.makedirs(work, exist_ok=True)

H, W, N, FPS = 540, 960, 240, 24
rng = np.random.default_rng(1)
tex = cv2.GaussianBlur((rng.random((1400, 2800, 3)) * 255).astype(np.uint8), (0, 0), 3)
xs = np.cumsum([4 + 2 * np.sin(k / 25) for k in range(N)])


def render(i):
    x, y = int(200 + xs[i]), int(300 + 60 * np.sin(i / 40))
    f = tex[y:y + H, x:x + W].copy()
    cv2.circle(f, (int(480 + 300 * np.sin(i / 30)), 270), 60, (0, 200, 255), -1)
    return f


truth = [render(i) for i in range(N)]

# Build the damaged sequence as a list of source indices.
drops = {30, 118, 160, 161}       # removed
held = {71, 205}                  # replaced by a copy of the previous frame
inserted = {140}                  # an extra copy added after this frame
seq = []
for i in range(N):
    if i in drops:
        continue
    seq.append(i - 1 if i in held else i)
    if i in inserted:
        seq.append(i)

src = os.path.join(work, "damaged.mp4")
p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
                      "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "16",
                      "-pix_fmt", "yuv420p", src], stdin=subprocess.PIPE)
for i in seq:
    p.stdin.write(truth[i].tobytes())
p.stdin.close(); p.wait()
print(f"damaged clip: {len(seq)} frames (truth {N}); expect 3 drop events "
      f"(one double), 2 held repeats, 1 inserted repeat")

out = os.path.join(work, "fixed.mov")
r = subprocess.run([sys.executable, TOOL, src, "--fix", out], capture_output=True, text=True)
print(r.stdout, r.stderr)

cap = cv2.VideoCapture(out)
frames = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    frames.append(f)
print(f"fixed clip: {len(frames)} frames (should equal ground truth: {N})")
if len(frames) == N:
    ps = [cv2.PSNR(a, b) for a, b in zip(frames, truth)]
    worst = np.argsort(ps)[:6]
    print("PSNR vs ground truth: median %.1f dB, worst frames %s" %
          (np.median(ps), ", ".join(f"{i}:{ps[i]:.1f}" for i in sorted(worst))))
