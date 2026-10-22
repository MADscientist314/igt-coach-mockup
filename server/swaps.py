"""
Smart-swap engine with hard nutritional guardrails.

Design principle, and the reason the levers are ordered the way they are:

    Prefer levers that do not remove food.

Noori et al. (Diabet Med 2026, PMID 41995724) found GDM care protocols
"may exacerbate or precipitate" disordered eating "due to their focus on
dietary surveillance, glucose control and fear-based messaging".  This
population has not even been told they have a condition, so the risk of
iatrogenic restriction is higher, not lower.  The engine therefore tries,
in order:

    1. SEQUENCE  -- eat the protein and vegetables first. Same food.
    2. TIMING    -- move it later in the day. Same food.
    3. ADD       -- put something alongside it. More food.
    4. SWAP      -- change one component. Only if 1-3 are insufficient.

Hard constraint, checked on every suggestion and asserted in tests:
carbohydrate intake must not fall below 175 g/day (IOM), and there is RCT
evidence of micronutrient deficiency at <=165 g/day in GDM (Sweeting et al.,
Nutrients 2021, PMID 34444759).  The engine has a FLOOR, not a ceiling.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

import yaml
from pathlib import Path

from synth import Meal, predict_curve, circadian_sensitivity

CONTENT = Path(__file__).parent / "content"

# ------------------------------------------------------------- guardrails

CARB_FLOOR_G = 175.0          # IOM minimum in pregnancy. Hard refusal below.
CARB_CAUTION_G = 195.0        # Warn here; the day still has room to go wrong.
MAX_SUGGESTIONS = 3           # More than three is noise and reads as nagging.
CAFFEINE_CEILING_MG = 200.0


class GuardrailViolation(Exception):
    """Raised when a suggestion would breach a hard nutritional constraint."""


def _load(name: str):
    with open(CONTENT / name, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_FOODS = None
_RULES = None
_BLOCK = None


def foods():
    global _FOODS
    if _FOODS is None:
        _FOODS = {f["id"]: f for f in _load("foods.yaml")["foods"]}
    return _FOODS


def rules():
    global _RULES
    if _RULES is None:
        _RULES = _load("swaps.yaml")
    return _RULES


def blocklist():
    global _BLOCK
    if _BLOCK is None:
        _BLOCK = _load("blocklist.yaml")
    return _BLOCK


def is_blocked(food_id: str):
    """Pregnancy food-safety screen. Returns a reason string, or None."""
    f = foods().get(food_id)
    if not f:
        return None
    bl = blocklist()
    for rule in bl["categories"]:
        if rule["key"] in f.get("safety_flags", []):
            return rule["reason"]
    return None


# ----------------------------------------------------------- the levers

@dataclass
class Suggestion:
    kind: str                 # sequence | timing | add | swap
    title: str
    detail: str
    carb_delta_g: float       # change in the day's carbohydrate. >= 0 preferred.
    projected_peak: float
    projected_excursion: float
    baseline_peak: float
    baseline_excursion: float
    rationale: str            # the feature that drove it, for the RD card

    @property
    def peak_reduction(self):
        return round(self.baseline_peak - self.projected_peak, 1)

    def as_dict(self):
        d = self.__dict__.copy()
        d["peak_reduction"] = self.peak_reduction
        return d


def _meal_from_items(items):
    """Sum a list of (food_id, servings) into one Meal."""
    carb = prot = fat = fib = 0.0
    for fid, n in items:
        f = foods()[fid]
        carb += f["carb_g"] * n
        prot += f["protein_g"] * n
        fat += f["fat_g"] * n
        fib += f["fiber_g"] * n
    return carb, prot, fat, fib


def evaluate(items, minute, profile, week, fasting, label="Meal"):
    """Predicted response for a set of items at a time of day."""
    carb, prot, fat, fib = _meal_from_items(items)
    m = Meal(minute, label, carb, prot, fat, fib)
    return m, predict_curve(m, profile, week, fasting)


def propose(items, minute, profile, week, fasting, own_threshold,
            carbs_so_far_g=0.0, carbs_planned_rest_of_day_g=0.0, slot=None):
    """Return up to MAX_SUGGESTIONS, ordered least-restrictive first.

    `own_threshold` is HER OWN value from the blinded run-in week, not a
    population target. No population target exists for this group.

    `slot` is breakfast | lunch | dinner | snack. It keeps suggestions
    meal-appropriate: nobody wants salmon with their breakfast bagel.
    """
    slot = slot or _slot_for(minute)
    base_meal, base = evaluate(items, minute, profile, week, fasting)
    day_carb = carbs_so_far_g + base_meal.carb_g + carbs_planned_rest_of_day_g
    out = []

    # ---- 1. SEQUENCE: protein and vegetables first. No food changes. -----
    lead = _lead_item(items)
    if lead:
        # Eating protein/veg first blunts and delays; modelled as added damping.
        m2 = Meal(minute, "sequenced", base_meal.carb_g,
                  base_meal.protein_g * 1.9, base_meal.fat_g * 1.25,
                  base_meal.fiber_g * 1.4)
        seq = predict_curve(m2, profile, week, fasting)
        out.append(Suggestion(
            kind="sequence",
            title="Start with the %s" % lead.lower(),
            detail="Same meal, same amount, different order. The %s first, "
                   "then the rest." % lead.lower(),
            carb_delta_g=0.0,
            projected_peak=seq["peak_mgdl"], projected_excursion=seq["excursion_mgdl"],
            baseline_peak=base["peak_mgdl"], baseline_excursion=base["excursion_mgdl"],
            rationale="Order of eating; carbohydrate unchanged at %.0f g."
                      % base_meal.carb_g))

    # ---- 2. TIMING: the circadian lever. No food changes. ----------------
    # Deliberately NOT offered for breakfast. "Move your breakfast to
    # lunchtime" is "skip breakfast", which is the restrictive pattern this
    # engine exists to avoid.
    if slot in ("snack", "lunch"):
        later = max(minute, 13 * 60)
        if circadian_sensitivity(minute) > circadian_sensitivity(later) * 1.06:
            _, alt = evaluate(items, later, profile, week, fasting)
            out.append(Suggestion(
                kind="timing",
                title="This lands softer later in the day",
                detail="Insulin sensitivity is lowest in the morning in "
                       "pregnancy. The same thing at %s instead of %s."
                       % (_hhmm(later), _hhmm(minute)),
                carb_delta_g=0.0,
                projected_peak=alt["peak_mgdl"], projected_excursion=alt["excursion_mgdl"],
                baseline_peak=base["peak_mgdl"], baseline_excursion=base["excursion_mgdl"],
                rationale="Time of day; %.0f%% more response at %s than at %s."
                          % ((circadian_sensitivity(minute) / circadian_sensitivity(later) - 1) * 100,
                             _hhmm(minute), _hhmm(later))))

    # ---- 3. ADD: put something alongside. More food, not less. -----------
    for add in rules()["additions"]:
        fid = add["food"]
        if is_blocked(fid):
            continue
        if slot not in add.get("slots", ["breakfast", "lunch", "dinner", "snack"]):
            continue
        cand = list(items) + [(fid, add.get("servings", 1))]
        m3, alt = evaluate(cand, minute, profile, week, fasting)
        if alt["peak_mgdl"] < base["peak_mgdl"] - 3:
            out.append(Suggestion(
                kind="add",
                title=add["title"],
                detail=add["detail"],
                carb_delta_g=round(m3.carb_g - base_meal.carb_g, 1),
                projected_peak=alt["peak_mgdl"], projected_excursion=alt["excursion_mgdl"],
                baseline_peak=base["peak_mgdl"], baseline_excursion=base["excursion_mgdl"],
                rationale="Carbohydrate-to-protein ratio falls from %.1f to %.1f."
                          % (base_meal.carb_g / max(base_meal.protein_g, 1),
                             m3.carb_g / max(m3.protein_g, 1))))

    # ---- 4. SWAP: change one component. In-category only. ----------------
    for i, (fid, n) in enumerate(items):
        for alt_id in rules()["swaps"].get(fid, []):
            if is_blocked(alt_id):
                continue
            a, b = foods()[fid], foods().get(alt_id)
            if not b or a["category"] != b["category"]:
                continue                      # in-category only; keep it recognisable
            cand = list(items)
            cand[i] = (alt_id, n)
            m4, alt = evaluate(cand, minute, profile, week, fasting)
            delta = round(m4.carb_g - base_meal.carb_g, 1)
            if delta < 0 and day_carb + delta < CARB_FLOOR_G:
                continue                      # hard floor: refuse, silently
            if alt["peak_mgdl"] < base["peak_mgdl"] - 4:
                out.append(Suggestion(
                    kind="swap",
                    title="%s instead of %s" % (b["name"], a["name"]),
                    detail=b.get("note", "Same kind of food, steadier release."),
                    carb_delta_g=delta,
                    projected_peak=alt["peak_mgdl"],
                    projected_excursion=alt["excursion_mgdl"],
                    baseline_peak=base["peak_mgdl"],
                    baseline_excursion=base["excursion_mgdl"],
                    rationale="Same food category (%s); fiber %+.0f g, "
                              "net carbohydrate %+.0f g."
                              % (a["category"], b["fiber_g"] - a["fiber_g"], delta)))

    # On a day already near the floor, only suggestions that ADD food survive.
    if day_carb < CARB_CAUTION_G:
        out = [s for s in out if s.carb_delta_g >= 0]

    order = {"sequence": 0, "timing": 1, "add": 2, "swap": 3}
    out.sort(key=lambda s: (order[s.kind], -s.peak_reduction))

    # Cap each lever at two, so a pile of additions cannot crowd out the one
    # swap that would actually help. De-duplicate by title: a meal containing
    # the same food twice would otherwise produce the same swap twice.
    chosen, seen, titles = [], {}, set()
    for s in out:
        if s.title in titles:
            continue
        if seen.get(s.kind, 0) >= 2:
            continue
        titles.add(s.title)
        seen[s.kind] = seen.get(s.kind, 0) + 1
        chosen.append(s)
        if len(chosen) == MAX_SUGGESTIONS:
            break

    check_day(day_carb, chosen)
    return base_meal, base, chosen, _day_status(day_carb)


def check_day(day_carb_g, suggestions):
    """Assert the hard floor. Raises rather than quietly shipping a breach.

    Only a suggestion that REMOVES carbohydrate can breach the floor. If the
    day is already short, adding food is the correct answer, not a violation.
    """
    for s in suggestions:
        if s.carb_delta_g < 0 and day_carb_g + s.carb_delta_g < CARB_FLOOR_G:
            raise GuardrailViolation(
                "Suggestion %r would put the day at %.0f g carbohydrate, below "
                "the %.0f g floor." % (s.title, day_carb_g + s.carb_delta_g,
                                       CARB_FLOOR_G))
    return True


def _day_status(day_carb_g):
    if day_carb_g < CARB_FLOOR_G:
        return {"level": "floor", "carb_g": round(day_carb_g),
                "message": "Today is below the %.0f g of carbohydrate pregnancy "
                           "needs. No reductions will be suggested."
                           % CARB_FLOOR_G}
    if day_carb_g < CARB_CAUTION_G:
        return {"level": "caution", "carb_g": round(day_carb_g),
                "message": "Close to the %.0f g daily minimum. Suggestions are "
                           "limited to ones that add food." % CARB_FLOOR_G}
    return {"level": "ok", "carb_g": round(day_carb_g),
            "message": "Carbohydrate for the day is in a healthy range for pregnancy."}


def _lead_item(items):
    """The thing to eat first: a vegetable or a protein, never the starch."""
    best, best_score = None, 0
    for fid, _ in items:
        f = foods()[fid]
        if f.get("category") not in ("veg_add", "protein_add", "dairy", "fat_add"):
            continue
        score = f["protein_g"] + 2 * f["fiber_g"]
        if score > best_score:
            best, best_score = f["name"], score
    return best if best_score >= 6 else None


def _slot_for(minute):
    h = (minute / 60.0) % 24
    if h < 10.5:
        return "breakfast"
    if h < 11.5:
        return "snack"
    if h < 15:
        return "lunch"
    if h < 17.5:
        return "snack"
    return "dinner"


def _hhmm(minute):
    h, m = divmod(int(minute) % (24 * 60), 60)
    ap = "am" if h < 12 else "pm"
    hh = h % 12 or 12
    return "%d:%02d%s" % (hh, m, ap)


# ------------------------------------------- Metabolic Summary Card (RD)

def summary_card(participant, base_meal, base, suggestions, own_threshold,
                 week, day_status):
    """The feature-extracted card the registered dietitian reviews.

    This is the human-in-the-loop step from the protocol: the model sends
    its reasoning to a dietitian, who decides what the participant sees.
    """
    crossed = base["peak_mgdl"] > own_threshold
    return {
        "participant": participant,
        "gestational_week": week,
        "generated": "on meal entry, before eating",
        "crossed_own_threshold": crossed,
        "own_threshold_mgdl": own_threshold,
        "own_threshold_basis": "90th centile of her own blinded run-in week; "
                               "investigator-defined, not a clinical threshold",
        "projected_peak_mgdl": base["peak_mgdl"],
        "projected_peak_min": base["peak_min"],
        "features": [
            {"name": "Net carbohydrate", "value": "%.0f g" % base_meal.net_carb,
             "weight": "primary"},
            {"name": "Carbohydrate : protein",
             "value": "%.1f : 1" % (base_meal.carb_g / max(base_meal.protein_g, 1)),
             "weight": "primary"},
            {"name": "Time of day", "value": _hhmm(base_meal.minute),
             "weight": "secondary",
             "note": "%.0f%% above this participant's evening response"
                     % ((circadian_sensitivity(base_meal.minute)
                         / circadian_sensitivity(19 * 60) - 1) * 100)},
            {"name": "Fiber", "value": "%.0f g" % base_meal.fiber_g,
             "weight": "secondary"},
            {"name": "Gestational week", "value": "W%.0f" % week,
             "weight": "context",
             "note": "insulin sensitivity falls across gestation"},
        ],
        "model_suggests": [s.as_dict() for s in suggestions],
        "guardrails_applied": {
            "carbohydrate_floor_g": CARB_FLOOR_G,
            "day_status": day_status,
            "levers_tried_in_order": ["sequence", "timing", "add", "swap"],
            "suggestions_capped_at": MAX_SUGGESTIONS,
        },
        "awaiting": "registered dietitian review",
    }
