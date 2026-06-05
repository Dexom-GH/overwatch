# Storage growth & data-retention policy (#40)

The Jetson has a **512 GB NVMe**. Overwatch runs **24/7**, so three data streams
grow continuously and must be bounded or the device fills and goes down:

1. **EventStore** (SQLite durable tier, ADR-0001) — `ZoneCount` / `HealthSignal` /
   `Event` / `Alert` rows.
2. **Recorded clips** (`.owrec`, #11) — RGB+depth capture for offline iteration.
3. **Saved crops** — ReID crops kept on-demand (ADR-0003).

This policy bounds all three. The logic is host-tested
([`output/retention.py`](../src/overwatch/output/retention.py)); the **per-day byte
figures below are placeholders to confirm on-device** (real `.owrec` and crop
sizes depend on the ZED resolution/FPS finalized in #46/#14).

## Growth budget (placeholders — confirm on device)

| Stream | Unit size (est.) | Rate (est.) | Per day (est.) | Bound by |
|---|---|---|---|---|
| EventStore rows | ~0.3 KB/row | ~1–10 rows/s aggregate | ~25–250 MB/day | `output.store.retention` (age/rows) |
| Recorded clips | ~depends on res/fps | only when recording | **the dominant term** | `RetentionPolicy(max_total_bytes)` on the recordings dir |
| Saved crops | ~30 KB/crop | bounded by ReID dispatch rate (ADR-0003) | small | `RetentionPolicy(max_total_bytes)` on the crops dir |

**Sizing rule:** pick a per-stream byte budget so the **sum stays under a safe
fraction of 512 GB** (target ≤ ~60% = ~300 GB, leaving headroom for the OS,
models, and spikes). Recorded clips dominate, so their directory budget is the
main knob; EventStore rows are tiny and bounded by age.

## Enforcement

- **EventStore:** `enforce_event_store(store, policy, now=...)` applies both
  budgets — it age-prunes via `EventStore.prune(before)` (cutoff from
  `policy.age_cutoff(now)`) **and** enforces the global row cap via
  `EventStore.prune_to_max_rows(max_rows)` (newest rows survive). Build the policy
  straight from config with `RetentionPolicy.from_config(cfg.output.store.retention)`
  (`max_age_days` → seconds, `max_rows` → row cap). Default: **90 days**, no row cap.
- **Recordings & crops:** `enforce_directory(dir, RetentionPolicy(...), now=...)`
  deletes files **oldest-first** once the directory exceeds its age/size/count
  budget. (The recordings/crops directory byte budgets are configured per-directory
  when those features land — #11 recordings, the ReID crop path; the policy +
  enforcement are ready now.)
- **When it runs:** the supervised **`RetentionStage`** (`app.py`, #106) sweeps the
  EventStore every `output.store.retention.interval_seconds` (default hourly),
  calling `enforce_event_store` with the config-derived policy; it joins the
  pipeline's clean shutdown. Directory enforcement (`enforce_directory` for the
  recordings/crops dirs) extends the same stage once those dirs are configured
  (#11 recordings, the ReID crop path).

## On-device verification (target — deferred)

- [ ] Measure real `.owrec` bytes/min and crop bytes at the finalized ZED
      resolution/FPS; replace the placeholder figures above.
- [ ] Confirm a multi-day run stays under the ~300 GB target with these defaults.
- [ ] Tune `max_age_days` / directory byte budgets from the measured rates.
