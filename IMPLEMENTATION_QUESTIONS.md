# Implementation Questions

Material unresolved items only, per `coding_instructions.txt` section 2. Routine implementation
choices are not recorded here.

## Open

### 1. K1/K2 physical-state readback (handoff open hardware item 16)

The software tracks only commanded contactor state (`ContactorInterface.snapshot()`); there is no
auxiliary-contact or voltage readback confirming K1/K2 physically opened or closed. A K1/K2 stuck
physically closed while commanded open is an explicitly undetectable known gap
(`coding_instructions.txt` section 7, fault-matrix row) — covered by a skipped, documented test in
`tests/test_faultmatrix.py::test_k1_k2_physically_stuck_closed_row` rather than a false unit test.

`coding_instructions.txt` section 13 requires this item be "explicitly accepted or resolved before
Stage 6." No decision has been made either way. Resolving it means adding physical-state sensing
hardware (auxiliary contacts or voltage sensing on the load side) and a corresponding HAL method;
accepting it means recording that decision here and in the commissioning sign-off before Stage 6.

### 2. UL 2231-2 endpoint definition confirmation

`config.yaml`'s `analysis.endpoint_definition` (currently the `v2` text) is the project's own
provisional definition of the trip-time measurement endpoints, used because UL 2231-2 section 23.3.1
has not yet been confirmed against the actual standard on paper. `ccid/analysis.py`'s module
docstring already states this explicitly and the definition is frozen by the config hash so it
cannot drift silently, but the underlying question — does UL 2231-2 define these endpoints
differently — remains open.

## Resolved this session

- Camera `device_index` was previously hardcoded with no `config.yaml` path — now configurable via
  the `camera:` section (deployment-specific, unlike VISA `backend`/`reconnect_attempts` or GPIO
  `active_high`/`initial_value`, which stay hardcoded because the spec locks them).
- "Disk below 2 GB" fault-matrix row was unimplemented — now enforced via `paths.min_free_disk_gb`
  and `Sequencer._assert_sufficient_disk_space`.
