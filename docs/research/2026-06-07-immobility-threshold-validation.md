# Spike #64 — Ground-truth validation method for the V1 immobility threshold

**Date:** 2026-06-07 · **Timebox:** 1 day · **Status:** complete
**Question:** How do we validate that the V1 immobility threshold
(`fusion.health.immobility_seconds`, plus the dwell logic in #19) corresponds to
*real* animal immobility rather than a guessed default — and what is the cheapest
repeatable ground-truth method? **Output is a procedure + a number, not production
code** (no ADR — this is tuning/validation, not architecture).

---

## 1. What the threshold actually means (ground it in the code)

`HealthMonitor.update_immobility` (`src/overwatch/fusion/health.py`) is **2D
image-centroid dwell** logic (ADR-0006: mono-capable, no depth):

- Each track keeps an `anchor` centroid and a `since` timestamp.
- If the centroid drifts **> `move_threshold_px`** (code default **25 px**) the
  anchor/timer reset — "the animal moved".
- Once a track has stayed within `move_threshold_px` of its anchor for
  **≥ `immobility_seconds`** (config default, currently `600`), it emits **one**
  immobility `HealthSignal` → `Alert` for that episode (it does not re-fire while
  the animal keeps resting).

So the alert fires on an animal whose **image-plane location** has not changed by
more than ~25 px for the threshold duration. Two parameters govern it:
`immobility_seconds` (time) and `move_threshold_px` (space). Both are placeholders;
the schema flags `immobility_seconds` `must_tune`.

## 2. Why a guessed default is risky — the resting confound

The dominant failure mode is **false positives from normal rest**, not missed
detections. Ruminants (sheep, goats) and poultry spend a large fraction of the day
**lying/resting/ruminating essentially motionless**, in continuous bouts that
routinely run **tens of minutes**, and many hours of lying per 24 h (more, and
longer bouts, overnight). A stationary animal is, by base rate, almost always just
resting — **normal**, not a health event.

What the alert is *meant* to catch is **abnormal** immobility: a **cast** sheep
(rolled onto its back, unable to right itself — can be fatal within hours), severe
illness/injury, or birthing difficulty. These are rare and high-value.

Consequences for tuning:

- **`immobility_seconds: 600` (10 min) is below the duration of a normal resting
  bout** → it will fire constantly on healthy resting animals. It is **not
  defensible** as shipped; it guarantees alert fatigue, which destroys the value of
  a core V1 output.
- The threshold must sit **above the upper range of normal voluntary rest** to buy
  precision, while staying **low enough to catch a cast/incapacitated animal inside
  a clinically useful window** (help needed within ~hours, so a detection latency of
  tens of minutes is acceptable).
- **V1 has no posture signal** (lying-vs-standing needs pose/depth — pose is V2,
  #22). So V1 immobility is a **coarse screen biased toward the most extreme cases**
  (truly motionless animals), not a diagnostic. The validation method must measure,
  not assume, the false-positive rate this incurs.

## 3. The validation method (cheap, repeatable)

The method turns "is the threshold right?" into a measured precision/latency
trade-off against human ground truth, and is **fully host-runnable** once a
recording exists — it reuses the existing record/replay harness (#99/#102:
`MessageRecorder` captures the `infer.track` stream; `replay_to_bus` /
`ReplaySource` play it back), so the threshold sweep is a one-command rerun with **no
device and no live animals**.

### Step 1 — Capture a representative recording (on-device, once)
On the Jetson during a live run (#84), record the **`infer.track`** stream with
`MessageRecorder` to a `.owrec` file over a window that covers the **full behaviour
mix**: animals grazing/walking, animals resting (the critical confound), and — if
obtainable — at least one genuine prolonged-immobility episode. Also save the
synchronized **video** (the burned-in operator feed, ADR-0008) for annotation.

> Genuine pathological immobility is rare and can't be staged ethically. Use
> proxies: (a) opportunistically capture naturally long-still resting episodes
> (these exercise the false-positive axis, which is the binding constraint); and/or
> (b) a deliberately stationary stand-in (a tracked decoy / a person holding still)
> to exercise the true-positive/latency axis. The method scores both axes
> separately, so a proxy on either axis is still informative.

### Step 2 — Human ground-truth annotation
Watch the synchronized video and, **per `track_id`**, log every **stationary
episode** as a CSV row: `track_id, t_start, t_end, label` where `label ∈
{normal_rest, concern}`. "Stationary" = the animal is not changing location (a
resting animal that shifts its head/weight but stays put is *one* stationary
episode). A stopwatch and a spreadsheet are sufficient — no tooling required. This
CSV is the ground truth.

### Step 3 — Sweep the detector logic over the recording (host)
Replay the recorded `infer.track` through `HealthMonitor.update_immobility` for a
**grid of `(immobility_seconds, move_threshold_px)`** values, recording, per track,
*when* (or whether) the monitor would fire.

> **Timing gotcha — drive immobility off *capture* time, not replay wall-clock.**
> `Track` carries **no timestamp**; in the live pipeline immobility time comes from
> `MonoAlertFanout`'s injected **`clock`** (default `time.monotonic`) — `FrameAssembler`
> stamps each assembled frame with `clock()`. So replaying the recording
> as-fast-as-possible under the default clock would compress hours of footage into
> seconds and **never cross the threshold**. Reconstruct capture time instead:
> advance a synthetic clock by **`1/fps` per assembled frame** (fps from
> `capture.sources[].fps`). This also lets the sweep run **instantly and
> deterministically** over arbitrarily long clips — no real-time wait. (The shipped
> `clock=` seam on `MonoAlertFanout` exists precisely for this; here we drive
> `HealthMonitor` directly for clarity.)

Illustrative driver (analysis tooling, **not** a new production module):

```python
# Drive the shipped HealthMonitor over a recorded infer.track clip, reconstructing
# capture time from frame_id + the configured fps so timing matches the live run.
# `records` is the recorded (topic, msg) stream in order — obtain it by replaying
# the .owrec onto an in-process bus (overwatch.capture.recording.replay_to_bus) and
# capturing the pairs; the .owrec format guarantees recorded order.
from overwatch.bus import topics
from overwatch.fusion.health import HealthMonitor

def fire_times(records, immobility_seconds, move_px, fps):
    """Return {track_id: first_fire_time} for one parameter pair."""
    mon = HealthMonitor(immobility_seconds=immobility_seconds, move_threshold_px=move_px)
    out, frame_index, last_frame = {}, -1, None
    for topic, msg in records:
        if topic != topics.INFER_TRACK:
            continue
        if msg.frame_id != last_frame:        # new frame -> advance the synthetic clock
            frame_index += 1
            last_frame = msg.frame_id
        t = frame_index / fps                 # capture-time, NOT replay wall-clock
        sig = mon.update_immobility(t, msg)
        if sig and sig.track_id not in out:
            out[sig.track_id] = t
    return out

for secs in (300, 600, 900, 1800, 2700, 3600):
    for move_px in (15, 25, 40):
        fired = fire_times(records, secs, move_px, fps=15)
        # ... score `fired` against the ground-truth CSV (Step 4) ...
```

### Step 4 — Score against ground truth
For each `(immobility_seconds, move_threshold_px)` pair, cross the fire log with the
CSV:

- **False positives** = fires whose time falls inside a `normal_rest` episode.
  Normalize to an operator-facing rate: **false alerts / animal / day**.
- **True positives** = fires inside a `concern` episode.
- **Missed** = `concern` episodes with no fire.
- **Detection latency** = `fire_time − episode_start` for true positives.

Tabulate it (a small grid):

| `immobility_seconds` | FP / animal / day | TP rate | Missed | Median latency |
|---|---|---|---|---|
| 600  | (expect high) | … | … | low |
| 1800 | (expect low)  | … | … | ~30 min |
| 3600 | (expect ~0)   | … | … | ~60 min |

### Step 5 — Pick the operating point, then record it
Choose the **smallest `immobility_seconds`** that meets a precision target — suggested
**≤ ~1 false alert / animal / day** (above that, operators tune out) — while keeping
median true-positive latency within the clinical window (**≤ ~1 h** for a cast
animal). Tune `move_threshold_px` so normal bbox jitter doesn't reset the timer
(too-small → missed) while real relocation does (too-large → false). Write the chosen
numbers and the table back into this doc and into `configs/default.yaml`.

### Step 6 — Repeatability
Re-run Steps 1–5 whenever the **camera moves, resolution changes, or a new
pen/species** is added (all change the px↔animal scale and the behaviour mix). Because
the sweep is host-side replay, re-validation after a new recording is one command.

## 4. The defensible V1 default (the number)

Pending the on-device sweep above, the **defensible starting default is
`immobility_seconds: 1800` (30 minutes)**, replacing the indefensible `600`:

- `600` sits **below** a normal resting bout → near-certain false positives; it
  cannot be shipped as-is.
- `1800` favours **precision** (sits above typical single rest bouts) while still
  detecting a cast/incapacitated animal within ~30 min — comfortably inside the
  clinical window. It is a **conservative starting estimate, not a measured value**:
  Step 5 replaces it with the site-measured operating point (which, for overnight or
  heavy-resting herds, may need to go **higher**, e.g. 2700–3600 s).

`move_threshold_px` stays at the code default **25 px** for now, but note it is
**scale-dependent** (animal pixel size varies with camera distance/resolution); a
sensible follow-on is to express it **relative to bbox size** rather than absolute
pixels, and/or expose it in config for tuning. That is a code change and is **out of
scope for this spike** — flagged, not done.

## 5. Exit criteria

- [x] **Documented, repeatable validation procedure** (§3) recorded under `docs/`,
      reusing the #11/#102 record/replay harness; host-runnable, final confirmation
      on-device.
- [x] **Defensible default `immobility_seconds` for V1** (§4): `600 → 1800`, with
      rationale and the procedure to refine it. Reflected into `configs/default.yaml`
      (the placeholder it owns) — this is the "reflect into #19's AC" action, since
      #19 (immobility slice) is merged and consumes that config value.
- [x] **Output is a procedure + a number, not production code** — no new/changed
      `src/` logic; the only artifact change is the config placeholder and this doc.
- [ ] **On-device run** of Steps 1–5 against real clips (needs a live #84 capture)
      replaces the 1800 s estimate with a measured operating point — **deferred to
      the first on-device capture window**; this spike delivers the method + starting
      number, not the measured result.

## 6. Limitations (honest)

V1 immobility is a **location-dwell screen without posture** — it cannot distinguish
"lying and resting" from "lying and unable to rise". The threshold can only trade
false-positive rate against latency; it cannot fix that blind spot. The real
precision lift is **posture from pose+depth (V2, #22)**. Until then, the alert should
be presented to operators as a **"check on this animal" prompt, not a diagnosis**,
and the threshold tuned to keep its false-alert rate low enough to stay trusted.
