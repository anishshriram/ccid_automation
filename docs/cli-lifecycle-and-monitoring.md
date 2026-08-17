# CLI, Lifecycle & Monitoring

**Source file:** `ccid/main.py` (561 lines)
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
- **`camera_mode`**: `sim` → `CameraSim(**camera_sim_kwargs)`; else → `CameraReal(config=CameraRealConfig(device_index=config.camera.device_index), capture_factory=..., state_classifier=camera_classifier)`. This is the exact point where `ccid.classify`'s HSV logic gets wired into the real camera HAL implementation (HAL doc §4) — `camera_classifier` is threaded in from the caller, keeping `camera_real.py` itself free of any vision domain knowledge.

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
    result = sequencer.run(run_dir, state)
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

---

## 8. `_finalize_result` — the exit code contract

```
COMPLETE           → notify_complete, return 0
NO_TRIP             → notify_fault + heartbeat_fail, return 2
RIG_FAULT           → notify_fault + heartbeat_fail, return 3
anything else (HALTED: persistence/controller/peripheral)  → notify_fault + heartbeat_fail, return 4
```
(`StopRequested`, handled earlier in `_execute_campaign`, returns `130` — the conventional "terminated by signal" exit code, 128+SIGINT's number.)

This is a real operational contract, not an incidental detail — it's what `deploy/ccid-automation.service`'s `Restart=on-failure` and any wrapping operator scripts key off of to distinguish "the campaign finished" from "a DUT genuinely failed" from "the rig itself has a problem" from "something else broke," without needing to parse log text. Both `notify_fault` (ntfy push) and `heartbeat_fail` (Cronitor explicit-failure ping) fire together on every non-COMPLETE, non-signal exit — the two monitoring channels are meant to be redundant with each other for anything that counts as a real halt.

---

## 9. Test coverage map

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

---

## 10. Things to know if you're about to change this file

- **Any new long-running wait inside `Sequencer` should go through the injected `sleep`, not a bare `time.sleep`** — bypassing it silently drops both the watchdog ping and the signal-check behavior for that specific wait.
- If you add a fifth CLI command, decide deliberately whether it needs the full lifecycle wrapper (signal handling + watchdog) or belongs with `status` outside it — that choice should track whether the command can energize anything, not just convenience.
- The exit-code contract (§8) is a real interface other things depend on (the systemd unit, any operator scripts) — don't repurpose an exit code without checking what currently keys off it.
- `_execute_campaign`'s double `safe_off` (§7) is intentional defense in depth, not a bug to "clean up" — removing the outer one would remove the only backstop against a failure mode inside `Sequencer.run` that somehow escapes its own `finally`.
