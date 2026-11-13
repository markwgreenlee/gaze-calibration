"""Gaze-calibration run for MR-based eye tracking (DeepMReye), neck-vibration study.

A white dot steps around a virtual clock face: twelve o'clock, back to centre, one o'clock, back
to centre, and so on, 1000 ms per position. Targets sit on two rings, 7.5 deg and 3.75 deg, so the
decoder is fine-tuned across the gaze range rather than only at its edge. Rings alternate
revolution by revolution — outer, inner, outer, inner clockwise, then the same anticlockwise — so
eccentricity is not confounded with time in the scanner. Two revolutions per ring per direction
give 192 s of targets, bracketed by 10 s of central fixation at each end so the discarded dummy
volumes and the BOLD baseline are not spent on a moving target: 212 s in total, 106 volumes at
TR 2 s. The participant looks at the dot wherever it appears and presses a key whenever it turns
red for 100 ms; that task is what enforces foveation of each target, which is what makes the run
usable as calibration.

Run it from a PsychoPy environment:

    python neck_vib_calibration.py --participant P01 --session lab
    python neck_vib_calibration.py --participant P01 --session scanner --scanner

In the scanner, --scanner waits for the trigger character (default "5") before the first frame and
accepts "1" as the response key; the trigger timestamp becomes t = 0 for every logged time, so the
log aligns with the first acquired volume.

Outputs, written next to the script in ./logs/:
    <base>_log.tsv        every frame-accurate event: epoch onsets, flashes, key presses
    <base>_events.tsv     BIDS-style events file for the GLM
    <base>_config.json    full configuration, monitor specification and performance summary
    <base>_gazelabels.npy  (n_volumes, 10, 2) intended gaze in degrees, for DeepMReye fine-tuning

Timing: the schedule is defined in seconds and each frame is drawn according to the elapsed time of
a single clock started at t = 0, so a dropped frame shifts one frame, never the whole run. Measured
onsets are written to the log; compare them against the intended ones before trusting a dataset.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from psychopy import core, event, monitors, visual
from psychopy.hardware import keyboard

import calib_schedule as cs

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- setup
def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--participant", required=True)
    p.add_argument("--session", default="lab")
    p.add_argument("--run", default="1")
    p.add_argument("--scanner", action="store_true",
                   help="wait for the scanner trigger and use the button-box key")
    p.add_argument("--rings", default=None,
                   help="comma-separated ring eccentricities in degrees (default 7.5,3.75)")
    p.add_argument("--ring-order", choices=("interleaved", "blocked"), default=None,
                   help="alternate the rings revolution by revolution (default) or run them "
                        "as two consecutive phases")
    p.add_argument("--flashes", type=int, default=None,
                   help="number of red-flash targets in the run (default 96, about one per 4 s)")
    p.add_argument("--revolutions", type=int, default=None,
                   help="revolutions per ring per direction (default 2; use 1 for a quick check)")
    p.add_argument("--distance", type=float, default=None,
                   help="override viewing distance in mm (default 1200)")
    p.add_argument("--lead-in", type=float, default=None,
                   help="central fixation before the first target, seconds (default 10)")
    p.add_argument("--lead-out", type=float, default=None,
                   help="central fixation after the last target, seconds (default 10)")
    p.add_argument("--screen", type=int, default=0, help="PsychoPy screen index")
    p.add_argument("--screen-mm", default=None,
                   help="physical active area of the display as WIDTHxHEIGHT in mm, e.g. 597x336. "
                        "Required when not using the Dell P2723QE this was measured on")
    p.add_argument("--windowed", action="store_true", help="small window, for checking the code")
    p.add_argument("--volumes", type=int, default=None,
                   help="number of volumes in the acquired series; the label array is padded with "
                        "NaN or truncated to match (default: ceil(duration/TR))")
    p.add_argument("--tr", type=float, default=None,
                   help="repetition time of the calibration series in seconds (default 2.0). "
                        "Must match the acquisition or every gaze label is misaligned")
    p.add_argument("--lead-volumes", type=int, default=0,
                   help="volumes SAVED before the trigger that starts this script. Leave at 0 when "
                        "the scanner discards its dummies; set it when they are kept, and that "
                        "many NaN rows are prepended so label row i is series volume i")
    p.add_argument("--series", default=None,
                   help="scanner series number or name this run accompanies, recorded in the log")
    return p.parse_args()


def build_config(args):
    screen = cs.Screen()
    proto = cs.Protocol()
    if args.screen_mm is not None:
        w_mm, h_mm = (float(v) for v in args.screen_mm.lower().split("x"))
        screen.width_mm, screen.height_mm, screen.name = w_mm, h_mm, "user-specified panel"
    res = detect_resolution(args.screen)
    if res and list(res) != [screen.width_px, screen.height_px]:
        print(f"display {args.screen} is running at {res[0]}x{res[1]}, not the "
              f"{screen.width_px}x{screen.height_px} recorded in its EDID; using the live "
              f"resolution, physical size {screen.width_mm:.0f}x{screen.height_mm:.0f} mm unchanged")
        screen.width_px, screen.height_px = res
    if args.rings is not None:
        proto.eccentricities_deg = tuple(float(v) for v in args.rings.split(","))
    if args.ring_order is not None:
        proto.ring_order = args.ring_order
    if args.flashes is not None:
        proto.n_flashes = args.flashes
    if args.revolutions is not None:
        proto.revolutions = args.revolutions
    if args.tr is not None:
        proto.tr_s = args.tr
    if args.distance is not None:
        screen.distance_mm = args.distance
    if args.lead_in is not None:
        proto.lead_in_s = args.lead_in
    if args.lead_out is not None:
        proto.lead_out_s = args.lead_out
    proto.wait_for_trigger = bool(args.scanner)
    if args.flashes is None and args.revolutions is not None:
        # Hold the agreed rate of roughly one flash per 4 s when the run is shortened. Computed
        # last, so --lead-in and --lead-out are already in the duration.
        proto.n_flashes = max(1, round(cs.total_duration_s(proto) / 4))
    off = [e for e in cs.build_schedule(proto, screen) if not screen.fits(e.x_deg, e.y_deg, 5.0)]
    if off:
        bad = sorted({e.ring_deg for e in off})
        raise SystemExit(
            f"{len(off)} target positions fall off this screen at {screen.distance_mm/1000:.2f} m "
            f"(rings {bad} deg). Maximum vertical eccentricity here is "
            f"{screen.max_eccentricity_deg()['vertical']:.2f} deg. Lower --rings or --distance.")
    return screen, proto


def detect_resolution(screen_index: int):
    """Actual pixel size of the display we are about to use, or None if it cannot be read.

    The physical panel size is fixed, so the pixel pitch — and therefore every target position —
    depends on the resolution Windows is currently driving. Reading it here rather than trusting
    the EDID default is what keeps a stated eccentricity true on the screen.
    """
    try:
        import pyglet
        try:
            disp = pyglet.canvas.get_display()          # pyglet 1.x, shipped with PsychoPy 2022
        except AttributeError:
            disp = pyglet.display.get_display()         # pyglet 2.x
        scr = disp.get_screens()[screen_index]
        return int(scr.width), int(scr.height)
    except Exception:
        return None


def make_window(screen: cs.Screen, args):
    mon = monitors.Monitor("vib_calibration", width=screen.width_mm / 10.0,
                           distance=screen.distance_mm / 10.0)
    mon.setSizePix([screen.width_px, screen.height_px])
    try:
        mon.save()      # silences "Monitor specification not found"; harmless if it fails
    except Exception:
        pass
    size = [1280, 720] if args.windowed else [screen.width_px, screen.height_px]
    win = visual.Window(size=size, fullscr=not args.windowed, screen=args.screen, monitor=mon,
                        units="pix", color=[-1, -1, -1], colorSpace="rgb", allowGUI=args.windowed,
                        waitBlanking=True)
    win.mouseVisible = False
    return win


# --------------------------------------------------------------------------- run
def main():
    args = parse_args()
    screen, proto = build_config(args)
    epochs = cs.build_schedule(proto, screen)
    flash_onsets = cs.add_flashes(epochs, proto)
    duration = cs.total_duration_s(proto)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"sub-{args.participant}_ses-{args.session}_run-{args.run}_{stamp}"
    outdir = HERE / "logs"
    outdir.mkdir(exist_ok=True)
    log_rows = []

    def log(t, event_name, **kw):
        # 'event_name' rather than 'event' or 'kind': both appear as payload keys below, and a
        # collision between a positional parameter and a keyword is a TypeError at runtime.
        log_rows.append({"t_s": round(float(t), 4), "event": event_name, **kw})

    win = make_window(screen, args)
    # Last word on geometry: what the window actually got. In full screen this must match the
    # display; if it does not (detection failed, or the driver clamped the request) the schedule
    # is rebuilt against the real pixel count so a stated eccentricity is still true on screen.
    actual = [int(v) for v in win.size]
    if not args.windowed and actual != [screen.width_px, screen.height_px]:
        print(f"window opened at {actual[0]}x{actual[1]}; rebuilding target positions for it")
        screen.width_px, screen.height_px = actual
        epochs = cs.build_schedule(proto, screen)
        flash_onsets = cs.add_flashes(epochs, proto)
    off = [e for e in epochs if not screen.fits(e.x_deg, e.y_deg, 5.0)]
    if off and not args.windowed:
        win.close()
        raise SystemExit(
            f"{len(off)} targets fall off a {screen.width_mm:.0f}x{screen.height_mm:.0f} mm screen "
            f"at {screen.distance_mm/1000:.2f} m. Maximum vertical eccentricity is "
            f"{screen.max_eccentricity_deg()['vertical']:.2f} deg. Lower --rings or --distance, "
            "or give the true panel size with --screen-mm.")
    dot_px = screen.deg_to_px_size(proto.dot_diameter_deg)
    dot = visual.Circle(win, radius=dot_px / 2, units="pix", fillColor="white",
                        lineColor=None, autoLog=False)
    msg = visual.TextStim(win, units="pix", height=38, color="white", wrapWidth=1400, autoLog=False,
                          text=("Look straight at the dot wherever it appears.\n\n"
                                "Press the response key every time it briefly turns red.\n\n"
                                + ("Waiting for the scanner trigger…" if proto.wait_for_trigger
                                   else "Press space to begin.")))
    msg.draw()
    win.flip()

    kb = keyboard.Keyboard()
    start_keys = [proto.trigger_key] if proto.wait_for_trigger else ["space"]
    keys = event.waitKeys(keyList=start_keys + [proto.quit_key])
    if proto.quit_key in keys:
        win.close()
        core.quit()

    clock = core.Clock()          # t = 0 at the trigger / keypress
    kb.clock.reset()
    kb.clearEvents()
    log(0.0, "run_start", detail="trigger" if proto.wait_for_trigger else "keypress")

    responses, n_frames, aborted = [], 0, False
    shown = {e.index: False for e in epochs}
    flash_logged = set()
    ei = 0
    last_pos, last_flash = (None, None), None
    win.recordFrameIntervals = True
    win.refreshThreshold = 1.0 / screen.refresh_hz * 1.2
    while True:
        t = clock.getTime()
        if t >= duration:
            break
        while ei + 1 < len(epochs) and t >= epochs[ei + 1].onset_s:
            ei += 1
        e = epochs[ei]
        is_flash = (e.flash_onset_s is not None
                    and e.flash_onset_s <= t < e.flash_onset_s + proto.flash_ms / 1000.0)
        # Assign only on change. Setting pos or fillColor re-runs PsychoPy's attribute machinery
        # (colour strings are re-resolved on every assignment), which is enough per-frame work to
        # cost dropped frames on a 4K display.
        if (e.x_px, e.y_px) != last_pos:
            dot.pos = (e.x_px, e.y_px)
            last_pos = (e.x_px, e.y_px)
        if is_flash != last_flash:
            dot.fillColor = "red" if is_flash else "white"
            last_flash = is_flash
        dot.draw()
        win.flip()
        n_frames += 1
        t_flip = clock.getTime()
        if not shown[e.index]:
            shown[e.index] = True
            log(t_flip, "epoch_onset", epoch=e.index, kind=e.kind, direction=e.direction,
                revolution=e.revolution, clock_pos=e.clock_pos, ring_deg=e.ring_deg,
                x_deg=round(e.x_deg, 3),
                y_deg=round(e.y_deg, 3), x_px=round(e.x_px, 1), y_px=round(e.y_px, 1),
                intended_onset_s=round(e.onset_s, 4))
        if is_flash and e.index not in flash_logged:
            flash_logged.add(e.index)
            log(t_flip, "flash_onset", epoch=e.index, clock_pos=e.clock_pos,
                intended_onset_s=round(e.flash_onset_s, 4))
        for k in kb.getKeys(list(proto.response_keys) + [proto.quit_key], waitRelease=False):
            if k.name == proto.quit_key:
                aborted = True
                break
            responses.append(k.rt)
            log(k.rt, "response", key=k.name)
        if aborted:
            break

    win.flip()
    t_end = clock.getTime()
    log(t_end, "run_end", aborted=aborted, frames=n_frames)

    # ----------------------------------------------------------------- scoring
    summary = cs.score_responses(flash_onsets, responses)
    iv = sorted(win.frameIntervals[5:])          # drop the first few, they include window warm-up
    summary.update({"frames_drawn": n_frames,
                    "expected_frames_at_nominal_hz": round(duration * screen.refresh_hz),
                    "effective_refresh_hz": round(n_frames / t_end, 2) if t_end else None,
                    "run_duration_s": round(t_end, 3), "aborted": aborted,
                    "frame_interval_ms": ({"median": round(1000 * iv[len(iv) // 2], 2),
                                           "p95": round(1000 * iv[int(0.95 * len(iv))], 2),
                                           "max": round(1000 * iv[-1], 2)} if iv else None),
                    "dropped_frames": int(win.nDroppedFrames)})

    # ----------------------------------------------------------------- outputs
    onset_rows = [r for r in log_rows if r["event"] == "epoch_onset"]
    lag = [r["t_s"] - r["intended_onset_s"] for r in onset_rows]
    # The first epoch carries window warm-up and is reported separately, so the median and max
    # describe the steady state a reader would want to judge the session by.
    steady = sorted(lag[1:]) or sorted(lag)
    summary["onset_lag_ms"] = ({"median": round(1000 * steady[len(steady) // 2], 2),
                                "p95": round(1000 * steady[int(0.95 * (len(steady) - 1))], 2),
                                "max": round(1000 * steady[-1], 2),
                                "first_epoch": round(1000 * lag[0], 2)} if lag else None)

    cols = ["t_s", "event", "epoch", "kind", "direction", "revolution", "clock_pos", "ring_deg",
            "x_deg", "y_deg", "x_px", "y_px", "intended_onset_s", "key", "aborted", "frames",
            "detail"]
    with open(outdir / f"{base}_log.tsv", "w", encoding="utf-8") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in log_rows:
            fh.write("\t".join(str(r.get(c, "n/a")) for c in cols) + "\n")

    cs.write_events_tsv(outdir / f"{base}_events.tsv", epochs)

    # Gaze labels on the acquisition's volume grid. Row i is series volume i: `--lead-volumes`
    # NaN rows stand for volumes saved before the trigger, then the schedule, then NaN padding or
    # truncation so the array length equals the series length.
    n_sched = int(-(-duration // proto.tr_s))
    labels = [[[float("nan"), float("nan")]] * proto.subtr_per_tr
              for _ in range(args.lead_volumes)] + cs.gaze_trace(epochs, n_sched, proto)
    n_vol = args.volumes if args.volumes else len(labels)
    if len(labels) < n_vol:
        labels += [[[float("nan"), float("nan")]] * proto.subtr_per_tr
                   for _ in range(n_vol - len(labels))]
    labels = labels[:n_vol]
    try:
        import numpy as np
        np.save(outdir / f"{base}_gazelabels.npy", np.asarray(labels, dtype="float32"))
    except ImportError:
        with open(outdir / f"{base}_gazelabels.json", "w", encoding="utf-8") as fh:
            json.dump(labels, fh)

    header = cs.config_header(proto, screen, extra={
        "participant": args.participant, "session": args.session, "run": args.run,
        "started": stamp,
        "psychopy_window": {"size": [int(v) for v in win.size], "fullscreen": not args.windowed,
                            "measured_refresh_hz": win.getActualFrameRate(),
                            "mm_per_px": [screen.mm_per_px_x, screen.mm_per_px_y],
                            "psychopy_version": __import__("psychopy").__version__},
        "performance": summary,
        "gaze_labels": {
            "file": f"{base}_gazelabels.npy",
            "shape": [n_vol, proto.subtr_per_tr, 2],
            "volumes": n_vol,
            "lead_volumes_nan": args.lead_volumes,
            "tr_s": proto.tr_s,
            "units": "degrees of visual angle at the eye",
            "origin": "screen centre = (0, 0)",
            "axes": "index 0 = horizontal, positive right; index 1 = vertical, positive UP",
            "time_zero": "trigger" if proto.wait_for_trigger else "experimenter keypress",
            "sampling": "centre of each sub-TR bin; row i is series volume i",
            "nan_meaning": "no scheduled gaze position for that bin (before or after the run)",
            "note": ("DeepMReye's own calibration loader flips the vertical axis for its screen; "
                     "check the sign against this convention before fine-tuning."),
            "scanner_series": args.series}})
    with open(outdir / f"{base}_config.json", "w", encoding="utf-8") as fh:
        json.dump(header, fh, indent=2)

    win.close()
    print(json.dumps(summary, indent=2))
    core.quit()


if __name__ == "__main__":
    sys.exit(main())
