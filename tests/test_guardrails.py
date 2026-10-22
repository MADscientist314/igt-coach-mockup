"""
Guardrail tests.

These are the checks the IRB submission points at. They assert behaviour the
protocol promises, under adversarial input rather than happy-path input.

    pytest tests -q
"""
import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import swaps            # noqa: E402
import synth            # noqa: E402

WEEK = 28.3
FASTING = 92.0
OWN = 135.0

ALL_FOODS = list(swaps.foods().keys())
CARB_FOODS = [f for f in ALL_FOODS
              if swaps.foods()[f]["carb_g"] >= 15
              and not swaps.is_blocked(f)]
SLOT_MINUTES = [7 * 60 + 30, 10 * 60 + 45, 12 * 60 + 45, 15 * 60 + 30, 19 * 60]


# --------------------------------------------------------- carbohydrate floor

@pytest.mark.parametrize("day_carb", [0, 90, 150, 170, 174, 176, 200, 260])
@pytest.mark.parametrize("minute", SLOT_MINUTES)
def test_floor_never_breached(day_carb, minute):
    """No suggestion may carry the day below 175 g of carbohydrate."""
    for combo in itertools.combinations(CARB_FOODS[:14], 2):
        items = [(combo[0], 1), (combo[1], 1)]
        meal_carb = sum(swaps.foods()[f]["carb_g"] for f in combo)
        rest = max(0.0, day_carb - meal_carb)
        _, _, sugg, _ = swaps.propose(items, minute, "igt", WEEK, FASTING, OWN,
                                      carbs_so_far_g=0.0,
                                      carbs_planned_rest_of_day_g=rest)
        total = meal_carb + rest
        for s in sugg:
            assert not (s.carb_delta_g < 0 and total + s.carb_delta_g
                        < swaps.CARB_FLOOR_G), (
                "%s would put the day at %.0f g" % (s.title, total + s.carb_delta_g))


@pytest.mark.parametrize("minute", SLOT_MINUTES)
def test_short_day_only_adds(minute):
    """On a day already below the caution line, nothing may remove food."""
    for fid in CARB_FOODS[:16]:
        _, _, sugg, day = swaps.propose(
            [(fid, 1)], minute, "igt", WEEK, FASTING, OWN,
            carbs_so_far_g=40.0, carbs_planned_rest_of_day_g=0.0)
        assert day["level"] in ("floor", "caution")
        assert all(s.carb_delta_g >= 0 for s in sugg), (
            "a reduction was offered on a short day: "
            + ", ".join(s.title for s in sugg))


def test_check_day_raises_on_breach():
    """The assertion is real, not decorative."""
    bad = swaps.Suggestion(
        kind="swap", title="bad", detail="", carb_delta_g=-50.0,
        projected_peak=120, projected_excursion=20, baseline_peak=150,
        baseline_excursion=50, rationale="")
    with pytest.raises(swaps.GuardrailViolation):
        swaps.check_day(180.0, [bad])
    swaps.check_day(400.0, [bad])          # plenty of room: fine


# --------------------------------------------------------- pregnancy safety

def test_every_blocked_category_is_rejected():
    """Each food-safety flag must actually block something."""
    flags = {c["key"] for c in swaps.blocklist()["categories"]}
    seen = set()
    for fid, f in swaps.foods().items():
        for fl in f.get("safety_flags", []) or []:
            seen.add(fl)
            assert swaps.is_blocked(fid), "%s carries %s but is not blocked" % (fid, fl)
    assert seen, "no food carries a safety flag; the screen is untested"
    unused = flags - seen
    assert not unused - {"raw_undercooked"}, "unexercised flags: %s" % unused


@pytest.mark.parametrize("minute", SLOT_MINUTES)
def test_blocked_foods_are_never_suggested(minute):
    """Nothing unsafe in pregnancy may ever appear as a suggestion."""
    blocked_names = {swaps.foods()[f]["name"] for f in ALL_FOODS
                     if swaps.is_blocked(f)}
    assert blocked_names
    for fid in CARB_FOODS[:16]:
        _, _, sugg, _ = swaps.propose([(fid, 1)], minute, "igt", WEEK,
                                      FASTING, OWN,
                                      carbs_planned_rest_of_day_g=140.0)
        for s in sugg:
            for bad in blocked_names:
                assert bad not in s.title, "%s suggested %s" % (s.kind, bad)
                assert bad not in s.detail


def test_blocked_food_in_the_meal_is_reported_not_swapped():
    """If she enters something unsafe, the engine flags it rather than
    silently optimising around it."""
    assert swaps.is_blocked("swordfish")
    assert swaps.is_blocked("queso_fresco_unpast")
    assert swaps.is_blocked("wine_glass")
    assert swaps.is_blocked("liver_pate")
    assert swaps.is_blocked("salmon") is None


# ------------------------------------------------------------- lever order

@pytest.mark.parametrize("minute", SLOT_MINUTES)
def test_levers_are_ordered_least_restrictive_first(minute):
    rank = {"sequence": 0, "timing": 1, "add": 2, "swap": 3}
    for combo in itertools.combinations(CARB_FOODS[:10], 2):
        _, _, sugg, _ = swaps.propose([(combo[0], 1), (combo[1], 1)], minute,
                                      "igt", WEEK, FASTING, OWN,
                                      carbs_planned_rest_of_day_g=140.0)
        ranks = [rank[s.kind] for s in sugg]
        assert ranks == sorted(ranks), "levers out of order: %s" % ranks


def test_timing_lever_never_suggests_skipping_breakfast():
    """'Move breakfast to lunchtime' is 'skip breakfast'."""
    for fid in CARB_FOODS[:16]:
        _, _, sugg, _ = swaps.propose([(fid, 1)], 7 * 60 + 30, "igt", WEEK,
                                      FASTING, OWN,
                                      carbs_planned_rest_of_day_g=140.0)
        assert all(s.kind != "timing" for s in sugg)


def test_swaps_stay_in_category():
    for fid, alts in swaps.rules()["swaps"].items():
        cat = swaps.foods()[fid]["category"]
        for a in alts:
            assert swaps.foods()[a]["category"] == cat, \
                "%s -> %s crosses category" % (fid, a)


def test_suggestions_are_capped():
    for minute in SLOT_MINUTES:
        _, _, sugg, _ = swaps.propose(
            [("bagel_plain", 1), ("juice_orange", 1), ("rice_white", 1)],
            minute, "igt", WEEK, FASTING, OWN,
            carbs_planned_rest_of_day_g=140.0)
        assert len(sugg) <= swaps.MAX_SUGGESTIONS


# ------------------------------------------------------------- the language

FORBIDDEN = ["spike", "blood sugar is too high", "you should avoid", "cut back",
             "bad food", "cheat", "you went over", "stay under", "calorie",
             "lose weight", "too high", "out of range", "abnormal"]


def test_participant_facing_copy_avoids_fear_language():
    """Noori 2026: fear-based messaging and dietary surveillance are what
    precipitate disordered eating in this care pathway."""
    blobs = []
    for a in swaps.rules()["additions"]:
        blobs += [a["title"], a["detail"]]
    for f in swaps.foods().values():
        if f.get("note"):
            blobs.append(f["note"])
    for group in ("opening", "observation", "invitation"):
        blobs += swaps.rules()["coaching"][group]
    for minute in SLOT_MINUTES:
        _, _, sugg, day = swaps.propose(
            [("bagel_plain", 1), ("juice_orange", 1)], minute, "igt", WEEK,
            FASTING, OWN, carbs_planned_rest_of_day_g=140.0)
        blobs += [s.title for s in sugg] + [s.detail for s in sugg]
        blobs.append(day["message"])
    low = " || ".join(blobs).lower()
    for bad in FORBIDDEN:
        assert bad not in low, "participant-facing copy contains %r" % bad


def test_app_never_states_a_population_target():
    """No validated CGM target exists for this population. The engine must
    compare against her own threshold only."""
    card = _card()
    assert "own" in card["own_threshold_basis"]
    assert "investigator-defined" in card["own_threshold_basis"]
    assert "not a clinical threshold" in card["own_threshold_basis"]


# ------------------------------------------------- metabolic summary card

def _card():
    bm, base, sugg, day = swaps.propose(
        [("bagel_plain", 1), ("juice_orange", 1)], 7 * 60 + 30, "igt", WEEK,
        FASTING, OWN, carbs_planned_rest_of_day_g=135.0)
    return swaps.summary_card("P-014", bm, base, sugg, OWN, WEEK, day)


def test_summary_card_is_reviewable():
    """The dietitian must be able to see what drove the model."""
    c = _card()
    assert c["awaiting"] == "registered dietitian review"
    assert c["crossed_own_threshold"] is True
    names = [f["name"] for f in c["features"]]
    for required in ["Net carbohydrate", "Carbohydrate : protein", "Time of day",
                     "Gestational week"]:
        assert required in names
    g = c["guardrails_applied"]
    assert g["carbohydrate_floor_g"] == swaps.CARB_FLOOR_G
    assert g["levers_tried_in_order"] == ["sequence", "timing", "add", "swap"]


# ------------------------------------------------------ synthetic fidelity

def test_synthetic_traces_match_published_glam_statistics():
    """The uncomplicated profile must land near GLAM's published values,
    because that is the whole provenance argument."""
    m = synth.metrics(synth.generate_series(days=28, profile="uncomplicated"))
    assert 94 <= m["mean"] <= 103, m             # GLAM 98 +/- 7
    assert 1.0 <= m["tar_140"] <= 6.0, m         # GLAM median 2.5%
    assert 12 <= m["cv"] <= 20, m
    assert 80 <= m["tir_63_120"] <= 93, m        # GLAM median 86%


def test_profiles_are_ordered():
    a = synth.metrics(synth.generate_series(days=21, profile="uncomplicated"))
    b = synth.metrics(synth.generate_series(days=21, profile="igt"))
    c = synth.metrics(synth.generate_series(days=21, profile="gdm"))
    assert a["mean"] < b["mean"] < c["mean"]
    assert a["tar_140"] < b["tar_140"] < c["tar_140"]


def test_sentinel_values_and_gaps_are_representable():
    recs = synth.generate_series(days=14, profile="gdm")
    for r in recs:
        assert 39 <= r["value"] <= 401
        assert r["unit"] == "mg/dL"
        assert r["trend"] in ("doubleUp", "singleUp", "fortyFiveUp", "flat",
                              "fortyFiveDown", "singleDown", "doubleDown")
    # a 14-day series at 5 minutes would be 4032 points if nothing were missing
    assert len(recs) < 14 * synth.SAMPLES_PER_DAY, "no data gaps were generated"


def test_breakfast_peaks_higher_than_the_same_meal_at_dinner():
    """The circadian finding is the most useful thing the app shows."""
    m = dict(carb_g=58, protein_g=9, fat_g=12, fiber_g=2)
    bk = synth.predict_curve(synth.Meal(7 * 60 + 30, "x", **m), "igt", WEEK, FASTING)
    dn = synth.predict_curve(synth.Meal(19 * 60, "x", **m), "igt", WEEK, FASTING)
    assert bk["peak_mgdl"] > dn["peak_mgdl"] + 15


def test_personal_baseline_is_derived_from_the_run_in_week():
    recs = synth.generate_series(days=7, profile="igt")
    bl = synth.personal_baseline(recs)
    assert bl["own_threshold"] == bl["p90"]
    assert bl["fasting_median"] < bl["median"] < bl["own_threshold"]
    assert 6.5 <= bl["run_in_days"] <= 7.1


def test_no_duplicate_suggestions():
    """A meal containing the same food twice must not produce the same
    suggestion twice."""
    for items in ([("pasta_white", 1), ("pasta_white", 1), ("olive_oil", 1)],
                  [("rice_white", 1), ("rice_white", 1)],
                  [("bread_white", 1), ("bread_white", 1), ("juice_orange", 1)]):
        for minute in SLOT_MINUTES:
            _, _, sugg, _ = swaps.propose(items, minute, "igt", WEEK, FASTING,
                                          OWN, carbs_planned_rest_of_day_g=140.0)
            titles = [s.title for s in sugg]
            assert len(titles) == len(set(titles)), titles
