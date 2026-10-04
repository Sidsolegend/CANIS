"""
event_log.py — timestamped JSONL event logging for the CANIS longitudinal study
===============================================================================
The dashboard previously showed ONLY the latest snapshot: the moment the page
refreshed, the behavioral record was gone. A longitudinal study (see
optimized/study/STUDY_PROTOCOL.md) needs a durable, append-only record of what
actually happened. This module provides it, plus the session-level aggregation
used both by the dashboard's CSV export and by the offline analyzer
(optimized/study/analyze_sessions.py).

EVENT SCHEMA (one JSON object per line, append-only):
  Common fields on every event:
    ts          float   Unix epoch seconds (Pi wall clock — the ESP32 has none)
    iso         str     human-readable UTC ISO-8601 of ts
    session_id  str     groups events into one dashboard run / study session
    type        str     event type (below)

  Event types and their extra fields:
    session_start     {}                                  dashboard process began
    state_transition  {from, to, arousal}                 ESP32 state changed
    treat_dispensed   {reward_count}                      reward counter ticked up
    dispenser_jam     {jam_count}                         jam counter ticked up
    zone_seen         {zone}                              vision POSTed a sighting
                                                          (~1 Hz while the dog is
                                                          visible; episodes are
                                                          reconstructed by gap)
    bark              {duration_s, peak_db}               audio detector heard a bark
    bark_quiet        {barks, accepted}                   quiet after a bark episode (accepted=treat signalled)
    mic_status        {ok}                                bark microphone lost / back
    exposure_change   {from, to}                          EXPOSURE_LEVEL changed
    firmware_reboot   {uptime_sec}                        uptime went backwards
    link_gap          {gap_sec}                           ESP32 silent > STALE_AFTER,
                                                          then came back

The file is opened/closed per write: crash-safe (every accepted event is on
disk) and trivially tail -f-able. Line rate is low (~a few per minute in a
live session), so durability beats throughput here.
"""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone


class EventLog:
    """Append-only JSONL writer, safe under a threaded WSGI server."""

    def __init__(self, path, session_id=None):
        self.path = path
        self.session_id = session_id or (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6])
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def log(self, event_type, **fields):
        """Write one event. Returns the event dict (handy for tests)."""
        now = time.time()
        evt = {
            "ts": round(now, 3),
            "iso": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            "type": event_type,
        }
        evt.update(fields)
        line = json.dumps(evt, separators=(",", ":"))
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return evt


def read_events(path):
    """Read a JSONL event file -> list of dicts. Skips blank/corrupt lines
    (a power cut can truncate the last line; never lose the whole file to it)."""
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


# ---------------------------------------------------------------------------
# Session aggregation — shared by /export/sessions.csv and analyze_sessions.py
# ---------------------------------------------------------------------------
STATES = ("CALM", "AROUSAL", "CORRECTION")


def summarize_sessions(events, zone_gap_s=3.0):
    """Aggregate a flat event list into per-session metrics.

    zone_gap_s: two zone_seen events further apart than this belong to
    different zone EPISODES (matches the dashboard's COMMAND_TTL: the
    detection would have expired between them).

    Returns a list of dicts, one per session_id, ordered by first event time.
    All durations are derived from event timestamps only — no fabrication: a
    metric that cannot be computed from the log is reported as None/0.
    """
    by_session = {}
    for evt in events:
        by_session.setdefault(evt.get("session_id", "?"), []).append(evt)

    summaries = []
    for sid, evts in by_session.items():
        evts.sort(key=lambda e: e.get("ts", 0.0))
        first_ts, last_ts = evts[0]["ts"], evts[-1]["ts"]

        s = {
            "session_id": sid,
            "start_ts": first_ts,          # numeric sort key (iso can be blank)
            "start_iso": evts[0].get("iso", ""),
            "duration_s": round(last_ts - first_ts, 1),
            "transitions": 0,
            "corrections_entered": 0,
            "treats_dispensed": 0,
            "jams": 0,
            "zone_episodes": 0,
            "zone_time_s": 0.0,        # approx: summed spans of zone_seen episodes
            "first_zone_latency_s": None,   # session start -> first zone sighting
            "time_calm_s": 0.0,
            "time_arousal_s": 0.0,
            "time_correction_s": 0.0,
            "link_gaps": 0,
            "reboots": 0,
            "exposure_levels": set(),
        }

        # -- state occupancy: integrate between state_transition events.
        # The firmware boots in CALM; until the first transition we attribute
        # time to the transition's "from" state when known, else CALM.
        cur_state = None
        cur_since = first_ts
        for e in evts:
            t = e["type"]
            if t == "state_transition":
                s["transitions"] += 1
                frm, to = e.get("from"), e.get("to")
                if cur_state is None:
                    cur_state = frm if frm in STATES else "CALM"
                _add_state_time(s, cur_state, e["ts"] - cur_since)
                cur_state, cur_since = to, e["ts"]
                if to == "CORRECTION":
                    s["corrections_entered"] += 1
            elif t == "treat_dispensed":
                s["treats_dispensed"] += 1
            elif t == "dispenser_jam":
                s["jams"] += 1
            elif t == "link_gap":
                s["link_gaps"] += 1
            elif t == "firmware_reboot":
                s["reboots"] += 1
            elif t == "exposure_change":
                if "to" in e:
                    s["exposure_levels"].add(e["to"])
        if cur_state in STATES:
            _add_state_time(s, cur_state, last_ts - cur_since)

        # -- zone episodes: cluster zone_seen by gap.
        zone_ts = [e["ts"] for e in evts if e["type"] == "zone_seen"]
        if zone_ts:
            s["first_zone_latency_s"] = round(zone_ts[0] - first_ts, 1)
            episodes = 1
            ep_start = prev = zone_ts[0]
            zone_time = 0.0
            for t in zone_ts[1:]:
                if t - prev > zone_gap_s:
                    zone_time += prev - ep_start
                    episodes += 1
                    ep_start = t
                prev = t
            zone_time += prev - ep_start
            s["zone_episodes"] = episodes
            s["zone_time_s"] = round(zone_time, 1)

        s["exposure_levels"] = ",".join(str(x) for x in sorted(s["exposure_levels"]))
        for k in ("time_calm_s", "time_arousal_s", "time_correction_s"):
            s[k] = round(s[k], 1)
        summaries.append(s)

    summaries.sort(key=lambda x: x["start_ts"])
    return summaries


def _add_state_time(summary, state, seconds):
    if seconds <= 0:
        return
    key = {"CALM": "time_calm_s", "AROUSAL": "time_arousal_s",
           "CORRECTION": "time_correction_s"}.get(state)
    if key:
        summary[key] += seconds


# CSV column orders (stable schema for spreadsheets / plotting).
SESSION_CSV_FIELDS = [
    "session_id", "start_iso", "duration_s",
    "transitions", "corrections_entered", "treats_dispensed", "jams",
    "zone_episodes", "zone_time_s", "first_zone_latency_s",
    "time_calm_s", "time_arousal_s", "time_correction_s",
    "link_gaps", "reboots", "exposure_levels",
]

EVENT_CSV_FIELDS = [
    "ts", "iso", "session_id", "type",
    "from", "to", "arousal", "reward_count", "jam_count",
    "zone", "gap_sec", "uptime_sec",
]
