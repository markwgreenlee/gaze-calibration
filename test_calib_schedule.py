"""Checks on the calibration schedule. Run before a session: `python test_calib_schedule.py`.

These cover the parts that cannot be eyeballed during a run — geometry against the physical panel,
epoch counts and timing, flash placement, response scoring and the DeepMReye label array. They
need no PsychoPy and no display.
"""
import math
import random

import calib_schedule as cs


def test_geometry_matches_panel():
    s = cs.Screen()
    assert abs(s.mm_per_px_x - 597 / 3840) < 1e-9
    # 7.5 deg at 1.2 m, computed independently of the module
    r_mm = 1200 * math.tan(math.radians(7.5))
    x_px, y_px = s.deg_to_px(0.0, 7.5)
    assert abs(y_px * s.mm_per_px_y - r_mm) < 1e-6 and abs(x_px) < 1e-9
    assert s.fits(0.0, 7.5, margin_mm=5.0), "twelve o'clock must clear the screen edge"
    assert not s.fits(0.0, 10.0), "10 deg must be rejected on this panel at 1.2 m"
    assert abs(s.max_eccentricity_deg()["vertical"] - 7.97) < 0.01


def test_clock_positions():
    p = cs.Protocol()
    for ecc in p.eccentricities_deg:
        # twelve o'clock is straight up; three o'clock is right in cw and left in ccw
        assert cs.clock_position(0, ecc, "cw") == (0.0, ecc)
        x, y = cs.clock_position(3, ecc, "cw")
        assert abs(x - ecc) < 1e-9 and abs(y) < 1e-9
        x, y = cs.clock_position(3, ecc, "ccw")
        assert abs(x + ecc) < 1e-9 and abs(y) < 1e-9
        # every position sits at the requested eccentricity
        for h in range(12):
            for d in ("cw", "ccw"):
                x, y = cs.clock_position(h, ecc, d)
                assert abs(math.hypot(x, y) - ecc) < 1e-9


def test_schedule_shape_and_timing():
    """Target sequence only; the default 10 s fixation brackets are checked separately."""
    p, s = cs.Protocol(lead_in_s=0.0, lead_out_s=0.0), cs.Screen()
    ep = cs.build_schedule(p, s)
    # 2 rings x 2 directions x 2 revolutions x 12 positions, each a target plus a centre epoch
    assert len(ep) == 192 and abs(cs.total_duration_s(p) - 384.0) < 1e-9
    assert sum(e.kind == "eccentric" for e in ep) == 96
    # epochs tile the run with no gap and no overlap
    for a, b in zip(ep, ep[1:]):
        assert abs((a.onset_s + a.duration_s) - b.onset_s) < 1e-9
    assert abs(ep[-1].onset_s + ep[-1].duration_s - 384.0) < 1e-9
    # eccentric and centre strictly alternate, and every target is on screen
    assert all(e.kind == ("eccentric" if i % 2 == 0 else "centre") for i, e in enumerate(ep))
    assert all(s.fits(e.x_deg, e.y_deg, 5.0) for e in ep)
    # displayed clock positions run 12,1,2,... then 12,11,10,...
    cw = [e.clock_pos for e in ep if e.kind == "eccentric" and e.direction == "cw"][:12]
    ccw = [e.clock_pos for e in ep if e.kind == "eccentric" and e.direction == "ccw"][:12]
    assert cw == [12, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert ccw == [12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    # each epoch is one TR, so every volume lands on a single gaze position
    assert all(abs(e.duration_s - p.tr_s) < 1e-9 for e in ep)


def test_two_rings():
    p, s = cs.Protocol(), cs.Screen()
    ep = cs.build_schedule(p, s)
    targets = [e for e in ep if e.kind == "eccentric"]
    # both rings are visited equally often, and each target sits on its own ring
    per_ring = {r: [e for e in targets if e.ring_deg == r] for r in p.eccentricities_deg}
    assert {r: len(v) for r, v in per_ring.items()} == {7.5: 48, 3.75: 48}
    for r, v in per_ring.items():
        assert all(abs(math.hypot(e.x_deg, e.y_deg) - r) < 1e-9 for e in v)
        assert sorted({e.clock_pos for e in v}) == list(range(1, 13))
    # interleaving balances the two rings in time: equal mean onset, neither one late
    means = {r: sum(e.onset_s for e in v) / len(v) for r, v in per_ring.items()}
    assert abs(means[7.5] - means[3.75]) < 1e-6
    # rings alternate revolution by revolution, starting on a different ring per direction
    per_rev = [(e.direction, e.ring_deg) for e in targets[::p.n_positions]]
    assert per_rev == [("cw", 7.5), ("cw", 3.75), ("cw", 7.5), ("cw", 3.75),
                       ("ccw", 3.75), ("ccw", 7.5), ("ccw", 3.75), ("ccw", 7.5)]
    # blocked ordering is available and puts one ring entirely after the other
    b = cs.build_schedule(cs.Protocol(ring_order="blocked"), s)
    bt = [e for e in b if e.kind == "eccentric"]
    assert max(e.onset_s for e in bt if e.ring_deg == 7.5) < min(e.onset_s for e in bt
                                                                 if e.ring_deg == 3.75)


def test_lead_in_out():
    """The shipped default brackets the targets with 10 s of central fixation at each end."""
    p = cs.Protocol()
    assert p.lead_in_s == 10.0 and p.lead_out_s == 10.0
    ep = cs.build_schedule(p, cs.Screen())
    assert len(ep) == 194 and abs(cs.total_duration_s(p) - 404.0) < 1e-9
    assert abs(cs.total_duration_s(p) / p.tr_s - 202) < 1e-9, "404 s is a whole number of TRs"
    assert ep[0].kind == "centre" and ep[0].duration_s == 10.0
    assert ep[-1].kind == "centre" and ep[-1].duration_s == 10.0
    assert ep[0].x_deg == 0.0 and ep[0].y_deg == 0.0
    first_target = next(e for e in ep if e.kind == "eccentric")
    last_target = [e for e in ep if e.kind == "eccentric"][-1]
    assert abs(first_target.onset_s - 10.0) < 1e-9
    assert abs(last_target.onset_s + last_target.duration_s - 392.0) < 1e-9
    for a, b in zip(ep, ep[1:]):            # still gapless with the brackets in place
        assert abs((a.onset_s + a.duration_s) - b.onset_s) < 1e-9
    # the brackets are whole numbers of TRs too, so nothing straddles a volume boundary
    assert all(abs((e.onset_s % p.tr_s)) < 1e-9 for e in ep)
    # turning them off reproduces the bare target sequence
    p0 = cs.Protocol(lead_in_s=0.0, lead_out_s=0.0)
    assert len(cs.build_schedule(p0, cs.Screen())) == 192


def test_flashes():
    p, s = cs.Protocol(), cs.Screen()
    ep = cs.build_schedule(p, s)
    onsets = cs.add_flashes(ep, p)
    assert len(onsets) == p.n_flashes
    assert all(b - a >= p.flash_min_gap_s - 1e-9 for a, b in zip(onsets, onsets[1:]))
    lo, hi = p.flash_window_ms
    for e in ep:
        if e.flash_onset_s is None:
            continue
        into = (e.flash_onset_s - e.onset_s) * 1000
        assert lo <= into <= hi, "flash must start inside its epoch's window"
        assert into + p.flash_ms <= e.duration_s * 1000, "flash must end before the epoch does"
    # the same seed reproduces the same run
    ep2 = cs.build_schedule(p, s)
    assert cs.add_flashes(ep2, p) == onsets


def test_scoring():
    flashes = [1.0, 5.0, 9.0]
    # perfect responder: one press 400 ms after each flash
    r = cs.score_responses(flashes, [f + 0.4 for f in flashes])
    assert (r["hits"], r["misses"], r["false_alarms"]) == (3, 0, 0)
    assert abs(r["median_rt_s"] - 0.4) < 1e-9 and r["hit_rate"] == 1.0
    # one miss and one press too early to be a response
    r = cs.score_responses(flashes, [1.4, 5.05, 9.4])
    assert (r["hits"], r["misses"], r["false_alarms"]) == (2, 1, 1)
    # double press on one flash: one hit, one false alarm
    r = cs.score_responses([1.0], [1.3, 1.5])
    assert (r["hits"], r["false_alarms"]) == (1, 1)
    # a press cannot be credited to two flashes
    r = cs.score_responses([1.0, 1.5], [1.6])
    assert r["hits"] == 1 and r["misses"] == 1
    # nobody responding
    r = cs.score_responses(flashes, [])
    assert (r["hits"], r["misses"], r["false_alarms"]) == (0, 3, 0) and r["median_rt_s"] is None


def test_gaze_labels():
    p, s = cs.Protocol(), cs.Screen()
    ep = cs.build_schedule(p, s)
    n_vol = int(cs.total_duration_s(p) / p.tr_s)
    lab = cs.gaze_trace(ep, n_vol, p)
    assert len(lab) == n_vol and len(lab[0]) == p.subtr_per_tr and len(lab[0][0]) == 2
    flat = [tuple(v) for row in lab for v in row]
    assert not any(math.isnan(v[0]) for v in flat), "no sample should fall outside the schedule"
    # 12 targets per ring plus the centre, and every sample sits at one. Rounding is needed because
    # a position reached clockwise and the same position reached anticlockwise differ in the last
    # bits (sin(-330 deg) vs sin(30 deg)), and because the centre can come out as -0.0.
    assert len({(round(x, 9) + 0.0, round(y, 9) + 0.0) for x, y in flat}) == 25
    assert all(any(abs(math.hypot(*v) - e) < 1e-9 for e in p.eccentricities_deg)
               or math.hypot(*v) == 0.0 for v in flat)
    # the 10 s lead-in fills the first five volumes with central fixation
    n_lead = int(p.lead_in_s / p.tr_s)
    assert all(math.hypot(x, y) == 0.0 for row in lab[:n_lead] for x, y in row)
    # epochs are one TR long, so every volume holds a single gaze position — no mixed labels
    assert all(len({(round(x, 9), round(y, 9)) for x, y in row}) == 1 for row in lab)
    # the first target volume is entirely at twelve o'clock on the outer ring, the next at centre
    first = [tuple(v) for v in lab[n_lead]]
    assert all(abs(x) < 1e-12 and abs(y - max(p.eccentricities_deg)) < 1e-12 for x, y in first)
    assert all(math.hypot(x, y) == 0.0 for x, y in lab[n_lead + 1])
    # both rings appear among the labels
    radii = {round(math.hypot(x, y), 6) for row in lab for x, y in row}
    assert radii == {0.0} | {round(e, 6) for e in p.eccentricities_deg}
    # labels follow a shifted trigger
    shifted = cs.gaze_trace(ep, n_vol, p, t_zero_s=-10.0)
    assert math.isnan(shifted[0][0][0]), "samples before the run start must be NaN"


def test_random_responder_is_mostly_false_alarms():
    p = cs.Protocol()
    ep = cs.build_schedule(p, cs.Screen())
    onsets = cs.add_flashes(ep, p)
    rng = random.Random(7)
    presses = sorted(rng.uniform(0, cs.total_duration_s(p)) for _ in range(p.n_flashes))
    r = cs.score_responses(onsets, presses)
    assert r["false_alarms"] >= r["hits"], "random pressing must not look like performance"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")
