"""Geometry, trial schedule and DeepMReye label construction for the gaze-calibration run.

Deliberately free of any PsychoPy import so the schedule can be built, checked and plotted on a
machine with no display. `neck_vib_calibration.py` imports from here and only adds presentation.

Screen geometry is computed with tan(), not PsychoPy's small-angle 'deg' units, so an eccentricity
in degrees means the same thing here as on the screen: a point whose visual angle from the centre
of gaze is exactly that value, for a viewer at the configured distance facing the screen centre.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict, field


# --------------------------------------------------------------------------- configuration
@dataclass
class Screen:
    """Physical display. Defaults are the Dell P2723QE read from its EDID on this machine."""
    name: str = "DELL P2723QE"
    width_px: int = 3840
    height_px: int = 2160
    width_mm: float = 597.0
    height_mm: float = 336.0
    refresh_hz: float = 60.0
    distance_mm: float = 1200.0

    @property
    def mm_per_px_x(self) -> float:
        return self.width_mm / self.width_px

    @property
    def mm_per_px_y(self) -> float:
        return self.height_mm / self.height_px

    def deg_to_px(self, dx_deg: float, dy_deg: float) -> tuple[float, float]:
        """Visual angle -> pixel offset from screen centre (+x right, +y up, PsychoPy convention)."""
        dx_mm = self.distance_mm * math.tan(math.radians(dx_deg))
        dy_mm = self.distance_mm * math.tan(math.radians(dy_deg))
        return dx_mm / self.mm_per_px_x, dy_mm / self.mm_per_px_y

    def deg_to_px_size(self, size_deg: float) -> float:
        """Diameter in degrees -> diameter in pixels, at the screen centre."""
        return 2 * self.distance_mm * math.tan(math.radians(size_deg / 2)) / self.mm_per_px_x

    def fits(self, dx_deg: float, dy_deg: float, margin_mm: float = 0.0) -> bool:
        x_px, y_px = self.deg_to_px(dx_deg, dy_deg)
        return (abs(x_px * self.mm_per_px_x) <= self.width_mm / 2 - margin_mm
                and abs(y_px * self.mm_per_px_y) <= self.height_mm / 2 - margin_mm)

    def max_eccentricity_deg(self) -> dict:
        return {"horizontal": math.degrees(math.atan((self.width_mm / 2) / self.distance_mm)),
                "vertical": math.degrees(math.atan((self.height_mm / 2) / self.distance_mm))}


@dataclass
class Protocol:
    # Two rings: the outer one sets the gaze range, the inner one samples the interior where
    # vibration-induced drift of a degree or two will sit. 10 deg does not fit vertically on the
    # Dell panel at 1.2 m, hence 7.5.
    eccentricities_deg: tuple = (7.5, 3.75)
    # "interleaved" alternates the rings revolution by revolution, so eccentricity is not
    # confounded with time in the scanner; "blocked" runs all revolutions of one ring, then the
    # other, which is simpler to describe but puts the inner ring entirely in the second half.
    ring_order: str = "interleaved"
    n_positions: int = 12              # hour-hand positions on a virtual clock
    revolutions: int = 2               # per ring, per direction
    directions: tuple = ("cw", "ccw")  # order of travel
    # One TR each, so every volume falls entirely on a target or entirely on central fixation and
    # no training label is a within-volume mixture. A position therefore costs 2 TRs.
    eccentric_ms: int = 2000
    centre_ms: int = 2000
    # Central fixation before the first target and after the last one, so the discarded dummy
    # volumes and the BOLD baseline are not spent on a moving target. Set both to 0 for a quick
    # lab check without the baseline; the scanner runs keep 10 s.
    lead_in_s: float = 10.0
    lead_out_s: float = 10.0
    dot_diameter_deg: float = 0.35
    # detection task
    flash_ms: int = 100
    n_flashes: int = 96                # one per ~4 s, the rate agreed for the single-ring run
    flash_min_gap_s: float = 2.0
    # Flash onset within an epoch: late enough that a saccade has landed, early enough that the
    # flash ends well before the epoch does. The wide window keeps the timing unpredictable.
    flash_window_ms: tuple = (400, 1600)
    flash_on_centre_epochs: bool = True
    rng_seed: int = 20261023
    # response / sync
    response_keys: tuple = ("space", "1")   # lab keyboard and scanner button box
    trigger_key: str = "5"
    wait_for_trigger: bool = False          # True in the scanner
    quit_key: str = "escape"
    # scan parameters, only used when writing DeepMReye labels
    tr_s: float = 2.0
    subtr_per_tr: int = 10


@dataclass
class Epoch:
    index: int
    direction: str
    revolution: int
    hour: int | None          # step index 0..11 along the direction of travel; None at centre
    clock_pos: int | None     # clock position actually displayed (12 = twelve o'clock)
    ring_deg: float           # eccentricity of the ring this epoch belongs to
    kind: str                 # "eccentric" | "centre"
    onset_s: float
    duration_s: float
    x_deg: float
    y_deg: float
    x_px: float = 0.0
    y_px: float = 0.0
    flash_onset_s: float | None = None   # absolute time of the red flash, if this epoch carries one


# --------------------------------------------------------------------------- schedule
def clock_position(hour: int, eccentricity_deg: float, direction: str) -> tuple[float, float]:
    """Hour-hand position on a virtual clock: hour 0 = twelve o'clock = straight up.

    Angles run clockwise for 'cw' (12 -> 1 -> 2 ...) and anticlockwise for 'ccw' (12 -> 11 -> 10 ...).
    Returns (x_deg, y_deg) with +x right and +y up.
    """
    step = 360.0 / 12
    sign = 1.0 if direction == "cw" else -1.0
    theta = math.radians(sign * hour * step)
    return eccentricity_deg * math.sin(theta), eccentricity_deg * math.cos(theta)


def build_schedule(proto: Protocol, screen: Screen) -> list[Epoch]:
    """Alternating eccentric/centre epochs, two revolutions per direction."""
    epochs: list[Epoch] = []
    t = 0.0
    idx = 0
    if proto.lead_in_s > 0:
        epochs.append(Epoch(index=idx, direction="n/a", revolution=-1, hour=None, clock_pos=None,
                            ring_deg=0.0, kind="centre", onset_s=0.0, duration_s=proto.lead_in_s,
                            x_deg=0.0, y_deg=0.0))
        t += proto.lead_in_s
        idx += 1

    rings = tuple(proto.eccentricities_deg)
    if proto.ring_order == "interleaved":
        # Alternate rings revolution by revolution, and start each direction on a different ring
        # (cw: outer, inner, outer, inner; ccw: inner, outer, inner, outer) so the two rings have
        # the same mean time in the run and neither is systematically late.
        plan = [(d, rings[(r + i) % len(rings)], r // len(rings))
                for i, d in enumerate(proto.directions)
                for r in range(proto.revolutions * len(rings))]
    elif proto.ring_order == "blocked":
        plan = [(d, ring, r) for ring in rings
                for d in proto.directions for r in range(proto.revolutions)]
    else:
        raise ValueError(f"ring_order must be 'interleaved' or 'blocked', not {proto.ring_order!r}")

    for direction, ring, rev in plan:
        for h in range(proto.n_positions):
            x, y = clock_position(h, ring, direction)
            for kind, dur_ms, (xx, yy) in (("eccentric", proto.eccentric_ms, (x, y)),
                                           ("centre", proto.centre_ms, (0.0, 0.0))):
                px, py = screen.deg_to_px(xx, yy)
                disp = 12 if h == 0 else (h if direction == "cw" else 12 - h)
                epochs.append(Epoch(index=idx, direction=direction, revolution=rev,
                                    hour=h if kind == "eccentric" else None,
                                    clock_pos=disp if kind == "eccentric" else None,
                                    ring_deg=ring if kind == "eccentric" else 0.0, kind=kind,
                                    onset_s=t, duration_s=dur_ms / 1000.0,
                                    x_deg=xx, y_deg=yy, x_px=px, y_px=py))
                t += dur_ms / 1000.0
                idx += 1
    if proto.lead_out_s > 0:
        epochs.append(Epoch(index=idx, direction="n/a", revolution=-1, hour=None, clock_pos=None,
                            ring_deg=0.0, kind="centre", onset_s=t, duration_s=proto.lead_out_s,
                            x_deg=0.0, y_deg=0.0))
    return epochs


def add_flashes(epochs: list[Epoch], proto: Protocol) -> list[float]:
    """Assign red-flash onsets to a random subset of epochs, respecting a minimum gap.

    The flash is placed between flash_window_ms after epoch onset, i.e. after a saccade to a new
    target would have landed, and it always ends before the epoch does.
    """
    import random
    rng = random.Random(proto.rng_seed)
    lo, hi = proto.flash_window_ms
    assert hi + proto.flash_ms <= min(proto.eccentric_ms, proto.centre_ms), \
        "flash window must end before the shortest epoch does"
    pool = [e for e in epochs
            if (proto.flash_on_centre_epochs or e.kind == "eccentric") and e.index > 0]
    rng.shuffle(pool)
    chosen, onsets = [], []
    for e in pool:
        if len(chosen) == proto.n_flashes:
            break
        t = e.onset_s + rng.uniform(lo, hi) / 1000.0
        if all(abs(t - o) >= proto.flash_min_gap_s for o in onsets):
            e.flash_onset_s = t
            chosen.append(e)
            onsets.append(t)
    onsets.sort()
    return onsets


def total_duration_s(proto: Protocol) -> float:
    per_revolution = proto.n_positions * (proto.eccentric_ms + proto.centre_ms)
    n_revolutions = proto.revolutions * len(proto.eccentricities_deg) * len(proto.directions)
    return n_revolutions * per_revolution / 1000.0 + proto.lead_in_s + proto.lead_out_s


# --------------------------------------------------------------------------- DeepMReye labels
def gaze_trace(epochs: list[Epoch], n_volumes: int, proto: Protocol, t_zero_s: float = 0.0):
    """Intended gaze position sampled at DeepMReye's sub-TR resolution.

    Returns a nested list shaped (n_volumes, subtr_per_tr, 2) holding (x_deg, y_deg), the format
    DeepMReye expects for fine-tuning labels. Samples are taken at the centre of each sub-TR bin;
    a bin whose centre falls outside the schedule is filled with NaN so it can be dropped.
    """
    import bisect
    dt = proto.tr_s / proto.subtr_per_tr
    starts = [e.onset_s for e in epochs]
    end = epochs[-1].onset_s + epochs[-1].duration_s
    out = []
    for v in range(n_volumes):
        row = []
        for s in range(proto.subtr_per_tr):
            t = t_zero_s + v * proto.tr_s + (s + 0.5) * dt
            if t < starts[0] or t >= end:
                row.append([float("nan"), float("nan")])
                continue
            e = epochs[bisect.bisect_right(starts, t) - 1]
            row.append([e.x_deg, e.y_deg])
        out.append(row)
    return out


# --------------------------------------------------------------------------- scoring
def score_responses(flash_onsets: list[float], response_times: list[float],
                    window_s: tuple = (0.10, 1.20)) -> dict:
    """Match key presses to red flashes.

    Greedy earliest-first matching: each flash takes the earliest unused response that falls in
    its window, so one press cannot be credited to two flashes and a double press on one flash
    counts as one hit plus one false alarm. Responses faster than the lower bound are treated as
    false alarms rather than hits, since nobody reacts to a flash in under 100 ms.
    """
    lo, hi = window_s
    used, rts = set(), []
    for onset in sorted(flash_onsets):
        for i, r in sorted(enumerate(response_times), key=lambda p: p[1]):
            if i in used:
                continue
            if lo <= r - onset <= hi:
                used.add(i)
                rts.append(r - onset)
                break
    rts.sort()
    n = len(flash_onsets)
    return {"n_flashes": n, "hits": len(used), "misses": n - len(used),
            "false_alarms": len(response_times) - len(used),
            "hit_rate": round(len(used) / n, 3) if n else None,
            "median_rt_s": round(rts[len(rts) // 2], 3) if rts else None,
            "mean_rt_s": round(sum(rts) / len(rts), 3) if rts else None}


# --------------------------------------------------------------------------- serialisation
def config_header(proto: Protocol, screen: Screen, extra: dict | None = None) -> dict:
    n_epochs = (proto.revolutions * len(proto.eccentricities_deg) * len(proto.directions)
                * proto.n_positions * 2
                + (proto.lead_in_s > 0) + (proto.lead_out_s > 0))
    head = {"screen": asdict(screen), "protocol": asdict(proto),
            "derived": {"total_duration_s": total_duration_s(proto),
                        "n_volumes_at_tr": total_duration_s(proto) / proto.tr_s,
                        "n_epochs": n_epochs,
                        "ring_radius_mm": {f"{e} deg": screen.distance_mm
                                           * math.tan(math.radians(e))
                                           for e in proto.eccentricities_deg},
                        "dot_diameter_px": screen.deg_to_px_size(proto.dot_diameter_deg),
                        "max_eccentricity_deg": screen.max_eccentricity_deg()}}
    if extra:
        head.update(extra)
    return head


def epochs_to_rows(epochs: list[Epoch]) -> list[dict]:
    return [asdict(e) for e in epochs]


def write_events_tsv(path, epochs: list[Epoch]) -> None:
    """BIDS-style events file: one row per epoch, plus the flash events."""
    cols = ["onset", "duration", "trial_type", "clock_pos", "ring_deg", "direction",
            "x_deg", "y_deg"]
    lines = ["\t".join(cols)]
    for e in epochs:
        lines.append("\t".join([f"{e.onset_s:.3f}", f"{e.duration_s:.3f}",
                                (f"ring{e.ring_deg:g}_{e.direction}" if e.kind == "eccentric"
                                 else "centre"),
                                "n/a" if e.clock_pos is None else str(e.clock_pos),
                                f"{e.ring_deg:g}", e.direction,
                                f"{e.x_deg:.3f}", f"{e.y_deg:.3f}"]))
    for e in epochs:
        if e.flash_onset_s is not None:
            lines.append("\t".join([f"{e.flash_onset_s:.3f}", f"{0.001 * 100:.3f}", "target_flash",
                                    "n/a" if e.clock_pos is None else str(e.clock_pos),
                                    f"{e.ring_deg:g}", e.direction,
                                    f"{e.x_deg:.3f}", f"{e.y_deg:.3f}"]))
    lines.sort(key=lambda s: (s.split("\t")[0] != "onset", float(s.split("\t")[0])
                              if s.split("\t")[0] != "onset" else -1))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
