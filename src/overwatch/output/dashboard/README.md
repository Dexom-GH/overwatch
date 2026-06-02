# operator dashboard

The on-site operator screen. **Interface stub in V1** — the dashboard may ship
as a thin view first and grow later.

Open question (not yet decided): web app served on-device vs a native on-device
UI. Whichever is chosen, it reads from the `EventStore` (`output/store.py`) and
should not reach into other stages directly — it is a consumer of stored
records and bus alerts, like any other sink.

No implementation yet. When built, add an ADR for the dashboard tech choice.
