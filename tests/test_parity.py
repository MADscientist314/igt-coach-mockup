"""
Drift tests between the Python engine and the browser engine.

The page computes predictions in JavaScript so it works with no server. The
server computes them in Python. If the two drift apart, the demo and the live
app would tell a participant two different things about the same meal.

A live end-to-end comparison was run in the browser against /api/predict and
matched to 0.0 mg/dL on every case. These tests guard the inputs to that
agreement -- the model coefficients, the profile parameters, the constraints
and the food library -- so a later edit to one side fails here rather than
silently diverging.

    pytest tests -q
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import swaps            # noqa: E402
import synth            # noqa: E402

APP = (ROOT / "web" / "app.html").read_text(encoding="utf-8")
DATA = json.loads((ROOT / "web" / "data.json").read_text(encoding="utf-8"))


def js_slice(start_marker, end_marker):
    a = APP.index(start_marker)
    b = APP.index(end_marker, a)
    return APP[a:b]


# ------------------------------------------------------- model coefficients

MODEL_JS = js_slice("var MODEL = {", "function foodById")


@pytest.mark.parametrize("literal,label", [
    ("0.30", "circadian amplitude"),
    ("7.5", "circadian phase (peak resistance hour)"),
    ("0.016", "gestational drift per week"),
    ("0.55", "protein damping weight"),
    ("0.28", "fat damping weight"),
    ("0.9", "fiber damping weight"),
    ("58", "dose saturation ceiling"),
    ("42", "dose saturation scale"),
    ("18", "fat peak shift"),
    ("1.9", "kernel spread divisor"),
])
def test_js_model_carries_the_python_coefficient(literal, label):
    assert literal in MODEL_JS, "%s (%s) missing from the browser model" % (literal, label)


def test_python_model_carries_the_same_coefficients():
    src = (ROOT / "server" / "synth.py").read_text(encoding="utf-8")
    for literal in ("0.30", "7.5", "0.016", "0.55", "0.28", "0.9", "58.0", "42.0"):
        assert literal in src, literal


def test_sample_interval_matches():
    assert synth.SAMPLE_MIN == 5
    assert "/ 5" in MODEL_JS or "* 5" in MODEL_JS


# ------------------------------------------------------------- the profiles

def test_profiles_match_exactly():
    """The browser reads profiles from data.json, so they cannot drift from
    synth.PROFILES unless data.json is stale."""
    assert DATA["profiles"] == {
        k: dict(v) for k, v in synth.PROFILES.items()
    }, "data.json is stale -- rerun python server/build_data.py"


# ---------------------------------------------------------- the constraints

def test_constraints_match():
    c = DATA["constraints"]
    assert c["carb_floor_g"] == swaps.CARB_FLOOR_G
    assert c["carb_caution_g"] == swaps.CARB_CAUTION_G
    assert c["max_suggestions"] == swaps.MAX_SUGGESTIONS
    assert c["caffeine_ceiling_mg"] == swaps.CAFFEINE_CEILING_MG


def test_floor_constant_is_the_iom_value():
    """175 g/day is the IOM minimum in pregnancy. If this ever changes it
    should be a deliberate, reviewed decision."""
    assert swaps.CARB_FLOOR_G == 175.0
    assert "175" in APP, "the page must be able to state the floor it enforces"


# --------------------------------------------------------- the food library

def test_food_library_matches():
    py = {f: dict(v) for f, v in swaps.foods().items()}
    js = {f["id"]: f for f in DATA["foods"]}
    assert set(py) == set(js), "food ids differ -- rerun build_data.py"
    for fid in py:
        for k in ("carb_g", "protein_g", "fat_g", "fiber_g", "category", "name"):
            assert py[fid][k] == js[fid][k], "%s.%s differs" % (fid, k)


def test_blocked_foods_carry_their_reason_into_the_page_data():
    blocked = [f for f in DATA["foods"] if f.get("blocked_reason")]
    assert len(blocked) >= 5
    for f in blocked:
        assert swaps.is_blocked(f["id"]) == f["blocked_reason"]


def test_swap_targets_all_exist():
    ids = {f["id"] for f in DATA["foods"]}
    for src, alts in DATA["swaps"].items():
        assert src in ids, src
        for a in alts:
            assert a in ids, a


def test_additions_all_exist_and_are_safe():
    ids = {f["id"]: f for f in DATA["foods"]}
    for a in DATA["additions"]:
        assert a["food"] in ids, a["food"]
        assert not ids[a["food"]].get("blocked_reason"), \
            "%s is in the addition library but is unsafe in pregnancy" % a["food"]
        assert a.get("slots"), "%s has no meal slots" % a["food"]


# ------------------------------------------------------- the page's own copy

def participant_copy():
    """Only what renders inside the device frame.

    Deliberately excludes the masthead (which describes the study to a
    reviewer) and the NOTES panel (which discusses the forbidden terms on
    purpose, to explain why they are absent from the app).
    """
    return js_slice("function screenConsent()", "function card(t, b)")


NEGATORS = ("no ", "not ", "never", "nothing", "without", "n't")


def affirmative_uses(term, text):
    """Occurrences of `term` that are NOT inside an explicit denial.

    The app is allowed -- encouraged -- to say "No calorie counting. No
    streaks." Telling a participant what the app will not do to her is the
    opposite of the harm being guarded against. What is forbidden is using
    the term affirmatively about her own data.
    """
    low = text.lower()
    hits = []
    for m in re.finditer(re.escape(term), low):
        window = low[max(0, m.start() - 40):m.start()]
        if not any(neg in window for neg in NEGATORS):
            hits.append(low[max(0, m.start() - 60):m.start() + 40])
    return hits


@pytest.mark.parametrize("bad", [
    "spike", "out of range", "abnormal", "too high", "cheat",
    "lose weight", "calorie", "you went over", "stay under", "bad food",
    "you should avoid", "cut back",
])
def test_participant_screens_never_use_fear_language(bad):
    """Noori 2026: dietary surveillance and fear-based messaging are what
    precipitate disordered eating in this care pathway. The engine's copy is
    checked in test_guardrails; this checks the hand-written screens."""
    hits = affirmative_uses(bad, participant_copy())
    assert not hits, "participant-facing copy uses %r affirmatively: %s" % (bad, hits)


@pytest.mark.parametrize("bad", ["streak", "badge", "points", "goal met", "score"])
def test_participant_screens_do_not_gamify(bad):
    hits = affirmative_uses(bad, participant_copy())
    assert not hits, "participant-facing copy uses %r affirmatively: %s" % (bad, hits)


def test_the_denials_are_actually_present():
    """Guard against the above passing because the copy was deleted."""
    low = participant_copy().lower()
    for phrase in ["no calorie counting", "no weight tracking",
                   "no streaks", "no score"]:
        assert phrase in low, "the app no longer states %r" % phrase


def test_page_states_the_api_delay_and_the_scope_limitation():
    """Two facts the Dexcom documentation forces on the protocol."""
    assert "hour" in APP and "behind" in APP, "the one-hour delay is not stated"
    assert "not let you pick" in APP or "one permission" in APP, \
        "all-or-nothing scopes are not stated"


def test_page_declares_its_provenance():
    assert "GLAM" in DATA["provenance"]
    assert "not traceable" in DATA["provenance"] or \
           "no value shown here is traceable" in DATA["provenance"].lower()
