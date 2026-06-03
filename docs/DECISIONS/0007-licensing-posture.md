# ADR 0007 — Project licensing posture (non-commercial / AGPL / public repo)

- **Status:** Accepted
- **Date:** 2026-06-03
- **Deciders:** Product Owner

## Context

Earlier work assumed Overwatch was a **commercial** product. That premise drove
ADR-0005 (#27), which treated the `cc-by-nc-4.0` (NON-COMMERCIAL) license on the
named ReID weights (`BVRA/MegaDescriptor-T-224`) as a hard shipping blocker, and
it shaped the detector-model selection spike (#5), which was steering away from
copyleft/non-commercial detectors.

While selecting the V1 detector model (#5), the licensing analysis surfaced the
same class of blocker on the detector side: the most mature, best-tooled option
(Ultralytics YOLOv8/YOLO11) is **AGPL-3.0** on both code and weights — a blocker
*only if* the product is closed-source and commercial. This forced the prior
question: **is Overwatch actually a commercial product?**

The PO's answer (2026-06-03): **No.** Overwatch is a **non-commercial** project,
and the repository can be made **public / open-source**. This ADR records that
posture as a first-class, project-wide decision, because it invalidates the
"commercial product" assumption that several other decisions were built on.

## Options considered

### Option A — Commercial / closed-source (the prior, now-incorrect assumption)
- Pros: preserves the option to sell Overwatch; forces permissive-only deps.
- Cons: blocks AGPL detectors (Ultralytics) and `cc-by-nc` weights
  (MegaDescriptor) unless replaced/relicensed — the live blocker behind #27 and
  the friction in #5. Was never actually the project's intent.

### Option B — Non-commercial + public repo, AGPL-compatible copyleft (chosen)
- Pros: **unblocks AGPL detectors** (Ultralytics YOLO — best DeepStream tooling)
  by open-sourcing the app; **unblocks `cc-by-nc` weights** (keep MegaDescriptor
  as-is) because the use is non-commercial — this **dissolves the #27 blocker**.
  Going public also **unlocks GitHub branch protection / rulesets** on the free
  plan (previously 403'd while private — see the repo plan-limits note).
- Cons: **one-way door** — adopting AGPL deps + `cc-by-nc` weights forecloses
  ever taking Overwatch closed-source/commercial without ripping those out.
  `cc-by-nc` "non-commercial" has fuzzy edges if a deployment derives economic
  benefit (e.g. running a working farm) — see Consequences.

### Option C — Non-commercial intent, but stay permissive anyway
- Pros: keeps a future commercial pivot cheap (no AGPL/NC lock-in).
- Cons: gives up Ultralytics' tooling advantage and the zero-cost reuse of the
  named MegaDescriptor weights for no present benefit, given the non-commercial
  commitment.

## Decision

Adopt **Option B**: Overwatch is **non-commercial** and **open-source under
AGPL-3.0**, with a **public** repository.

Consequently:
- **Detector (#5):** Ultralytics **YOLOv8** is licensing-cleared (AGPL-3.0, OK
  under this posture). Recorded in `docs/SOFTWARE_STACK.md`.
- **ReID (#27 / ADR-0005):** keep `BVRA/MegaDescriptor-T-224` (`cc-by-nc-4.0`)
  **as-is** for V1 — non-commercial use is within its license. The #27 blocker
  is dissolved; ADR-0005 is updated to point here.

## Consequences

- **AGPL copyleft is project-wide and load-bearing:** Overwatch itself must be
  distributed under AGPL-compatible terms. Any future closed-source/commercial
  pivot requires replacing the AGPL detector (and re-clearing `cc-by-nc`
  weights). Treat this as a one-way door.
- **`cc-by-nc` "non-commercial" scope:** if a future deployment monetizes (e.g.
  a paid monitoring service, or arguably a commercial farm operation), revisit —
  `cc-by-nc` may not cover it. This ADR's clearance assumes genuinely
  non-commercial use.
- **Unblocks:** #5 (detector pick), #27 (ReID licensing — recommend closing as
  resolved-by-this-ADR), and removes the pressure behind options B/C/D of
  ADR-0005 (train-own / commercial-license / alt-backbone).
- **Docs touched:** `docs/DECISIONS/0005-reid-model-licensing.md` (Decision
  updated), `docs/SOFTWARE_STACK.md` (Models section), `configs/animals.yaml`
  (deferral note removed).
- **Follow-up actions (outward — for the PO, not done by this change):**
  1. Flip the GitHub repo to **public**.
  2. Add an **AGPL-3.0 `LICENSE`** file at the repo root.
  3. Enable **branch protection / rulesets** (now available once public).
  4. Formally **close #27** referencing this ADR.
- **Revisit when:** the project's commercial intent changes, or a deployment
  introduces a monetization path that strains the `cc-by-nc` / AGPL terms.
