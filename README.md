# Clock-face gaze calibration for MR-based eye tracking

A PsychoPy calibration run that gives [DeepMReye](https://github.com/DeepMReye/DeepMReye) the
labelled gaze data it needs to be fine-tuned on an individual participant, together with the
schedule, geometry and scoring code as a separate, testable module.

Documentation site: <https://markwgreenlee.github.io/gaze-calibration/>

A white dot steps around a virtual clock face on two rings — 7.5° and 3.75° of visual angle —
holding each position for one TR and returning to the centre for one TR between steps. Rings
alternate revolution by revolution, clockwise then anticlockwise. The participant looks at the dot
wherever it appears and presses a key whenever it briefly turns red; that detection task is what
enforces foveation of each target, which is what makes the run usable as calibration.

![Layout and timing](docs/calibration_layout.png)

Default protocol: 10 s central fixation, 96 targets of 2 s each, 10 s central fixation —
**404 s, 202 volumes at TR 2 s**, with every volume holding a single gaze position.

## Requirements

- **PsychoPy** 2022.2 or later. The Standalone build from [psychopy.org](https://www.psychopy.org)
  is the simplest route on Windows and is what this was developed against (2022.2.2, Python 3.8).
- **numpy**, only to write the label array; the Standalone build already includes it.
- `calib_schedule.py` and `test_calib_schedule.py` import nothing beyond the standard library, so
  the schedule can be inspected and tested on any machine with any Python 3.8+.

## Quick start

```bash
# verify the schedule and geometry — no PsychoPy needed, under a second
python test_calib_schedule.py

# desk check: 96 s, one ring, windowed
python neck_vib_calibration.py --participant TEST --session desk \
    --windowed --distance 600 --rings 7.5 --revolutions 1 --lead-in 0 --lead-out 0

# full protocol, full screen
python neck_vib_calibration.py --participant P01 --session lab

# in the scanner
python neck_vib_calibration.py --participant P01 --session scanner --scanner \
    --tr 2.0 --volumes 202 --series 7
```

On Windows, if PsychoPy is the Standalone build and another Python is first on your PATH (a conda
`(base)` prompt will do it), call it by absolute path:

```powershell
$PP = "C:\Program Files\PsychoPy\python.exe"
& $PP neck_vib_calibration.py --participant P01 --session lab
```

**Run `test_calib_schedule.py` before every session.** It takes under a second and catches a
mis-set eccentricity, viewing distance or screen specification before a participant is in the bore.

## Screen geometry

Target positions are computed with `tan()` from the measured panel geometry, not from PsychoPy's
small-angle `deg` units, so a stated eccentricity is that angle at the eye. Two consequences:

- **Set the physical size and distance for your display.** Defaults are a Dell P2723QE
  (597 × 336 mm active area) viewed at 1.2 m. Use `--screen-mm WIDTHxHEIGHT` and `--distance` for
  anything else — an in-bore projector above all.
- **Resolution is read from the live display, not assumed.** Windows display scaling changes the
  pixel pitch without changing the panel, so the script queries the real resolution before building
  the schedule and rebuilds it again from the window it actually got. It prints a line when it
  makes that correction.

The script refuses to run full screen if any target would fall off the display, naming the limit
rather than drawing off-screen. At 1.2 m on a 336 mm-high panel the maximum vertical eccentricity
is 7.97°, which is why the outer ring is 7.5° and not 10°.

## Deploying to the scanner computer

### 1. Get the code there

```bash
git clone https://github.com/markwgreenlee/gaze-calibration.git
cd gaze-calibration
python test_calib_schedule.py
```

All nine tests passing confirms the transfer is intact; they need only the standard library, so any
Python 3.8+ on that machine will run them. If the machine has no git, use **Code → Download ZIP** on
the repository page, or copy the folder from a USB stick — the code is 270 kB.

`logs/` is in `.gitignore`, so participant data never reaches the repository even if you commit from
the scanner computer. Copy the four files per run back by stick or lab share.

### 2. Locate the PsychoPy interpreter

PsychoPy has to be installed on that machine separately; the repository cannot carry it. Use the
Standalone build rather than pip-installing into a conda environment. Then call its interpreter by
absolute path, because the Python first on the PATH is usually a different one:

```bash
# macOS — the interpreter inside the app bundle
PP=/Applications/PsychoPy.app/Contents/MacOS/python
$PP neck_vib_calibration.py --participant P01 --session scanner

# if that path does not exist, find the bundled interpreter:
find /Applications/PsychoPy.app -name "python*" -maxdepth 4 -type f
```

```powershell
# Windows
$PP = "C:\Program Files\PsychoPy\python.exe"
& $PP neck_vib_calibration.py --participant P01 --session scanner
```

### 3. Measure the projector geometry

Three numbers, and the run is wrong without them:

- **Projected image width and height in mm**, measured with a tape on the screen at the back of the
  bore — the actual projected rectangle as set up, not the projector's specification.
- **Total optical path from the participant's eye to that screen**: eye to mirror plus mirror to
  screen. The fold does not change the effective distance; add the segments.
- **The display index** of the projector, usually `1` when it is the second display.

```bash
$PP neck_vib_calibration.py --participant P01 --session scanner --run 1 \
    --screen 1 --screen-mm 400x300 --distance 950
```

Keep `--screen-mm` and `--distance` on every subsequent command. They are the calibration.

### 4. Verify the geometry before collecting

`*_config.json` reports `derived.ring_radius_mm` for each ring — the radius the dot's excursion
*should* have on the screen. Start a run, go into the magnet room, and **measure the actual radius
with a tape**. Agreement within a few mm means the geometry is right; disagreement means
`--screen-mm` is wrong and every gaze label is mislabelled by that factor. This check costs five
minutes and is worth more than any amount of code reading.

Also read `derived.max_eccentricity_deg`. In-bore screens are smaller but much closer, so the
usable eccentricity is often *larger* than at a desk; if it comfortably exceeds the outer ring,
widening `--rings` improves coverage of the gaze range. The script refuses to run full screen if a
target would fall off the display.

Check `psychopy_window.mm_per_px` in the same file: it must equal your measured width divided by the
reported horizontal resolution. On macOS this is where a Retina backing store shows up — the script
re-reads the window it actually got and rebuilds the schedule, but confirm the number is plausible
rather than assuming. If full-screen on the second display misbehaves on macOS, check
*System Settings → Desktop & Dock → Displays have separate Spaces*.

### 5. The scanner run

```bash
$PP neck_vib_calibration.py --participant P01 --session scanner --run 1 \
    --screen 1 --screen-mm 400x300 --distance 950 \
    --scanner --tr 2.0 --volumes 202 --series 7
```

`--scanner` waits for the trigger instead of a keypress and listens for the button-box key rather
than `space`. Set `--volumes` to what the series actually acquires and `--series` to its number.

Confirm two site-specific assumptions on a dummy run, with nobody in the bore: that the trigger
arrives as a keypress of `5`, and that the button box sends `1`. Start the sequence, confirm the
script leaves its wait screen by itself, then press escape.

### Projector notes

For this experiment a VPixx PROPixx is just a second display — the script draws through PsychoPy's
normal window and needs no VPixx API. Two things to check with whoever administers it: that it is
**not** in one of the high-refresh sequential modes, which change the resolution the operating
system reports and would silently corrupt the degree-to-pixel conversion; and what refresh rate it
is actually set to. The script measures the real refresh and records it, so 60 or 120 Hz both work,
but you want to know which you got.

## Output

Four files per run, written to `logs/`:

| file | contents |
|---|---|
| `*_log.tsv` | every event with its measured time: epoch onsets with intended-versus-actual, flashes, key presses |
| `*_events.tsv` | BIDS-style events file for the GLM |
| `*_config.json` | full protocol, screen and window specification, plus the performance summary |
| `*_gazelabels.npy` | `(volumes, 10, 2)` intended gaze in degrees — the array DeepMReye fine-tuning consumes |

The `gaze_labels` block in the config states the convention explicitly: units, origin, axis signs,
what `t = 0` means, what a NaN means, the TR and the lead-volume count. **Check the vertical sign
against DeepMReye's own loader before fine-tuning** — it flips the vertical axis for its screen.

### Aligning labels to the acquisition

- `--tr` must match the calibration series, or every label lands in the wrong volume.
- `--volumes` sets the array length to the series length; it is padded with NaN or truncated.
- `--lead-volumes N` prepends N NaN rows when the scanner *keeps* its dummy volumes, so that label
  row *i* is series volume *i*. Leave it at 0 when dummies are discarded.

### Judging a session

Read the `performance` block before trusting the data:

- `hit_rate` and `false_alarms` — whether the participant was performing the task. Zero hits *and*
  zero false alarms means no key was pressed at all, not that capture failed.
- `effective_refresh_hz`, `frame_interval_ms`, `dropped_frames` — display timing.
- `onset_lag_ms` — measured minus intended epoch onset, excluding the first epoch, which carries
  window warm-up and is reported separately as `first_epoch`.

The presentation loop is driven by the clock, not by counting frames, so a late frame displaces one
stimulus by one frame and nothing accumulates.

## Parameters

| option | default | purpose |
|---|---|---|
| `--participant`, `--session`, `--run` | — | naming for the output files |
| `--scanner` | off | wait for the trigger character and accept the button-box key |
| `--rings` | `7.5,3.75` | ring eccentricities in degrees |
| `--ring-order` | `interleaved` | alternate rings per revolution, or `blocked` for two phases |
| `--revolutions` | 2 | revolutions per ring per direction |
| `--flashes` | 96 | red-flash targets; auto-rescales to ~1 per 4 s when `--revolutions` is set |
| `--lead-in`, `--lead-out` | 10 | central fixation at each end, seconds |
| `--distance` | 1200 | viewing distance in mm |
| `--screen-mm` | Dell P2723QE | physical active area as `WIDTHxHEIGHT` in mm |
| `--screen` | 0 | display index |
| `--tr`, `--volumes`, `--lead-volumes`, `--series` | 2.0, auto, 0, — | acquisition alignment |
| `--windowed` | off | small window for checking flow; **geometry is only correct full screen** |

Escape aborts a run cleanly and still writes every output file, with `aborted: true` recorded.

## Testing

`test_calib_schedule.py` holds nine checks: geometry against the physical panel, clock positions on
both rings, schedule shape and timing, two-ring interleaving (equal visits, equal mean onset,
blocked ordering separating the rings in time), the fixation brackets, flash placement, response
scoring, the gaze-label array, and a random-responder control confirming the detection task can
discriminate attention.

## Licence

See `LICENSE`.
