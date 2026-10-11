"""
control - CANIS Pi-side safe-motion package (docs/SAFE_MOTION_CBF_SPEC.md)
===========================================================================
The host tier of the mobile upgrade: state estimator (spec §2), adaptive
comfort margin D_comfort(s) (§5), closed-form CBF-QP safety filter (§3-§4),
reward-on-voluntary-approach (§4), and the mode-gated controller that ties
them together (§6-§7). Wire formats: docs/INTERFACE_CONTRACT.md (LAW).

Entry point for the dashboard (in-process):

    from control import MotionController
    ctl = MotionController(sink=motion)     # dashboard's MotionCommand channel

Hardware-free verification:

    python3 control/motion_controller.py --selftest

With CANIS_MOBILE_ENABLED unset/0 (SAFE DEFAULT) the controller issues NO
drive commands - estimation, comfort adaptation, reward-on-approach, and
status() telemetry run; motion does not.
"""

from .motion_controller import MotionController, nominal_hold_and_face
from .estimator import Estimator, Estimate
from .comfort import ComfortMargin
from .reward import ApproachReward
from .cbf import filter_command

__all__ = ["MotionController", "nominal_hold_and_face", "Estimator",
           "Estimate", "ComfortMargin", "ApproachReward", "filter_command"]
