# IGT Coach — IRB mock-up

An interactive mock-up of the participant app and dietitian console for the
three-arm study of AI-guided CGM in pregnancies with **intermediate glucose
intolerance** (abnormal 1-hour screen, normal 3-hour test).

Built to answer the three questions an IRB actually asks about a study app:
**what does the participant see, what does it tell her to do, and what happens
when it sees something alarming.**

---

## Run it

```bash
python server/main.py          # http://127.0.0.1:8765
```

Standard library only, plus `numpy` and `pyyaml`. No web framework, nothing to
version-conflict with. If `web/data.json` is missing it is rebuilt on startup.

```bash
python -m pytest tests -q      # 109 tests
python server/build_data.py    # regenerate the frozen dataset
```

The page is **self-sufficient** — it computes everything in the browser from
`data.json`, so `web/app.html` + `web/data.json` work with no server at all.
That is how the shareable copy is published.

### Live Dexcom sandbox

Create an app at [developer.dexcom.com/account/apps](https://developer.dexcom.com/account/apps)
with redirect URI `http://127.0.0.1:8765/auth/callback`, then open
**<http://127.0.0.1:8765/setup>** and paste the client ID and secret. They are
written to a gitignored `.env` and nothing echoes them back.

Then: Connect the sensor → Sign in with Dexcom → pick a sandbox account (no
password in sandbox) → sign the HIPAA authorization. Tokens persist to a
gitignored `.token.json`, so restarting the server does not mean redoing the
consent flow.

Once connected, the Connect screen offers **Load live readings**, which pulls
real EGVs from the sandbox and renders them on Today behind a banner saying
what they are. Live mode deliberately drops the personal band, the threshold
line and the demo participant's statistics — those belong to the demo
participant, not to a sandbox account, and mixing them on one screen is exactly
what would mislead a reviewer.

**Verified end to end on 22 Oct 2026**: authorization code exchanged, access
token issued (2 h TTL), `/dataRange` returning 2025-09-04 → 2026-10-22 for the
G6 account, and 1,904 live EGVs over seven days rendering in the app.

This flow is worth screenshotting for the packet. It shows reviewers the actual
authorization surface a participant meets.

---

## Dexcom constraints that belong in the protocol

Documentation checked 22 Oct 2026; the items marked **(live)** were confirmed
against the sandbox API that same day.

**The G7 does not log carbohydrate.** Per the `/events` documentation, `carbs`
arrives only from the **G6 mobile app and the receiver interfaces**. The G7
mobile app records `bloodGlucose` and `insulin` only; Dexcom ONE does not
support `/events` at all. G7 is the sensor with the FDA pregnancy/GDM
indication, so meal capture has to live in our app regardless.

**(live) The sandbox cannot demonstrate carbohydrate events at all — not even
on G6.** The Sandbox Data page states that "insulin and carb events have been
populated for all users." That is not what the API returns. Scanning the *full*
13.5-month range for the G6 account, 30 days at a time:

| | |
|---|---|
| Total events in the entire range | **21** |
| Event types present | `insulin` only |
| Sub-types present | `longActing` only |
| Carbohydrate events | **none, anywhere** |
| Window containing them | 2025-09-04 → 2025-10-03 only |
| Values | 137–269 units of long-acting insulin |

Those doses are not clinically plausible, which is a useful reminder that
Dexcom's own warning is accurate: sandbox data "should not be used for formal
testing or validation, nor for training algorithms." Two separate conclusions
follow, and they should not be conflated — the **documented production**
constraint is that G7 does not log carbs; the **sandbox** additionally fails to
populate carbs for any account, so it cannot be used to prototype or demo the
meal path. Either way the answer for the study is the same: **meals are
captured in our app.**

**(live) The consent screen lists five data categories, not four.** The Scopes
page names four (EGVs, calibrations, events, device details). The HIPAA
authorization a participant actually signs lists **Estimated Blood Glucose
Levels, Calibration Data, Device Details, CGM Statistics and Events Data** —
"CGM Statistics" appears nowhere in the scope documentation. The consent form
should mirror the screen, not the docs.

**(live) The authorization says Dexcom's protections stop at our door.**
Verbatim: *"your Personal Information once transferred is no longer subject to
Dexcom's Privacy Policy or any applicable protections under privacy laws
applicable to Dexcom."* Also verbatim, and matching what the Withdraw screen
already tells participants: revocation at Dexcom "will promptly terminate
providing further Personal Information … but any information previously
provided will not be affected."

**(live) The app's registered name is participant-facing.** The consent text
names the app five times. For the real study it must be the name participants
should see, not an internal one.

**(live) The sandbox account list differs from the documentation.** The login
dropdown offers G6, G7, **STELO**, **D1+** and G715DAY. The docs table lists
G6, G7, D1 and G715DAY — no Stelo.

**The API is not real-time.** Mobile-app data reaches the web API with a
**one-hour delay** in the US, three hours elsewhere. Prediction cannot run off a
live feed, and real-time alerting is not buildable on this API. The app says so
on two screens. This is also a regulatory asset — it keeps the product clear of
"alerts, alarms, or prompts that recommend specific clinical action".

**Limited Access caps at five production users.** Sandbox is free and
unlimited. Beyond five real participants needs a commercial partnership with a
Dexcom technical and UI review, or the research route at
`provider.dexcom.com/education-research/research-programs`. That is a
Dexcom-side gate on enrolment, outside our control, and belongs in the
feasibility section now.

Also: scopes are all-or-nothing (EGVs, calibrations, events, devices — a
participant cannot consent to a subset, which goes in the consent form
verbatim); 30-day maximum query window; tokens must be held server-side; v2
endpoints are withdrawn in October 2026, so this is v3 only.

---

## Where the numbers come from

Nothing on screen is traceable to a human subject.

Traces come from a forward physiological model (`server/synth.py`) calibrated
to **published GLAM summary statistics** — 937 pregnancies, Dexcom G6,
*Diabetes Care* 2024 ([PMC11097821](https://pmc.ncbi.nlm.nih.gov/articles/PMC11097821/)):
mean glucose 98 ± 7 mg/dL, median time 63–120 mg/dL 86%, postprandial peak
126 ± 22, mean meal excursion 36 ± 22. Calibrating to published *statistics*
rather than to GLAM's *records* is what makes the provenance sentence a single
line in the IRB packet.

`test_synthetic_traces_match_published_glam_statistics` asserts the
uncomplicated profile lands in that neighbourhood, so the claim stays true.

The model has three levers, exactly the ones the proposal names: **which
carbohydrate, in what combination, at what time of day** — plus gestational
drift, because insulin sensitivity falls across pregnancy. A breakfast peaks
substantially higher than the identical meal at dinner, which is the single
most useful thing the app shows anyone.

---

## Design decisions that are safety decisions

**Her own threshold, never a population target.** The blinded run-in week sets
her baseline; her threshold is the 90th centile of her own week, and the
console says in words that it is investigator-defined and not a clinical
threshold. This is not a stylistic choice — **no validated CGM target exists
for GDM, let alone for intermediate glucose intolerance.** The 2019
international targets (63–140 mg/dL, >70% TIR) are for type 1 diabetes in
pregnancy (Szmuilowicz 2024, PMID 38606830). Showing a woman with a normal
OGTT a pass/fail line against someone else's number would be inventing a
diagnosis.

**A floor, not a ceiling.** `swaps.py` enforces ≥175 g carbohydrate/day (IOM);
there is RCT evidence of micronutrient deficiency at ≤165 g/d in GDM (Sweeting
2021, PMID 34444759). Only a suggestion that *removes* carbohydrate can breach
it, and on a day already short the engine can only add food. Asserted under
adversarial input across every meal slot.

**Levers ordered least-restrictive first.** `sequence` → `timing` → `add` →
`swap`. The first two change no food at all; `add` puts more on the plate.
Timing is deliberately never offered at breakfast, because "move breakfast to
lunchtime" is "skip breakfast". Noori 2026 (PMID 41995724) found GDM care
protocols "may exacerbate or precipitate" disordered eating through "dietary
surveillance, glucose control and fear-based messaging" — and this population
has not been told they have a condition at all.

**A pattern display, not a forecast.** The UI says *meals like this have
usually been followed by*, never *your glucose will reach 158*. Same model,
materially different claim under the FDA General Wellness guidance reissued
6 Jan 2026. (Neither the General Wellness nor the CDS pathway is available
here — General Wellness names CGMs as a non-wellness example, and the CDS
carve-out is clinician-facing only and excludes anything processing "a pattern"
from a signal acquisition system. The defensible position is enforcement
discretion under the Device Software Functions policy, with an IDE-exempt or
NSR determination memo.)

**No gamification.** No calorie counting, no weight tracking, no streaks, no
scores. The app says so on the Settings screen, and the tests assert both that
the terms never appear affirmatively *and* that the denials are still present.

**Pregnancy food-safety blocklist.** High-mercury fish, unpasteurised dairy,
raw or undercooked animal food, unheated deli meat, preformed vitamin A,
alcohol. A swap engine that optimises a curve without knowing it is talking to
a pregnant woman will eventually suggest something unsafe.

---

## What is in here

```
server/
  synth.py        GLAM-calibrated trace + meal-response model
  swaps.py        swap engine, guardrails, Metabolic Summary Card
  dexcom.py       Dexcom v3 sandbox client (stdlib only)
  main.py         stdlib HTTP server + OAuth callback
  build_data.py   writes web/data.json
  content/        foods, swaps, blocklist (YAML, dietitian-authorable)
web/
  app.html        the whole UI, one file
  data.json       frozen synthetic dataset
tests/            109 tests
```

`web/app.html` is authored as an Artifact fragment (no document skeleton) so
the same file serves locally and publishes as a shareable link. The server
wraps it in the identical skeleton the Artifact platform uses, so it looks the
same either way.

**The prediction engine exists twice** — Python in `swaps.py`, JavaScript in
`app.html` — because the page has to work without a server. They were compared
live against `/api/predict` across five meals and agreed to **0.0 mg/dL** with
identical suggestion lists. `tests/test_parity.py` guards the inputs to that
agreement so a later edit to one side fails loudly instead of drifting.

---

## The eleven screens

Starred ones are what the IRB is really buying.

1. Consent & AI disclosure — layered, with a comprehension check
2. Connect the sensor — the real Dexcom OAuth and HIPAA surface
3. Blinded run-in week — the screen stays dark on purpose
4. **Today ★** — unblinded, her own band, no verdict
5. **Plan a meal ★** — the front door; predict, then 1–3 suggestions
6. How close were we — the model held to account
7. This week — weekly narrative from a bounded library
8. **Flagged to the dietitian ★** — the escalation pathway
9. What this app does not do
10. Settings & pause — a real off switch
11. **Withdraw & delete ★** — three copies, three different answers

Plus a **dietitian console** with the review queue and the Metabolic Summary
Card. The "Arm" switch shows the monitor-only arm, where screens 5, 6 and 8 are
absent — the algorithm is the only difference between the randomised groups.

---

## Still to chase

- **IDEAL trial results** (Crowther, ACTRN12607000174482) — an RCT in exactly
  this population, positive GCT with normal OGTT, primary outcome LGA, powered
  for 14%→7%. Protocol published; results not findable. If published and null,
  the background must meet it head-on; if never completed, that is itself the
  argument for this pilot.
- **NCT05491954** (Ohio State) — CGM across euglycemia vs. glucose intolerance
  vs. GDM in pregnancy. Status unknown, completion Aug 2023. Nearest existing
  study; worth an email before submitting.
- **ACOG Practice Bulletin 190** — quote directly, not from secondary summaries.
- **Device determination** — confirm with Baylor regulatory affairs; a CDRH
  Pre-Submission is free if they want certainty.
- **Spanish**, given the Texas Children's catchment. Pregnant+ (n=238) did the
  multilingual work properly and still showed no dietary benefit, so translation
  is necessary and not sufficient — but English-only is an equity finding
  waiting to happen.
- **GLAM licence** — Jaeb serves it as a direct download with no visible terms.
  Get an attributable licence statement in writing before citing it either way.
  (The mock-up does not use GLAM records, only its published statistics.)
