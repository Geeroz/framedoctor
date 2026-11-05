# FrameDoctor

Find and repair **dropped** and **repeated** frames in video clips — common in AI-generated video, screen recordings and bad transcodes — while leaving every good frame untouched.

Works on anything FFmpeg can read (.mp4, .mov, .mkv; H.264, HEVC, ProRes …).

## How it works

FrameDoctor measures optical-flow motion between every pair of consecutive frames and compares it with the normal motion around it.

| What it sees | Motion pattern | Repair |
|---|---|---|
| **Dropped frame** | one step jumps to ~2× normal (3× = two missing) | new frame(s) inserted in the gap |
| **Held repeat** — a copy replaced a real frame | ~0 motion, then a ~2× jump | the copy is rebuilt in place (length unchanged) |
| **Inserted repeat** — an extra copy was added | ~0 motion, then normal | the copy is removed |
| **Scene cut** | big change that flow can't map | skipped |

New frames are synthesized by bidirectional optical-flow warping between the two good neighbours. All other frames pass through unchanged.

## Mac app (drag and drop)

1. Download `FrameDoctor-…-macOS-arm64.zip` from Releases, unzip, move **FrameDoctor.app** to Applications.
2. The first time, macOS will block it because it isn't notarized. Either open **System Settings → Privacy & Security** and click **Open Anyway**, or run once in Terminal:
   ```bash
   xattr -dr com.apple.quarantine /Applications/FrameDoctor.app
   ```
3. Drop one or more clips on the app (or double-click it and choose files). Pick **Report only** or **Fix**, then **Normal**, **Sensitive** or **Extra**.

Results go next to each original: `clip_report.csv` and, when fixing, `clip_fixed.mov` (ProRes 422 HQ). Originals are never modified.

The app is self-contained: Python, OpenCV and FFmpeg are bundled, so nothing else needs installing. The Apple Silicon build does not run on Intel Macs; build on an Intel Mac for that.

### Building the app

On a Mac with `python3` (Xcode Command Line Tools is enough):

```bash
./mac/build_app.sh
```

This produces `dist/FrameDoctor.app` and a zip ready to share or attach to a GitHub release.

## Command line

```bash
pip install -r requirements.txt
python3 framedoctor.py clip.mp4                 # detect only -> clip_report.csv
python3 framedoctor.py clip.mp4 --fix           # repair      -> clip_fixed.mov
python3 framedoctor.py clip.mp4 --fix out.mp4   # repair as H.264 MP4
```

To run it as `framedoctor` from anywhere:

```bash
chmod +x framedoctor.py && ln -s "$PWD/framedoctor.py" ~/bin/framedoctor
# or, with the app installed (no Python needed):
ln -s /Applications/FrameDoctor.app/Contents/Resources/framedoctor/framedoctor ~/bin/framedoctor
```

### Tuning

Pick a preset with `--sensitivity`:

| Preset | `--thresh` | `--min-motion` | `--window` | `--repeat-ratio` | Use for |
|---|---|---|---|---|---|
| `normal` (default) | 1.7 | 0.4 | 12 | 0.2 | most clips, fewest false hits |
| `sensitive` | 1.5 | 0.1 | 5 | 0.2 | subtle drops, slow openings, clips that speed up |
| `extra` | 1.35 | 0.05 | 4 | 0.3 | the faintest stutters, near-static shots |

Any option you set yourself overrides the preset:

| Option | Effect |
|---|---|
| `--thresh` | motion ratio that counts as a drop; lower = more sensitive |
| `--min-motion` | px/frame below which a stretch is treated as static and skipped |
| `--window` | frames each side used as the "normal motion" baseline; smaller adapts faster to speed changes |
| `--repeat-ratio` | motion below this fraction of normal counts as a repeat; higher catches near-duplicates |
| `--no-drops` / `--no-repeats` | fix only one kind |
| `--keep-length` | rebuild inserted repeats instead of removing them, keeping the frame count |
| `--save-frames DIR` | also save every new frame as PNG, for paint-fixes or swapping in RIFE/Topaz frames |
| `--dump` | write `clip_motion.csv` with the motion ratio of every frame pair, to see why a frame was or wasn't flagged |

The sensitive and extra presets can flag jerky but legitimate motion. Check the report before trusting a fix.

### Limitations

- Audio is not carried over. A fixed clip can differ in length from the source, so re-sync audio against the original in your edit.
- Flow interpolation can smear on hands, hair and fast occlusions. Use `--save-frames` to find those frames and fix them by hand or with RIFE/Topaz.
- Truly static shots have no motion to measure. Drops there are also nearly invisible.

## Testing

```bash
python3 tests/make_test_clip.py
```

This builds a synthetic clip with known drops, held repeats and an inserted repeat, fixes it, and compares the result with the ground truth.

## License

MIT
