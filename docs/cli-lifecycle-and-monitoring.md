# CLI, Lifecycle & Monitoring

**Source file:** `ccid/main.py` (681 lines)
**Tests:** `tests/test_main.py`

This is the process entry point — everything that turns `python -m ccid.main ...` into a running campaign: argument parsing, signal-safe shutdown, systemd watchdog integration, outbound monitoring (ntfy + Cronitor), and the glue that constructs a `Sequencer` wired to either real or simulated hardware.

---

## 1. Command-line surface

Four subcommands, all sharing top-level `--config` (default `config.yaml`) and `--log-level`:

| Command | Arguments | Purpose |
|---|---|---|
| `start` | `--target-cycles` (default 6000), `--run-id` (auto-generated timestamp if omitted) | Begin a brand-new run |
| `resume` | `--run-id`, `--latest`, `--allow-config-hash-override`, `--allow-halted-resume` | Continue an existing run directory |
| `status` | `--run-id`, `--latest` | Print current `runstate.json` as JSON, no side effects |
| `simulate` | `--target-cycles` (default 10), `--run-id`, `--scope-fault {none,never_triggered,no_trip,pretrigger_leakage}`, `--camera-fail-after` | Run entirely against sim HAL backends, with optional fault injection |

`main(argv)` parses args, loads config, builds `LifecycleSignals`/`SystemdNotifier`/`HttpNotifier`/`RunRecorder` once, then dispatches. **`status` is handled before signal handlers or the watchdog are ever installed** — it's read-only and safe to run at any time, including while another instance is actively running a campaign, so it deliberately skips the whole lifecycle-management wrapper the other three commands go through. `start`/`resume`/`simulate` are wrapped in `lifecycle.install()` / `watchdog.ready()` at the top and `watchdog.stopping()` / `lifecycle.restore()` in a `finally` at the bottom, regardless of which one runs or how it exits.

`generate_run_id()` defaults to `datetime.now(utc).strftime("%Y%m%d_%H%M%S")` — this is why `latest_run_dir` (§6) can just sort directory names lexicographically to find the most recent run.

---

## 2. Signal handling — `LifecycleSignals` / `StopRequested`

```python
class LifecycleSignals:
    def install(self):   # replaces SIGINT/SIGTERM handlers, remembers the previous ones
    def restore(self):   # puts the previous handlers back
    def check(self):     # raises StopRequested(signal_name) if a signal was caught
    def _handle(self, signum, frame):  # the actual handler: just records the signal name and logs a warning
```

The handler itself does almost nothing — it doesn't raise, doesn't unwind anything, just records which signal arrived. `check()` is what actually turns that into control flow, and it's called from exactly one place: inside `SystemdNotifier.sleep()` (§3), both before and after every sleep chunk. This means a SIGINT/SIGTERM during a long wait (cooldown, retry-cooldown, degraded-fixed-wait) is noticed at the next chunk boundary — never mid-hardware-operation, since those don't call `sleep()` with a stop-check at all. `StopRequested` (a plain `RuntimeError` subclass) is what `check()` raises, caught in exactly one place: `_execute_campaign` (§7), which responds with `safe_off` and a clean exit rather than an abandoned run.

---

## 3. Systemd integration — `SystemdNotifier`

Talks to systemd's notification socket (`NOTIFY_SOCKET` env var, `sendmsg`-style UNIX datagram — abstract-namespace sockets, whose address starts with `@`, are handled by substituting the leading null byte `_send_unix_datagram` requires) via three payloads: `READY=1` (sent once, at startup, before the campaign loop begins), `STOPPING=1` (sent once, in `main()`'s outer `finally`, regardless of how the command exited), and `WATCHDOG=1` (sent repeatedly, at half the interval systemd itself configured).

**Why half the interval**: `WATCHDOG_USEC` (systemd-supplied, matches `WatchdogSec=10` in `deploy/ccid-automation.service`) is converted to seconds and halved — "per systemd's own documented convention... pinging at the full interval leaves no margin if a single ping is ever late." If `WATCHDOG_USEC` is absent or unparseable, `_watchdog_interval_s` stays `None` and `watchdog_ping()`/the ping-splitting behavior below become no-ops — this is what makes the class work identically whether or not the process is actually running under systemd (e.g. `ccid.main simulate` from a plain shell).

**`sleep(seconds, stop_check=None)` is the single most load-bearing method here** — it's what `_execute_campaign` (§7) hands to `Sequencer` as its injected `sleep` callable, meaning *every* sleep anywhere in `Sequencer` (cooldowns, retry-cooldowns, the Entry 14 diagnostic delay, the vision-gate poll interval) actually flows through here:
```
if no watchdog interval configured: stop_check(), plain time.sleep(seconds), stop_check()
else: split `seconds` into chunks of at most `watchdog_interval_s`,
      calling stop_check() before each chunk and watchdog_ping() after each chunk,
      until the full duration has elapsed, then a final stop_check()
```
This single method is simultaneously how (a) a long sleep never causes systemd to think the process hung and restart it mid-cycle, and (b) a SIGINT/SIGTERM during a long sleep is noticed within one watchdog-interval's worth of chunk boundaries rather than only after the entire sleep completes. `test_systemd_notifier_splits_sleep_and_pings` verifies the ping count directly.

---

## 4. Outbound monitoring — `HttpNotifier`

Implements `NotificationInterface` (HAL doc §1) against two independent, unrelated services — and the module docstring is explicit that failures here **can never halt a campaign**: "every outbound call in `HttpNotifier` logs and swallows failures rather than raising."

- **`notify_start`/`notify_resume`/`notify_fault`/`notify_complete`** → ntfy.sh (`CCID_NTFY_TOPIC_URL` env var, read directly, no config.yaml indirection). Each is a POST with a `Title` header and a short plain-text body (`run=... cycle=.../...`, etc.) — push notifications to a phone, purely informational.
- **`heartbeat`/`heartbeat_fail`** → Cronitor's telemetry API (`config.yaml`'s `monitoring.cronitor_url_env`, resolved via `AppConfig.resolve_cronitor_url()` — see the persistence doc §8.4 for why only the env var *name*, never the URL itself, lives in the config object). `heartbeat` is a bare GET with `?message=run_id=...+last_completed_cycle=...` — no `state` parameter, since a plain periodic ping is what Cronitor's own expected-frequency alerting treats as "still alive." `heartbeat_fail` adds `?state=fail&message=...` — Cronitor's documented explicit-failure signal. Both go entirely through the query string, never a POST body, because Cronitor's telemetry endpoint discards POST bodies. `_http_request`'s `data` parameter defaults to `None` specifically to support this (the ntfy calls still pass real bytes as a body).

Every one of the six methods checks `if not self._cronitor_url` / `if not self._ntfy_topic_url` and returns immediately if unconfigured — both integrations are fully optional; a deployment can run with neither, either, or both env vars set. `_http_request`'s `try/except` around the actual network call is where the "never halt a campaign" guarantee is physically implemented: any exception is caught, logged as a warning with just the exception type name (not full details — avoids leaking a URL or credential into logs), and swallowed.

---

## 5. `build_hal_bundle` — real vs. sim, one mode flag at a time

Reads `config.modes.{gpio,scope,camera}_mode` **independently** — you can run, say, a real scope against simulated contactors and camera (useful for scope-only bench testing), though `ccid.main simulate`'s own guard (§6) requires all three to be `sim` together. For each of the three:
- **`gpio_mode`**: `sim` → `GpioSimContactorController`; else → `GpioRealContactorController(gpio_k1/k2/k3=config.gpio.*, output_factory=...)` (the factory injection point tests use to avoid touching real GPIO).
- **`scope_mode`**: `sim` → `ScopeSim(scenario=scope_sim_scenario or a 200kHz/50k-sample default, ...)`; else → `ScopeReal(resource=scope_resource, ...)`, which **raises `ValueError`** if `scope_resource` (from `CCID_SCOPE_RESOURCE`) is missing — a real-scope run cannot silently fall back to anything.
- **`camera_mode`**: `sim` → `CameraSim(**camera_sim_kwargs)`; else → `CameraReal(config=CameraRealConfig(device_index=config.camera.device_index), capture_factory=..., state_classifier=camera_classifier)`. This is the exact point where `ccid.classify`'s HSV logic gets wired into the real camera HAL implementation (HAL doc §4) — `camera_classifier` is threaded in from the caller, keeping `camera_real.py` itself free of any vision domain knowledge. `config.camera.device_index` is `int | str` — either a raw `/dev/videoN` index or a stable udev-provided device path (`deploy/99-c270-camera.rules` creates `/dev/ccid_camera`); `cv2.VideoCapture()` accepts both transparently, so no branching is needed here. The string form exists specifically because a raw index isn't stable across USB reconnects (HAL doc §3).

---

## 6. The command handlers

### `_cmd_start`
Generates or accepts a `run_id`, reads the raw `config.yaml` text (to freeze verbatim into the run directory), calls `recorder.initialize_run(...)`, loads the freshly-written state back (`allow_halted_resume=True` — a brand new run obviously isn't halted, this is just reusing the same loader rather than a separate code path), sends `notify_start`, and hands off to `_execute_campaign`.

### `_cmd_resume`
Resolves the run directory (`_resolve_run_dir` — by explicit `--run-id`, or `--latest` via `latest_run_dir`'s lexicographic-sort-of-directory-names, or neither → `None` → the caller raises `FileNotFoundError`). Then a fork: `--allow-config-hash-override` skips *all* validation (`read_run_state_unchecked`) — the escape hatch for a deliberate config change mid-campaign; otherwise `load_run_state` enforces both the config-hash check and the sticky-halt check (persistence doc §4.2), with `--allow-halted-resume` as the separate, narrower override for just the halt check. Calls `reconcile_orphans` (persistence doc §4.3) before resuming the campaign loop — this is the only command that does, since it's the only one resuming a possibly-crashed run.

### `_cmd_simulate`
Guards that all three HAL modes are `sim` (`raise ValueError` otherwise) — this command exists specifically for hardware-free testing/demonstration, and running it against real hardware by accident would be a meaningful footgun. Translates `--scope-fault` into `ScopeSimScenario` kwargs (`never_triggered`, `no_trip`, or `pretrigger_leakage` — note `pretrigger_leakage` also sets `no_trip=True`, since a K3-stuck-closed scenario should also never show a real collapse). Otherwise follows the same `initialize_run` → `notify_start` → `_execute_campaign` shape as `_cmd_start`, just passing the sim scenario/camera kwargs through.

### `_cmd_status`
No lifecycle wrapper, no watchdog. Resolves the run directory the same way `resume` does, reads the state **unchecked** (no config-hash/halt validation — status must be queryable regardless of what state the run is in), and prints `{"run_dir": ..., "run_state": {...}}` as JSON to stdout. Returns `1` with `{"status": "no_run_found"}` if nothing resolves — this is the one command whose failure mode is a clean, scriptable exit code rather than an exception.

---

## 7. `_execute_campaign` — the shared body

```
scope_resource = os.environ.get(CCID_SCOPE_RESOURCE)
bundle = build_hal_bundle(config, scope_resource, monotonic_now=time.monotonic, ...)
sequencer = Sequencer(..., sleep=lambda seconds: watchdog.sleep(seconds, stop_check=lifecycle.check))
try:
    bundle.camera.start()
    result = _run_campaign_with_auto_retry(sequencer, run_dir, state, notifier, watchdog, lifecycle,
                                            cooldown_retry_s=config.timing.cooldown_retry_s)
except StopRequested as exc:
    safe_off(bundle.contactors); notify_fault(...); return 130
finally:
    bundle.camera.stop()          # always
    safe_off(bundle.contactors)   # always, exceptions swallowed - this is a *second*, redundant safe_off
return _finalize_result(notifier, result)
```

Two details worth being precise about:

- **The injected `sleep` is the watchdog-aware one, not a bare `time.sleep`.** This is the actual wiring that makes §3's claim true — every sleep inside `Sequencer` (cooldown, retry-cooldown, the vision-gate poll interval, the Entry 14 diagnostic delay) goes through `SystemdNotifier.sleep`, which both pings the watchdog during long waits and checks for a pending stop signal.
- **`safe_off` is called here, in `_execute_campaign`'s `finally`, in addition to the one already inside `Sequencer.run`'s own `finally`** (sequencer doc §4.2). This looks redundant and is — deliberately. `Sequencer.run`'s `safe_off` handles the normal case; this outer one is a second, independent backstop in case `Sequencer.run` itself raised something unexpected before reaching its own `finally`, or in case `bundle.camera.stop()` (which runs first, in the same `finally` block here) somehow interfered. It's wrapped in its own bare `except Exception: pass` — this is the one place in the whole codebase where a `safe_off` failure is silently discarded rather than surfaced, because by this point in the shutdown sequence there is nothing further downstream that could act on it.

**As of the auto-retry work below, `_execute_campaign` itself no longer calls `sequencer.run()` directly** — it calls `_run_campaign_with_auto_retry`, which may invoke `sequencer.run()` more than once before this function ever sees a result. Everything else about `_execute_campaign` (the `StopRequested` handling, the double `safe_off`, the camera start/stop bracketing the whole thing) is unchanged and still wraps the *entire* auto-retry loop, not just one attempt.

---

## 8. `_run_campaign_with_auto_retry` — a halt no longer ends the campaign by itself

Added after real unattended campaigns kept stopping on the first halt of any kind — a scope timeout, a `ValueError`-class controller bug, disk space, a genuine DUT no-trip — and then just sitting idle until a human noticed and manually resumed. That idle-until-noticed gap is what was actually generating repeated Cronitor alerts on an overnight run, not the underlying faults themselves being unrecoverable.

```python
def _run_campaign_with_auto_retry(*, sequencer, run_dir, state, notifier, watchdog, lifecycle, cooldown_retry_s):
    no_trip_streak = 0
    other_streak = 0
    current_state = state
    while True:
        result = sequencer.run(run_dir=run_dir, state=current_state)
        made_progress = len(result.cycles) > 1
        if result.terminal is Terminal.COMPLETE:
            return result
        if made_progress:
            no_trip_streak = other_streak = 0
        if result.terminal is Terminal.NO_TRIP:
            no_trip_streak += 1; streak, limit = no_trip_streak, 3
        else:
            other_streak += 1; streak, limit = other_streak, 5
        if streak >= limit:
            return result   # give up for real - same exit path as before this feature existed
        watchdog.sleep(cooldown_retry_s, stop_check=lifecycle.check)
        attempted_cycle_index = result.cycles[-1].cycle_index if result.cycles else result.state.last_completed_cycle
        current_state = replace(result.state,
                                 last_completed_cycle=max(result.state.last_completed_cycle, attempted_cycle_index),
                                 halt_reason=None)
```

**The two streak limits are asymmetric on purpose.** `NO_TRIP` (the DUT genuinely failed to clear a fault within the no-trip window) gets a limit of **3**; every other non-`COMPLETE` terminal (`RIG_FAULT`, and `HALTED` with any `fault_category` — `CONTROLLER`, `PERSISTENCE`, etc.) shares a limit of **5**. The reasoning, worked through with the rig operator directly: a marginal `FAIL` near the 24.97 ms pass limit really can be phase-random luck (handoff §4's own argument — up to about one extra half-cycle of delay), and that case already auto-continues on its own without ever reaching this loop at all, since `FAIL` doesn't halt `sequencer.run()`. `NO_TRIP` sits at a 100 ms threshold specifically because that's far beyond what phase randomness alone explains — a repeated `NO_TRIP` is the DUT actually failing the thing this rig exists to test for, not a rig hiccup, so it should reach a human faster than ordinary scope/software flakiness does.

**`made_progress` is deliberately `len(result.cycles) > 1`, not a `pass_count + fail_count` diff.** `sequencer.run()` only ever returns on `Terminal.COMPLETE` or a halt — every `PASS`/`FAIL` cycle in between just continues the same call — so more than one entry in `result.cycles` means at least one cycle before this halt genuinely completed. An earlier version of this used `pass_count + fail_count` against a baseline, and that was a real bug: `RunRecorder.record_cycle` counts *any* non-`"PASS"` verdict as a fail, including a committed `NO_TRIP` — meaning a `NO_TRIP` would have looked like "progress" and silently reset its own streak, defeating the entire point of giving it a tighter limit. Found by writing the regression test, not by inspection.

**Resuming after a halt is not simply clearing `halt_reason`.** Some halts commit a full cycle record (`last_completed_cycle` advances — a sanity-check-triggered `RIG_FAULT`, or a real `NO_TRIP` verdict, both of which have an analyzed waveform); others halt before there's anything to commit (`scope_never_triggered_or_acquire_timeout`, or a `CONTROLLER` exception mid-attempt) and leave `last_completed_cycle` unchanged. In that second case, K3 may already have legitimately closed once for that `cycle_index` before the halt occurred — the charging-gate token is enforced single-use per `cycle_index` (sequencer doc §5.3), so naively retrying the *same* `cycle_index` a second time raises `SafetyViolationError`. This was a real bug caught by a real regression test, not a hypothetical: fixed by always advancing past whichever `cycle_index` was actually attempted (`result.cycles[-1].cycle_index`, populated for every attempt whether or not it committed), never just trusting `last_completed_cycle`. One visible side effect: a `cycle_index` that halts before committing anything is skipped in `cycles.csv`'s numbering rather than reused — not a new gap (that attempt never got a CSV row before this feature existed either, it just used to end the campaign instead), but worth knowing when reading a campaign's row numbers.

Intermediate retries only log (`LOGGER.warning`) — they do not call `notify_fault`/`heartbeat_fail`. You'll only hear from Cronitor once, when a streak is actually exhausted, exactly as before this feature existed. Regular per-cycle Cronitor heartbeats keep firing throughout auto-retry as long as cycles keep committing; if a `CONTROLLER`/`PERSISTENCE`-class failure repeats without ever committing a cycle, Cronitor's own missing-heartbeat grace window is a second, independent safety net on top of the 5-streak cap.

---

## 9. `_finalize_result` — the exit code contract

```
COMPLETE           → notify_complete, return 0
NO_TRIP             → notify_fault + heartbeat_fail, return 2
RIG_FAULT           → notify_fault + heartbeat_fail, return 3
anything else (HALTED: persistence/controller/peripheral)  → notify_fault + heartbeat_fail, return 4
```
(`StopRequested`, handled earlier in `_execute_campaign`, returns `130` — the conventional "terminated by signal" exit code, 128+SIGINT's number.)

This is a real operational contract, not an incidental detail — it's what `deploy/ccid-automation.service`'s `Restart=on-failure` and any wrapping operator scripts key off of to distinguish "the campaign finished" from "a DUT genuinely failed" from "the rig itself has a problem" from "something else broke," without needing to parse log text. Both `notify_fault` (ntfy push) and `heartbeat_fail` (Cronitor explicit-failure ping) fire together on every non-COMPLETE, non-signal exit — the two monitoring channels are meant to be redundant with each other for anything that counts as a real halt. **This only ever sees the *final* result of `_run_campaign_with_auto_retry` (§8)** — a streak that gets auto-retried and later recovers never reaches this function at all; only an exhausted streak or a genuine `COMPLETE` does.

---

## 10. Test coverage map

| Behavior | Test(s) |
|---|---|
| HAL bundle mode selection | `test_build_hal_bundle_selects_sim_backends` |
| `latest_run_dir` picks the lexicographically-highest directory name | `test_latest_run_dir_picks_highest_name` |
| Watchdog sleep-splitting and ping cadence | `test_systemd_notifier_splits_sleep_and_pings` |
| Cronitor heartbeat (bare ping, no state param), heartbeat-fail (`state=fail`), both-unconfigured no-op | `test_http_notifier_heartbeat_pings_cronitor_with_no_state`, `test_http_notifier_heartbeat_fail_sets_cronitor_fail_state`, `test_http_notifier_skips_cronitor_when_unconfigured` |
| `start` initializes a run and executes the campaign | `test_cmd_start_initializes_run_then_executes` |
| `resume --allow-config-hash-override` bypasses hash validation | `test_cmd_resume_allows_config_hash_override` |
| `status` is side-effect-free and reports current state | `test_cmd_status_is_safe_and_reports_runstate` |
| Best-effort monitoring never raises even when the transport does (fault-matrix row) | `tests/test_faultmatrix.py::test_cronitor_request_failure_is_swallowed_row` |
| Auto-retry recovers after transient `RIG_FAULT`s, without reusing a `cycle_index` that already closed K3 | `test_auto_retry_recovers_after_transient_rig_faults` |
| Auto-retry gives up after exactly 5 consecutive `RIG_FAULT`/`CONTROLLER`-class halts | `test_auto_retry_gives_up_after_five_consecutive_rig_faults` |
| Auto-retry gives up after exactly 3 consecutive `NO_TRIP`s (the tighter limit) | `test_auto_retry_gives_up_after_three_consecutive_no_trips` |
| The streak resets on any cycle that actually completes, not just on a fixed count | `test_auto_retry_streak_resets_on_a_completed_cycle` |
| A stop request arriving during a retry cooldown propagates rather than being swallowed | `test_stop_requested_during_retry_cooldown_propagates` |
| Periodic equipment refresh fires at the configured interval, camera/scope call counts | `test_equipment_refresh_fires_at_every_configured_interval`, `test_equipment_refresh_disabled_by_default` |
| Reactive equipment refresh fires after N consecutive camera-unavailable cycles, independent of the fixed schedule | `test_equipment_refresh_reactive_trigger_fires_on_consecutive_camera_unavailable`, `test_equipment_refresh_reactive_trigger_disabled_by_default` |
| A refresh failure is logged and swallowed rather than crashing the cycle | `test_equipment_refresh_failure_does_not_crash_the_cycle` |

---

## 11. Things to know if you're about to change this file

- **Any new long-running wait inside `Sequencer` should go through the injected `sleep`, not a bare `time.sleep`** — bypassing it silently drops both the watchdog ping and the signal-check behavior for that specific wait.
- If you add a fifth CLI command, decide deliberately whether it needs the full lifecycle wrapper (signal handling + watchdog) or belongs with `status` outside it — that choice should track whether the command can energize anything, not just convenience.
- The exit-code contract (§9) is a real interface other things depend on (the systemd unit, any operator scripts) — don't repurpose an exit code without checking what currently keys off it. It only ever reflects the *final* outcome of auto-retry (§8), not every individual halt along the way.
- `_execute_campaign`'s double `safe_off` (§7) is intentional defense in depth, not a bug to "clean up" — removing the outer one would remove the only backstop against a failure mode inside `Sequencer.run` that somehow escapes its own `finally`.
- **If you add a new halt category, decide deliberately which streak it belongs to** (§8) — the default for anything that isn't `NO_TRIP` is the 5-limit shared bucket, which is appropriate for "probably transient" failures but not for anything that represents the DUT itself failing.
- Equipment refresh (`Sequencer._maybe_refresh_equipment`/`_refresh_equipment`, sequencer doc) lives in `Sequencer.run()`, not here — `_execute_campaign` only owns the camera's initial `start()`/final `stop()` bracketing the whole campaign; the periodic/reactive mid-campaign refreshes are the sequencer's own responsibility, since it's the one that knows the current `cycle_index` and the per-cycle degraded-flag history.
