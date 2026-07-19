"""
control/cbf.py — the CBF safety filter as a CLOSED-FORM 2-input QP projection
==============================================================================
Implements docs/SAFE_MOTION_CBF_SPEC.md §3-§4 exactly.

Safety set (comfort set):     h(x) = d_rd - D_comfort(s)  >=  0
Barrier condition:            hdot(x, u) >= -gamma * h(x)         (alpha linear)

The robot's contribution to hdot for a unicycle with the dog at bearing beta
(contract sign: 0 = dead ahead, negative = LEFT, positive = RIGHT):

    ddot_robot = -v * cos(beta)          (omega does not change d_rd:
    hdot_robot = -v * cos(beta)           rotating in place is not approaching)

so the CBF constraint on u = [v, omega] is ONE linear inequality that touches
only v:

    v * cos(beta) <= gamma * h                       (a.u <= b, a = [cos(beta), 0])

The QP   u* = argmin ||u - u_nom||^2  s.t.  a.u <= b,  u in box   therefore has
a closed form: project v onto the halfspace (omega passes through untouched —
"facing != approaching", spec §4), then clamp to the actuator box.

Certificate semantics (spec §3): hdot also depends on the DOG's motion, which
no robot command controls. The certificate is over the ROBOT'S contribution:
the filtered command can never make d_rd decrease when h <= 0. Any d_rd
decrease at/below the margin is then attributable ONLY to the dog choosing to
approach — exactly the event the reward module pays (spec §4). This is the
continuous generalization of the [OPT-8] never-approach invariant (OPT-8 is
the degenerate case D_comfort = infinity below exposure 2).

Fail-safe evaluation (spec §6): if the distance is UNKNOWN (dog untracked,
range never established, sensors dropped) the caller passes
distance_known=False and this filter treats the state as AT THE BOUNDARY with
the dog dead ahead (h = 0, cos(beta) = 1) — the worst case — which forbids any
v > 0. Missing data is never "safe to advance".
"""

import math

try:                       # package import (dashboard: from control import cbf)
    from . import config as C
except ImportError:        # direct script run from control/
    import config as C


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def filter_command(v_nom, w_nom, *, h_m, beta_deg, distance_known=True,
                   gamma=None, v_max=None, w_max=None):
    """Closed-form CBF-QP projection of the nominal command u_nom = (v, omega).

    Returns (v, omega, info) where info = {"h", "active", "saturated"}:
      active    — the barrier constraint modified the nominal command
      saturated — the projected v exceeded the actuator box and was clamped to
                  the box edge. This only happens when the barrier demands a
                  retreat FASTER than v_max; the clamped command is then the
                  fastest distance-INCREASING command physically available, so
                  the "never distance-reducing at h <= 0" invariant still holds
                  (proof: bound <= 0 => projected v <= 0 => box-clamped v <= 0
                  => -v*cos(beta) >= 0 for cos(beta) > 0, symmetric for < 0).
    """
    gamma = C.CBF_GAMMA if gamma is None else float(gamma)
    v_max = C.MAX_LINEAR_MPS if v_max is None else float(v_max)
    w_max = C.MAX_ANGULAR_RPS if w_max is None else float(w_max)

    # Actuator box first (a nominal policy may propose beyond the caps; the
    # firmware clamps AGAIN after us — defense in depth, contract §2.2).
    v = clamp(float(v_nom), -v_max, v_max)
    w = clamp(float(w_nom), -w_max, w_max)

    if distance_known:
        h = float(h_m)
        cosb = math.cos(math.radians(float(beta_deg)))
    else:
        # Fail-safe: at the boundary, dog dead ahead (worst case) => v <= 0.
        h, cosb = 0.0, 1.0

    b = gamma * h
    active = saturated = False

    if abs(cosb) <= C.COS_EPS:
        # Dog exactly abeam: no v changes d_rd this instant, so the constraint
        # degenerates to 0 <= b. If violated (h < 0 while the dog is beside
        # us), no linear velocity can help or harm instantaneously — hold
        # v = 0 (never guess a direction); omega may still turn so a later
        # tick can retreat cleanly.
        if b < 0.0:
            v, active = 0.0, True
    elif v * cosb > b:
        # Halfspace projection. a = [cos(beta), 0] => only v moves; the
        # projection lands exactly ON the constraint boundary:
        #     v* = b / cos(beta)
        # (works for both signs of cos(beta): dog ahead => an upper bound on
        # v; dog behind => a LOWER bound — backing into a too-close dog is
        # rewritten into driving forward, away).
        active = True
        v = b / cosb
        if v < -v_max:
            v, saturated = -v_max, True
        elif v > v_max:
            v, saturated = v_max, True

    return v, w, {"h": h, "active": active, "saturated": saturated}
