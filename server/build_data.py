"""
Build web/data.json -- the frozen dataset the page ships with.

The page is self-sufficient: it renders entirely from this file, with no
server. The FastAPI server is only needed to demonstrate the live Dexcom
sandbox OAuth flow. That means the same app.html can be published as a
shareable Artifact for the IRB packet and run locally against the sandbox.

Run:  python server/build_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import synth
import swaps

OUT = HERE.parent / "web" / "data.json"

RUN_IN_DAYS = 7
RECENT_DAYS = 7
WEEK = 28.3

PARTICIPANT = {
    "id": "P-014",
    "initials": "A.R.",
    "week": WEEK,
    "week_label": "W28+2",
    "enrolled_week": "W27+1",
    "gct_1hr": 148,
    "gct_threshold": 140,
    "ogtt": [
        {"t": "Fasting", "value": 88, "cc": 95},
        {"t": "1 hour", "value": 171, "cc": 180},
        {"t": "2 hour", "value": 142, "cc": 155},
        {"t": "3 hour", "value": 118, "cc": 140},
    ],
    "ogtt_abnormal_count": 0,
    "arm": "model",
    "profile": "igt",
}


def time_of_day_band(records):
    """Percentile curve by time of day across the run-in week.

    This is HER band, from her own blinded week. It is not a population
    reference range and the UI never calls it one.
    """
    buckets = {}
    for r in records:
        hhmm = r["displayTime"][11:16]
        buckets.setdefault(hhmm, []).append(r["value"])
    out = []
    for hhmm in sorted(buckets):
        v = np.array(buckets[hhmm], float)
        out.append({
            "t": hhmm,
            "p10": round(float(np.percentile(v, 10)), 1),
            "p25": round(float(np.percentile(v, 25)), 1),
            "p50": round(float(np.percentile(v, 50)), 1),
            "p75": round(float(np.percentile(v, 75)), 1),
            "p90": round(float(np.percentile(v, 90)), 1),
        })
    return out


def slim(records):
    """Only the fields the page draws, to keep the payload small."""
    return [{"t": r["displayTime"], "v": r["value"],
             "tr": r["trend"], "st": r["status"]} for r in records]


def daily_summaries(records):
    by_day = {}
    for r in records:
        by_day.setdefault(r["displayTime"][:10], []).append(r)
    out = []
    for d in sorted(by_day):
        m = synth.metrics(by_day[d])
        m["date"] = d
        out.append(m)
    return out


def main():
    total_days = RUN_IN_DAYS + RECENT_DAYS
    all_recs = synth.generate_series(days=total_days, profile=PARTICIPANT["profile"],
                                     week=WEEK - total_days / 7.0, seed=11)
    cut = RUN_IN_DAYS * synth.SAMPLES_PER_DAY
    run_in, active = all_recs[:cut], all_recs[cut:]

    baseline = synth.personal_baseline(run_in)
    today = [r for r in active if r["displayTime"][:10] == active[-1]["displayTime"][:10]]

    # Comparison scenario for the "how close were we" screen: what the model
    # said about this morning's breakfast, and what actually happened.
    bm, projected, sugg, day = swaps.propose(
        [("bagel_plain", 1), ("juice_orange", 1)], 7 * 60 + 30,
        PARTICIPANT["profile"], WEEK, baseline["fasting_median"],
        baseline["own_threshold"], carbs_so_far_g=0.0,
        carbs_planned_rest_of_day_g=135.0)

    data = {
        "generated": "frozen synthetic dataset",
        "provenance": (
            "Synthetic throughout. Traces come from a physiological model "
            "calibrated to published GLAM summary statistics (937 pregnancies, "
            "Dexcom G6, Diabetes Care 2024): mean glucose 98 +/- 7 mg/dL, "
            "median time 63-120 mg/dL 86%, postprandial peak 126 +/- 22 mg/dL. "
            "Calibrating to published statistics rather than to GLAM's records "
            "means no value shown here is traceable to any human subject."),
        "participant": PARTICIPANT,
        "baseline": baseline,
        "band": time_of_day_band(run_in),
        "today": slim(today),
        "recent": slim(active),
        "daily": daily_summaries(active),
        "run_in_metrics": synth.metrics(run_in),
        "active_metrics": synth.metrics(active),
        "run_in_days": RUN_IN_DAYS,
        "foods": [dict(f, id=fid, blocked_reason=swaps.is_blocked(fid))
                  for fid, f in swaps.foods().items()],
        "additions": swaps.rules()["additions"],
        "swaps": swaps.rules()["swaps"],
        "coaching": swaps.rules()["coaching"],
        "blocklist": swaps.blocklist(),
        "constraints": {
            "carb_floor_g": swaps.CARB_FLOOR_G,
            "carb_caution_g": swaps.CARB_CAUTION_G,
            "max_suggestions": swaps.MAX_SUGGESTIONS,
            "caffeine_ceiling_mg": swaps.CAFFEINE_CEILING_MG,
        },
        "profiles": synth.PROFILES,
        "example_card": swaps.summary_card(
            PARTICIPANT["id"], bm, projected, sugg,
            baseline["own_threshold"], WEEK, day),
        "sandbox_users": [
            {"label": "Sandbox User - G6", "app": "G6 Mobile App", "logs_carbs": True,
             "note": "Records carbs, exercise, health and insulin."},
            {"label": "Sandbox User - G7", "app": "G7 Mobile App", "logs_carbs": False,
             "note": "Records bloodGlucose and insulin only. No carbs."},
            {"label": "Sandbox User - D1", "app": "Dexcom ONE", "logs_carbs": False,
             "note": "Does not support /events; returns an empty array."},
            {"label": "Sandbox User - G715DAY", "app": "G7 15-day", "logs_carbs": False,
             "note": "Same event limitation as G7."},
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print("wrote %s  (%.0f KB)" % (OUT, kb))
    print("  baseline:", baseline)
    print("  today points:", len(data["today"]), " recent points:", len(data["recent"]))
    print("  foods:", len(data["foods"]), " band points:", len(data["band"]))
    print("  active metrics:", data["active_metrics"])


if __name__ == "__main__":
    main()
