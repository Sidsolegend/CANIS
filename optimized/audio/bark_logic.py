"""
bark_logic.py — pure bark / "he went quiet" decision logic (no audio I/O, no network)
===================================================================================
Fed one loudness value per 50 ms frame (dBFS), it decides:

  * when a BARK happened  — a burst clearly louder than the room's noise floor that lasts
    between MIN_BARK_S and MAX_BARK_S (a click is too short, a TV/vacuum/howl too long);
  * when he has gone QUIET — QUIET_S seconds with nothing loud since a bark episode began.
    This fires ONCE per episode, and only if there was at least one bark.

Design points that matter for the dog:
  * Anything loud, even if it is not bark-shaped (a long howl, a loud TV), resets the quiet clock, so a
    treat is never dropped while there is still a racket.
  * The caller MUST call reset_episode() whenever the microphone is lost. Silence from a dead mic is not
    a quiet dog, and must never produce a "quiet" event.
  * The default rise (35 dB over the floor) was set from ONE recording made close to the mic, where
    barks peaked ~55 dB over the floor and talking/footsteps ~25 dB. It is a starting point, not a
    tuned value: re-check it against more recordings.

Pure Python so tests/test_bark.py can exercise it without hardware.
"""

import statistics

FRAME_S = 0.05                 # one loudness value per 50 ms
DB_MIN = -90.0                 # digital silence is clamped here so it cannot drag the floor to -inf

# Defaults (mirrored for the Python side in dashboard/canis_config.py: BARK_QUIET_S)
RISE_DB = 35.0                 # a frame this far above the noise floor starts a burst
HYSTERESIS_DB = 6.0            # once started, a burst lasts while it stays within this much of the start level
MIN_BARK_S = 0.15
MAX_BARK_S = 2.5
MERGE_GAP_S = 0.3              # dips shorter than this stay inside one burst
QUIET_S = 5.0                  # silence needed after a bark before the treat
FLOOR_INIT_FRAMES = 20         # 1 s of listening before any decision


class BarkDetector:
    def __init__(self, rise_db=RISE_DB, hysteresis_db=HYSTERESIS_DB, min_bark_s=MIN_BARK_S,
                 max_bark_s=MAX_BARK_S, merge_gap_s=MERGE_GAP_S, quiet_s=QUIET_S):
        self.rise_db = rise_db
        self.hyst_db = hysteresis_db
        self.min_bark_s = min_bark_s
        self.max_bark_s = max_bark_s
        self.merge_gap_s = merge_gap_s
        self.quiet_s = quiet_s
        self.floor = None
        self._init = []
        self.in_burst = False
        self._burst_start = 0.0
        self._last_above = 0.0
        self._peak = DB_MIN
        self.last_loud = None          # last time anything was loud (bark-shaped or not)
        self.episode_barks = 0

    # -- public ------------------------------------------------------------
    def reset_episode(self):
        """Forget a pending bark episode (call when the mic is lost). No 'quiet' will follow."""
        self.episode_barks = 0
        self.in_burst = False

    def feed(self, db, t):
        """db: this frame's level in dBFS; t: seconds (any monotonic clock).
        Returns a list of events: ('bark', t, duration_s, peak_db) and/or ('quiet', t, n_barks)."""
        db = max(db, DB_MIN)
        events = []

        if self.floor is None:                         # listen first, decide later
            self._init.append(db)
            if len(self._init) >= FLOOR_INIT_FRAMES:
                self.floor = statistics.median(self._init)
            return events

        start_thr = self.floor + self.rise_db
        keep_thr = start_thr - self.hyst_db

        if not self.in_burst:
            if db > start_thr:
                self.in_burst = True
                self._burst_start = t
                self._last_above = t
                self._peak = db
                self.last_loud = t
        else:
            if db > keep_thr:
                self._last_above = t
                self._peak = max(self._peak, db)
                self.last_loud = t
            elif t - self._last_above >= self.merge_gap_s:
                dur = self._last_above - self._burst_start + FRAME_S
                self.in_burst = False
                if self.min_bark_s <= dur <= self.max_bark_s:
                    self.episode_barks += 1
                    events.append(("bark", self._last_above, dur, self._peak))
                # else: a click or a long noise — loud (the quiet clock already restarted) but not a bark

        # the noise floor follows the room, but never while something is loud
        if not self.in_burst and (self.last_loud is None or t - self.last_loud > 1.0):
            k = 0.3 if db < self.floor else 0.01       # falls fast, rises slowly
            self.floor += k * (db - self.floor)

        # he has gone quiet: a bark episode, nothing loud for quiet_s
        if (self.episode_barks > 0 and not self.in_burst and self.last_loud is not None
                and t - self.last_loud >= self.quiet_s):
            events.append(("quiet", t, self.episode_barks))
            self.episode_barks = 0
        return events
