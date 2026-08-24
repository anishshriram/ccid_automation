# Hardware Abstraction Layer

**Source files:** `ccid/hal/base.py` (399), `ccid/hal/scope_real.py` (549), `ccid/hal/scope_sim.py` (328), `ccid/hal/gpio_real.py` (195), `ccid/hal/gpio_sim.py` (153), `ccid/hal/camera_real.py` (198), `ccid/hal/camera_sim.py` (214)
**Tests:** `test_scope_real.py`, `test_scope_sim.py`, `test_scope_protocol.py`, `test_gpio_real.py`, `test_safety.py` (gpio_sim's interlocks), `test_camera_real.py`, `test_camera_sim.py`

The HAL is why `Sequencer` (its own doc) is fully unit-testable without any real hardware in the loop: every hardware boundary — scope, contactors, camera — is one `ABC` in `base.py` with exactly two implementations each, a `*_real` one that talks to actual instruments and a `*_sim` one that's deterministic and fast. `Sequencer` only ever holds a reference to the interface type; it cannot tell which implementation it was given.

---

## 1. `ccid/hal/base.py` — the contracts

Four abstract interfaces, each documented with an unusually formal structure (Preconditions / Postconditions / Exceptions / Timeout behavior / I/O / Retry safety) — this is deliberate, not decorative: it's the actual contract both implementations are held to, and what `test_scope_protocol.py`'s `HalProtocolTests` checks fakes against.

### `ContactorInterface`
`close_k1/k2/k3`, `open_k1/k2/k3`, `safe_open_all`, `snapshot() -> ContactorSnapshot`, `detect_mains_command_mismatch(allowed_stagger_ms, now_monotonic_s) -> bool`. The docstring states the safety preconditions explicitly as part of the contract: "`close_k3` requires a valid charging gate token"; "`open_k1`/`open_k2` must reject commands while K3 is commanded closed." Both real and sim implementations enforce these identically (§2).

### `ScopeInterface`
`connect`/`disconnect`/`identify`/`configure_for_cycle`/`readback_settings`/`arm_single`/`wait_until_armed`/`wait_until_acquisition_complete`/`read_trigger_event_register`/`read_operation_condition`/`force_trigger`/`capture_after_acquire`/`capture_timeout_diagnostics`/`status`. Three of these methods carry long docstrings directly on the abstract method (not just in the implementation) because they encode hard-won, safety-relevant facts:

- **`read_trigger_event_register`**: "SCPI convention is read-and-clear... Only for live pre-injection checkpoints... Must not be polled inside the acquisition-timeout/backstop loop" — this is the interface itself documenting the constraint that shaped `Sequencer`'s design (see the sequencer doc §5.4–5.5).
- **`read_operation_condition`**: explicitly contrasted with the above — "a condition register, not an event register... safe to call at arbitrary diagnostic checkpoints without disturbing other state." This is *why* `_record_diagnostic_stage` (sequencer doc §7) can call this freely but must never call `read_trigger_event_register` on its own.
- **`force_trigger`**: "Diagnostic use only: this consumes the same single-shot acquisition a real measurement would use... must never be used for PASS/FAIL or trip-time calculation."

### `CameraInterface`
`start`/`stop`/`sample_state(now_monotonic_s) -> CameraStateSample`/`await_charging_gate(cycle_index, timeout_s, now_monotonic_s) -> (ChargingGateToken | None, CameraStateSample)`/`latest_frame`.

**Worth knowing precisely**: `await_charging_gate` is implemented by both `CameraReal` and `CameraSim` (§4), and is directly unit-tested in `test_camera_sim.py` — but **`Sequencer` never calls it**. The actual charging-gate wait used in production is `ccid.classify.await_charging_gate` (a module-level function in a different file, the vision/classification doc's subject), which builds its own gate policy (green-frame windows, required-frame counts, degraded handling) on top of repeated calls to `camera.sample_state()` alone. The HAL method exists as part of the formal interface contract and works correctly in isolation, but if you're tracing "where does the real gate decision happen," it's in `classify.py`, not here.

### `NotificationInterface`
`notify_start/resume/fault/complete`, `heartbeat`, `heartbeat_fail`. Implemented by `HttpNotifier` in `ccid/main.py` (own doc), not in `ccid/hal/` — it's grouped with the other interfaces here because it shares the same ABC pattern, but its concrete implementation lives with the CLI/lifecycle code since that's where it's constructed and wired to Cronitor/ntfy.

### Supporting dataclasses (all frozen)
`ContactorName` (K1/K2/K3 enum), `ScopeStatus` (DISCONNECTED → CONNECTED → CONFIGURED → ARMING → ARMED → ACQUIRING → COMPLETE), `CameraHealth` (HEALTHY/STALE/FAILED), `ContactorSnapshot`, `ChargingGateToken`, `WaveformCapture`, `ScopeTimeoutDiagnostics`, `CameraFrame`, `CameraStateSample`.

**`ScopeSettings`** deserves its own note — this is the single source of truth for every scope configuration value, and its defaults *are* the locked spec (50ms/div time base, CENTER reference, ±... V/div on CH1 with AC coupling and a 10:1 probe, EDGE trigger on CH1 at +20.0V positive slope, **DC trigger coupling** — the one field with an inline comment explaining why it must not be AC: "the trigger comparator must see the raw absolute voltage for a one-shot transient against a fixed level - AC-coupling the trigger path... high-pass filters the signal before comparison, so the effective 0V reference drifts with recent signal history instead of staying fixed"). `waveform_points_mode: RAW` + `waveform_points: MAXimum` is the fix for a real documented trap (§3.1).

---

## 2. Contactors — `gpio_real.py` / `gpio_sim.py`

Both implementations enforce **identical** interlocks, independently, in the domain layer rather than relying on the sequencer to get the order right (see the sequencer doc §1 for why):

```python
close_k3(gate):
    if gate.cycle_index already used this controller instance: raise SafetyViolationError
    if not (K1 commanded closed and K2 commanded closed): raise SafetyViolationError
    # only then actually close K3, and mark this cycle_index's token consumed

open_k1() / open_k2():
    if K3 commanded closed: raise SafetyViolationError
```

`GpioRealContactorController.__init__` additionally calls `safe_open_all()` at the end of construction — "Safety default: all outputs inactive on startup," so the moment the object exists, hardware state is known-safe regardless of whatever GPIO pins previously held.

**`detect_mains_command_mismatch(allowed_stagger_ms, now_monotonic_s)`** — the same algorithm in both implementations, a small stateful debouncer:
```python
mismatch = (K1 commanded) != (K2 commanded)
if not mismatch: reset the mismatch-start timestamp, return False
if this is the first mismatched observation: record now as the start; return True immediately only if allowed_stagger_ms == 0
otherwise: return True once elapsed time since the mismatch started exceeds allowed_stagger_ms
```
With `mains_stagger_ms: 0` in `config.yaml`, this collapses to "any observed mismatch is reported immediately" — the grace-window logic exists for a deployment that needs a brief allowance for genuinely staggered mains contactors, which this rig currently doesn't.

**Differences between real and sim:**
- `GpioRealContactorController` talks to `gpiozero.DigitalOutputDevice` (constructor-injectable via `output_factory` for tests), requires all three GPIO numbers to be distinct, and every command wraps the actual `.on()`/`.off()` call in try/except — a hardware I/O failure raises `GpioRealError` *after* logging a failed `RealContactorEvent`, so the failure is both raised to the caller and recorded in the event log.
- `GpioSimContactorController` has no hardware to fail against (failures are opt-in via `inject_failure(operation, count)`, consumed one at a time, raising `DeterministicCommandError` — used to test the sequencer's fault-handling paths deterministically) and additionally tracks `recent_open_order(count=3)` — a small helper that lets tests directly assert the K3→K2→K1 opening order actually happened, by name, rather than inferring it from timing.

Every command on both implementations appends a `SimContactorEvent`/`RealContactorEvent` (operation, contactor, commanded state, timestamp, success, detail) to an internal log — `events()` exposes this, and it's what `test_faultmatrix.py`'s opening-order assertions and the sim's crash-injection tooling (`tools/simulate.py`) read.

---

## 3. Oscilloscope — `scope_real.py` / `scope_sim.py`

### 3.1 `ScopeReal.configure_for_cycle` — command ordering matters

Sends `:STOP` first, waits (bounded, 1s) for the run bit to actually clear (`_wait_until_stopped`) — configuring a still-running scope is not attempted. Then a fixed sequence of SCPI writes, in an order that's commented precisely because getting it wrong produces *silently* wrong results:

- **`:CHANnel1:PROBe` before `:CHANnel1:SCALe`/`:OFFSet`** — those are interpreted "at the probe tip" using whatever probe ratio is *already* configured. Set scale first and it gets silently reinterpreted once probe ratio lands afterward — readback looks correct, the actual digitized range is off by the probe factor.
- **`:TRIGger:MODE EDGE` before the `:TRIGger:EDGE:*` parameters** — those parameters are inert unless mode is explicitly EDGE; the scope otherwise keeps triggering on whatever mode (Pattern, Glitch, etc.) was last left on via the front panel.
- **`:TRIGger:COUPling` is set explicitly**, separate from `channel1_coupling` — it controls what the trigger comparator sees, not what gets digitized, and is left at whatever the front panel had unless set.

After every write: `*OPC?` — a synchronization barrier that blocks until every queued command has actually finished executing, not just been sent. Without it, `arm_single()` (called immediately after this method returns, from the sequencer) could race ahead of the scope still internalizing the last config commands.

Then `_drain_configuration_errors()` reads `:SYSTem:ERRor?` in a bounded loop (max 20) until it sees `+0,...` (no error) or the budget runs out, collecting any rejected commands. **Any nonzero error here raises `ScopeConfigurationError`** — a partially-applied configuration is never silently proceeded past; the sequencer's exception handling (its `_run_cycle` five-way `except` — see that doc §4.3) catches this as a `CcidError` and halts before arm/inject can ever happen.

`:WAVeform:POINts:MODE` defaults to `RAW` + `POINts MAXimum` specifically because `NORMal` mode "returns ~1000 points regardless of 1 Mpts in memory" (per `handoff_latest.md`'s trap table) — every capture would otherwise be silently truncated to a tiny fraction of the actual acquisition depth.

### 3.2 Arming and completion — polling the run bit, never sleep-based sync

```python
_run_bit_set() = bool(read_operation_condition() & (1 << 3))
```
`wait_until_armed`/`wait_until_acquisition_complete` both poll this bit (10ms cadence) against a caller-supplied deadline rather than ever using a fixed `time.sleep()` to guess timing — a documented trap: "sleep-based sync works for 50 cycles then corrupts data at cycle 3000" (`handoff_latest.md`). `wait_until_armed` returns `True` the moment the bit sets; `wait_until_acquisition_complete` returns `True` the moment it *clears* (acquisition subsystem stopped running).

### 3.3 `capture_timeout_diagnostics` — the most defensively written method in the codebase

This exists to gather a best-effort snapshot after the scope has already failed to complete an acquisition — i.e., precisely the moment the scope is most likely to be in a bad state. Every design choice here traces back to a specific real-hardware incident recorded in `scope-trigger-debug-log.md`:

- **No background thread for timeout enforcement.** An earlier version used a daemon thread to bound a hung query's wall-clock time; on real hardware, the abandoned thread stayed blocked inside `libusb` after the caller gave up waiting, and a subsequent main-thread operation raced with it and **segfaulted the process** (Entry 6). The fix: every query here is bounded only by PyVISA's *native* per-resource `.timeout`, set once up front (`self._inst.timeout = diagnostics_query_timeout_s * 1000.0`) — this bounds the actual transport call at the libusb/kernel level, not just how long Python waits for it.
- **Fail-fast, not best-effort-per-field.** The first query failure aborts the *entire* remaining capture and permanently marks the connection `_connection_unusable` for the rest of the process (no further operation, including a later `disconnect()`'s own `.close()`, is attempted against it). Why so aggressive: Entry 3 showed that continuing to push more queries after one has already failed can cascade into a fully wedged USBTMC session — a stale unread response left in the instrument's output buffer desyncs every subsequent write/read pair, and in that incident recovery required physically power-cycling the scope.
- **One deliberate exception to fail-fast**: `.clear()` failing with `VI_ERROR_NSUP_OPER` is *not* treated as connection-unhealthy — confirmed on real hardware (Entry 5) to just mean this PyVISA backend doesn't implement device clear at all, which is a backend limitation, not evidence of a wedged transport. Diagnostics proceeds normally in that specific case.
- **`:WAVeform:POINts?` is deliberately excluded** from the diagnostic settings queries — on a real dry run it took over a second to respond while every other query answered immediately (Entry 6). Not essential (the mode/format/source fields already describe the waveform subsystem's configuration), so it isn't worth being the one query that stalls or further destabilizes an already-troubled transport.
- Bounded by both a per-query timeout and a total wall-clock budget (`diagnostics_total_budget_s`, 5.0s default) — the loop checks the deadline before every individual query, not just once at the start.

If the scope is already known unusable or was never connected, this returns an immediate best-effort bundle (`operation_condition=-1`, a descriptive `settings` string) without attempting any I/O at all.

### 3.4 `_retry_io` — used only for the "normal" operations, never for diagnostics

`_write`/`_query`/`_query_binary` (used by `configure_for_cycle`, `arm_single`, `capture_after_acquire`, etc.) go through `_retry_io`, which retries a failed call up to `reconnect_attempts` (default 3) times, reconnecting the VISA resource between attempts. `capture_timeout_diagnostics` deliberately bypasses all three of these — a scope that just failed to report acquisition-complete is plausibly wedged, and reconnect-on-failure could add many seconds of blocking reconnect attempts stacked across ~18 sequential diagnostic queries, which is exactly the kind of delay the fail-fast design above exists to avoid.

### 3.5 `ScopeSim` — deterministic waveform synthesis

`ScopeSimScenario` is a large frozen dataclass covering every fault axis the fault-matrix needs: `no_trip`, `never_triggered`, `pretrigger_leakage`, `arm_delay_s`/`acquisition_delay_s` (simulate slow hardware), `transfer_truncated`, `invalid_preamble`/`missing_preamble_fields`, `force_comm_errors` (a set of operation names that raise `ScopeSimCommunicationError` when invoked — used to test the real scope's retry/reconnect paths without real hardware), plus the three TER-related scenario flags already covered in the sequencer doc (§5.4's tests): `trigger_event_latched_at_configure` (self-clears on first read — models stale residue), `trigger_event_stuck_at_configure` (does not self-clear — models an active problem), `trigger_event_latched_before_injection`.

`_build_samples` synthesizes a literal AC sine burst sample-by-sample: for each sample time `t = i/sample_rate - pretrigger`, conduction is present if `t >= 0` and (`no_trip` is set, or `t <= trip_time`), or if `pretrigger_leakage` is set and `t < 0`. The voltage is `amplitude * sin(2π·freq·t + phase)` when conducting, `0.0` otherwise, quantized to an 8-bit `BYTE` code around a 128 midpoint over a 200V full-scale range — deliberately mirroring the real scope's own `BYTE`-format encoding (§3 of the loading logic in the analysis doc) so the exact same `load_waveform`/`_scale_samples` path exercises both real and simulated data identically.

`force_trigger()` sets `_force_triggered = True` **and immediately sets `status = COMPLETE`** — this is a load-bearing detail, not an afterthought: real `:TRIGger:FORCe` is synchronous (the acquisition completes as part of issuing the command), and since a caller that forces a trigger correctly stops calling `wait_until_acquisition_complete()` afterward (sequencer doc §5.7.1), nothing else would ever transition the sim's status to `COMPLETE` if this method didn't do it itself — an earlier version of this sim didn't, which is exactly the Entry 13 bug the sequencer doc's regression tests guard against.

---

## 4. Camera — `camera_real.py` / `camera_sim.py`

### `CameraReal` — bounded reader thread

Runs a dedicated daemon thread (`_reader_loop`) that continuously calls `cv2.VideoCapture.read()` and stores the latest frame under a lock — `sample_state()` never blocks on hardware I/O itself, it just reads whatever the reader thread most recently produced. Three layers of failure handling:
- **Per-read failures** increment `_consecutive_read_failures`; once that reaches `read_fail_limit` (15), `sample_state` reports `LedState.CAMERA_UNAVAILABLE` / `CameraHealth.FAILED` — this is what feeds the sequencer's degrade-and-continue path (sequencer doc §5.3).
- **Staleness**: even with a healthy reader thread, if the most recent frame is older than `stale_after_s` (1.0s) relative to the caller's `now_monotonic_s`, `sample_state` reports `OFF_OR_UNKNOWN`/`STALE` rather than classifying a stale frame as if it were current.
- **Classification is injected**, not implemented here — `state_classifier: Callable[[bytes, int, int], LedState]`, defaulting to a constant `OFF_OR_UNKNOWN` if none is supplied. `Sequencer` wires this to `ccid.classify`'s HSV logic at construction (`build_hal_bundle` in `main.py`). This keeps `camera_real.py` doing only hardware I/O — no HSV, no ROI, no domain knowledge of what a "charging" LED looks like.

A `capture_factory` can be injected (tests do this) to stand in for `cv2.VideoCapture` entirely — when injected, the real `cv2`-specific width/height/fps configuration calls are deliberately skipped, since there's no real capture device to configure.

**`CameraRealConfig.device_index` is `int | str`**, not just `int` — `cv2.VideoCapture()` accepts either a raw numeric index or a device path string natively, so no branching was needed to support this. Added after a real incident: a hardcoded numeric `/dev/videoN` index isn't stable across USB reconnects (the camera re-enumerated from `video0` to `video1`/`video2` mid-campaign, and the hardcoded index kept pointing at nothing, producing immediate `CAMERA_UNAVAILABLE`). The recommended value is now a udev-provided stable symlink (`deploy/99-c270-camera.rules` creates `/dev/ccid_camera`, matched on the camera's own USB serial rather than enumeration order — the same pattern already used for the scope in `deploy/99-keysight-usbtmc.rules`), with a plain int index still accepted for environments without that rule installed. `start()`/`stop()` fully close and reopen `cv2.VideoCapture` and spawn a fresh reader thread each time — nothing gates this to "only usable once at process startup," which is what makes `Sequencer`'s periodic equipment refresh (sequencer doc §9) safe to call repeatedly mid-campaign. One caveat worth knowing: `stop()`'s thread join is bounded (`timeout=1.0`) and proceeds regardless of whether the thread actually terminated — if the underlying `cap.read()` call is itself wedged, a `stop()`/`start()` cycle may not fully clear it, since nothing here can forcibly kill a blocked C-level read.

### `CameraSim` — fixture/replay based

Two modes, chosen at construction: a JSON **replay file** (`_load_replay_file` — a list of `{led_state, frame_bgr_base64, width, height}` objects, validated field-by-field with a specific error message for each malformed case) or an in-memory **fixture sequence**. If neither is given, `_default_fixtures()` provides a small deterministic progression: BOOTING (orange) → BOOTING (red, i.e. a different hue while still booting) → READY (orange) → CHARGING (green) — enough to exercise a full realistic gate-wait sequence without any real footage.

`sample_state` walks the fixture list forward by one on each call (clamped at the last fixture — it doesn't loop or run out), tagging each returned frame's metadata with `source: "replay"` or `"fixture"` so a consumer can tell real footage from synthetic data. `fail_after_samples` optionally forces `CAMERA_UNAVAILABLE` after N calls, mirroring `CameraReal`'s failure-count behavior for testing the same degrade path.

---

## 5. Test coverage map

| Behavior | Test(s) |
|---|---|
| Full protocol conformance (fakes bind to all 4 ABCs) | `test_scope_protocol.py::HalProtocolTests` |
| Scope config command ordering (stop-wait, probe-before-scale, mode-before-edge, trigger coupling) | `test_configure_waits_until_scope_is_stopped`, `test_configure_sets_trigger_mode_edge_before_edge_parameters`, `test_configure_sets_probe_ratio_before_scale_and_offset`, `test_configure_sets_trigger_coupling_dc` |
| OPC sync barrier, config error rejection/draining | `test_configure_sends_opc_sync_barrier_after_commands`, `test_configure_raises_when_scope_rejects_a_configuration_command`, `test_configure_raises_with_all_rejected_commands_listed`, `test_configure_error_drain_is_bounded_and_still_raises`, `test_configure_does_not_raise_when_error_queue_is_clean` |
| TER / operation-condition semantics (read-and-clear vs side-effect-free) | `test_read_trigger_event_register_false_when_clear`, `test_read_trigger_event_register_true_when_latched`, `test_read_operation_condition_is_side_effect_free_across_repeats` |
| `force_trigger` sends the documented command | `test_force_trigger_sends_documented_command` |
| Timeout diagnostics: no writes, no background threads, fail-fast on first failure, device-clear-unsupported tolerance, time-budget enforcement, disconnected-state bundle | The whole `test_timeout_diagnostics_*` block in `test_scope_real.py` (18 tests) |
| Preamble parsing | `test_parse_preamble` |
| Sim: normal/never-triggered/no-trip/pretrigger-leakage waveform synthesis, transfer truncation, invalid/missing preamble fields, comm-error injection, all three TER scenario flags, force-trigger status transitions (including reset on next configure) | `test_scope_sim.py::ScopeSimTests` |
| Contactor interlocks (K3-requires-K1&K2, K1/K2-blocked-while-K3-closed, single-use gate token) | `test_safety.py` (§ referenced in the sequencer doc) |
| Real-vs-sim contactor behavioral parity, mismatch-detector stagger window | `test_gpio_real.py::GpioRealTests` |
| Camera: healthy-frame classification, reader-failure → degrade path | `test_camera_real.py::CameraRealTests` |
| Camera sim: default fixture progression reaches CHARGING (both directly and through the real optical gate policy), failure path, replay-file loading and validation | `test_camera_sim.py::CameraSimTests` |

---

## 6. Things to know if you're about to change this layer

- **Real and sim implementations must stay behaviorally identical on everything the sequencer depends on** — not just method signatures. The Entry 13 `force_trigger` status-transition bug (§3.5) was exactly a sim/real behavioral mismatch that a naive "does it implement the interface" check wouldn't have caught.
- If you add a new `ScopeInterface` method that reads scope state, decide immediately whether it's an *event* register (read-and-clear, like `:TER?`) or a *condition* register (side-effect-free, like `:OPERegister:CONDition?`) and document it on the abstract method the same way the existing two are — this distinction is safety-relevant, not cosmetic (sequencer doc §7).
- Any new `ScopeReal` diagnostic query added to `capture_timeout_diagnostics`'s path should be added to `_DIAGNOSTIC_SETTINGS_QUERIES` and must be one that's been confirmed not to stall on real hardware — or it risks becoming the next Entry 6.
- If you ever wire `CameraInterface.await_charging_gate` into the real production path (instead of `classify.await_charging_gate`), be aware both currently exist and behave similarly but not necessarily identically — verify which gate policy you actually want before switching.
