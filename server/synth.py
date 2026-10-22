"""
Pregnancy CGM trace generator for the IGT Coach mock-up.

Calibrated to PUBLISHED SUMMARY STATISTICS from GLAM (Glucose Levels Across
Maternity; 937 pregnant participants, Dexcom G6, enrolled <17wk, median 123
days each -- Diabetes Care 2024, PMC11097821).  Calibrating to published
statistics rather than to GLAM's records means no value rendered by this
mock-up is traceable to any human subject.

GLAM, uncomplicated pregnancies (n=413):
    mean glucose          98 +/- 7 mg/dL   (103 / 98 / 98 by trimester)
    median TIR 63-120     86%              (84% / 87% / 85% by trimester)
    median time >140      2.5%
    median time <63       1.8%
    postprandial peak     126 +/- 22 mg/dL
    mean meal excursion   36 +/- 22 mg/dL

The study population is NOT uncomplicated: it is intermediate glucose
intolerance (abnormal 1-hour GCT, normal 3-hour OGTT).  The 'igt' profile
sits between GLAM-uncomplicated and the GDM range, which is where this
group falls.

NOTHING HERE IS TRAINED ON ANYTHING.  It is a forward physiological
caricature used to populate a user interface.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

SAMPLE_MIN = 5                      # Dexcom G6/G7 cadence
SAMPLES_PER_DAY = 24 * 60 // SAMPLE_MIN

PROFILES = {
    "uncomplicated": dict(fasting=87.5, fasting_sd=7.0, gain=1.22, delay=52, noise=5.2),
    "igt":           dict(fasting=93.0, fasting_sd=8.0, gain=1.52, delay=64, noise=5.4),
    "gdm":           dict(fasting=99.0, fasting_sd=8.5, gain=1.70, delay=72, noise=5.0),
}


@dataclass
class Meal:
    """A meal as the app knows it: the three things the model acts on."""
    minute: int                 # minutes past local midnight
    label: str
    carb_g: float
    protein_g: float = 0.0
    fat_g: float = 0.0
    fiber_g: float = 0.0

    @property
    def net_carb(self) -> float:
        return max(0.0, self.carb_g - 0.5 * self.fiber_g)


# ----------------------------------------------------------- the meal model
#
# Three levers, exactly the ones the proposal names: which carbohydrate, in
# what combination, at what time of day.
#
#   amplitude = gain * f(net carb) * combination_damping * circadian(t)
#   shape     = a gamma kernel (rise, then clearance)
#
# Pregnancy-specific: insulin sensitivity is lowest in the morning and falls
# across gestation, so breakfast of identical composition peaks higher than
# the same food at dinner.  That is the single most useful thing this app
# can show somebody, and it is well described in normal pregnancy.

def circadian_sensitivity(minute: int) -> float:
    """Multiplier on meal response by time of day. Breakfast is worst."""
    h = (minute / 60.0) % 24
    return 1.0 + 0.30 * math.cos((h - 7.5) * math.pi / 12.0)


def gestational_drift(week: float) -> float:
    """Insulin sensitivity falls across gestation; ~25% more response by W36."""
    return 1.0 + 0.016 * max(0.0, week - 20.0)


def combination_damping(m: Meal) -> float:
    """Protein, fat and fiber alongside carbohydrate flatten the rise."""
    pr = m.protein_g / max(m.net_carb, 1.0)
    fr = m.fat_g / max(m.net_carb, 1.0)
    fb = m.fiber_g / max(m.carb_g, 1.0)
    damp = 1.0 / (1.0 + 0.55 * min(pr, 1.2) + 0.28 * min(fr, 1.2) + 0.9 * min(fb, 0.4))
    return float(np.clip(damp, 0.45, 1.0))


def peak_shift(m: Meal) -> float:
    """Fat and protein push the peak later, in minutes."""
    return 18.0 * min(m.fat_g / max(m.net_carb, 1.0), 1.2) + 8.0 * min(
        m.protein_g / max(m.net_carb, 1.0), 1.2)


def _gamma_kernel(n: int, peak_at: float, spread: float) -> np.ndarray:
    """Normalised rise-and-clear kernel peaking at `peak_at` samples."""
    t = np.arange(n, dtype=float)
    k = max(peak_at / spread, 1.05)
    y = (t ** (k - 1.0)) * np.exp(-t / spread)
    mx = y.max()
    return y / mx if mx > 0 else y


def meal_response(m: Meal, profile: str, week: float, n_samples: int = 72) -> np.ndarray:
    """Glucose excursion in mg/dL for one meal, sampled every SAMPLE_MIN."""
    p = PROFILES[profile]
    # Saturating dose-response: 80 g does not do twice what 40 g does.
    dose = 58.0 * (1.0 - math.exp(-m.net_carb / 42.0))
    amp = (p["gain"] * dose * combination_damping(m)
           * circadian_sensitivity(m.minute) * gestational_drift(week))
    peak_at = (p["delay"] + peak_shift(m)) / SAMPLE_MIN
    return amp * _gamma_kernel(n_samples, peak_at, max(peak_at / 1.9, 2.0))


def predict_curve(meal: Meal, profile: str, week: float, fasting: float,
                  horizon_min: int = 180) -> dict:
    """What the 'Plan a meal' screen draws. Never called a forecast in the UI."""
    n = horizon_min // SAMPLE_MIN
    exc = meal_response(meal, profile, week, n_samples=n)
    curve = fasting + exc
    return {
        "minutes": [i * SAMPLE_MIN for i in range(n)],
        "mgdl": [round(float(v), 1) for v in curve],
        "peak_mgdl": round(float(curve.max()), 1),
        "peak_min": int(float(np.argmax(curve)) * SAMPLE_MIN),
        "excursion_mgdl": round(float(exc.max()), 1),
        "net_carb_g": round(meal.net_carb, 1),
    }


DEFAULT_DAY = [
    Meal(7 * 60 + 15, "Breakfast", 52, protein_g=9, fat_g=8, fiber_g=3),
    Meal(10 * 60 + 30, "Snack", 22, protein_g=4, fat_g=5, fiber_g=2),
    Meal(12 * 60 + 45, "Lunch", 61, protein_g=24, fat_g=16, fiber_g=6),
    Meal(15 * 60 + 45, "Snack", 18, protein_g=6, fat_g=7, fiber_g=2),
    Meal(19 * 60 + 0, "Dinner", 68, protein_g=31, fat_g=19, fiber_g=8),
]


def generate_day(day_index: int, profile: str = "igt", week: float = 28.0,
                 meals=None, seed: int = 0, start: datetime = None) -> dict:
    """One day of 5-minute EGVs, in the shape the Dexcom /egvs endpoint uses."""
    rng = np.random.default_rng(seed * 1000 + day_index)
    p = PROFILES[profile]
    meals = meals if meals is not None else DEFAULT_DAY

    n = SAMPLES_PER_DAY
    t = np.arange(n)
    minutes = t * SAMPLE_MIN

    # Overnight baseline with a dawn rise, plus slow drift so days differ.
    fasting = p["fasting"] + rng.normal(0, p["fasting_sd"] * 0.45)
    dawn = 7.0 * np.exp(-((minutes - 300) ** 2) / (2 * 95.0 ** 2))
    drift = 4.6 * np.sin(2 * np.pi * (t / n) + rng.uniform(0, 6.28))
    nocturnal = -6.5 * np.exp(-((minutes - 180) ** 2) / (2 * 80.0 ** 2))
    base = fasting + dawn + drift + nocturnal

    trace = base.copy()
    jitter = rng.normal(0, 14, size=len(meals))
    for j, m in enumerate(meals):
        mm = Meal(int(m.minute + jitter[j]), m.label,
                  max(4.0, m.carb_g * rng.normal(1.0, 0.16)),
                  m.protein_g * rng.normal(1.0, 0.18),
                  m.fat_g * rng.normal(1.0, 0.2),
                  m.fiber_g * rng.normal(1.0, 0.2))
        resp = meal_response(mm, profile, week)
        i0 = int(mm.minute // SAMPLE_MIN)
        i1 = min(i0 + len(resp), n)
        if i1 > i0:
            trace[i0:i1] += resp[: i1 - i0]

    # Sensor noise, then a smoothing pass -- CGM is a filtered signal.
    trace = trace + rng.normal(0, p["noise"], n)
    kern = np.array([0.12, 0.22, 0.32, 0.22, 0.12])
    trace = np.convolve(trace, kern, mode="same")
    trace[:2] = trace[2]
    trace[-2:] = trace[-3]

    if start is None:
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                   microsecond=0)
    day_start = start + timedelta(days=day_index)

    # Realistic gaps: a warm-up / signal-loss window on about 1 day in 4.
    gap = set()
    if rng.random() < 0.25:
        g0 = int(rng.integers(0, n - 10))
        gap = set(range(g0, min(g0 + int(rng.integers(5, 10)), n)))

    records = []
    for i in range(n):
        if i in gap:
            continue
        ts = day_start + timedelta(minutes=int(minutes[i]))
        v = float(trace[i])
        status = None
        if v < 40:
            v, status = 39, "low"            # Dexcom sentinel
        elif v > 400:
            v, status = 401, "high"
        rate = float((trace[i] - trace[i - 1]) / SAMPLE_MIN) if i else 0.0
        records.append({
            "recordId": "synth-%d-%d" % (day_index, i),
            "systemTime": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "displayTime": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "value": int(round(v)),
            "status": status,
            "trend": _trend(rate),
            "trendRate": round(rate, 1),
            "unit": "mg/dL",
            "rateUnit": "mg/dL/min",
            "displayDevice": "iOS",
            "transmitterGeneration": "g7",
        })
    return {"recordType": "egv", "recordVersion": "3.0",
            "userId": "synthetic-" + profile, "records": records}


def _trend(rate: float) -> str:
    if rate >= 3:
        return "doubleUp"
    if rate >= 2:
        return "singleUp"
    if rate >= 1:
        return "fortyFiveUp"
    if rate > -1:
        return "flat"
    if rate > -2:
        return "fortyFiveDown"
    if rate > -3:
        return "singleDown"
    return "doubleDown"


def generate_series(days: int = 21, profile: str = "igt", week: float = 28.0,
                    seed: int = 7) -> list:
    """A run-in week plus active weeks, as one flat EGV list."""
    start = (datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                microsecond=0)
             - timedelta(days=days - 1))
    out = []
    for d in range(days):
        out.extend(generate_day(d, profile, week + d / 7.0, seed=seed,
                                start=start)["records"])
    return out


def metrics(records: list) -> dict:
    """The conventional CGM metric set, plus the pregnancy windows.

    NOTE: %TIR is reported against 63-140 and 63-120 as DESCRIPTIVE windows.
    They are NOT targets for this population -- no validated CGM target
    exists for GDM, let alone for intermediate glucose intolerance
    (Szmuilowicz 2024, PMID 38606830).  The UI must never show them as
    pass/fail.
    """
    v = np.array([r["value"] for r in records if r["value"] is not None], float)
    if v.size == 0:
        return {}
    return {
        "n": int(v.size),
        "mean": round(float(v.mean()), 1),
        "sd": round(float(v.std(ddof=1)), 1),
        "cv": round(float(v.std(ddof=1) / v.mean() * 100), 1),
        "tir_63_140": round(float(((v >= 63) & (v <= 140)).mean() * 100), 1),
        "tir_63_120": round(float(((v >= 63) & (v <= 120)).mean() * 100), 1),
        "tar_140": round(float((v > 140).mean() * 100), 1),
        "tbr_63": round(float((v < 63).mean() * 100), 1),
        "gmi": round(3.31 + 0.02392 * float(v.mean()), 2),
    }


def personal_baseline(records: list) -> dict:
    """Her own baseline, from the blinded run-in week.

    This is what the app compares against -- never a population threshold.
    'Her own threshold' is the 90th centile of her run-in values, which is
    investigator-defined, and the UI says so.
    """
    v = np.array([r["value"] for r in records if r["value"] is not None], float)
    if v.size == 0:
        return {}
    return {
        "fasting_median": round(float(np.percentile(v, 10)), 1),
        "median": round(float(np.percentile(v, 50)), 1),
        "p25": round(float(np.percentile(v, 25)), 1),
        "p75": round(float(np.percentile(v, 75)), 1),
        "p90": round(float(np.percentile(v, 90)), 1),
        "own_threshold": round(float(np.percentile(v, 90)), 1),
        "run_in_days": round(v.size / SAMPLES_PER_DAY, 1),
    }
