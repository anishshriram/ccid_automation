from __future__ import annotations

from datetime import datetime, timezone
import unittest

from ccid.hal.base import (
    CameraFrame,
    CameraHealth,
    CameraInterface,
    CameraStateSample,
    ChargingGateToken,
    ContactorInterface,
    ContactorName,
    ContactorSnapshot,
    NotificationInterface,
    ScopeInterface,
    ScopeSettings,
    ScopeStatus,
    WaveformCapture,
)
from ccid.states import LedState


class _FakeContactors(ContactorInterface):
    def __init__(self) -> None:
        self._state = {
            ContactorName.K1: False,
            ContactorName.K2: False,
            ContactorName.K3: False,
        }

    def close_k1(self) -> None:
        self._state[ContactorName.K1] = True

    def close_k2(self) -> None:
        self._state[ContactorName.K2] = True

    def close_k3(self, gate: ChargingGateToken) -> None:
        del gate
        self._state[ContactorName.K3] = True

    def open_k1(self) -> None:
        self._state[ContactorName.K1] = False

    def open_k2(self) -> None:
        self._state[ContactorName.K2] = False

    def open_k3(self) -> None:
        self._state[ContactorName.K3] = False

    def safe_open_all(self) -> None:
        self.open_k3()
        self.open_k2()
        self.open_k1()

    def snapshot(self) -> ContactorSnapshot:
        return ContactorSnapshot(commanded_closed=dict(self._state), captured_at_monotonic_s=1.0)

    def detect_mains_command_mismatch(self, allowed_stagger_ms: int, now_monotonic_s: float) -> bool:
        del allowed_stagger_ms, now_monotonic_s
        return self._state[ContactorName.K1] != self._state[ContactorName.K2]


class _FakeScope(ScopeInterface):
    def __init__(self) -> None:
        self._status = ScopeStatus.DISCONNECTED
        self._settings = ScopeSettings()

    def connect(self) -> None:
        self._status = ScopeStatus.CONNECTED

    def disconnect(self) -> None:
        self._status = ScopeStatus.DISCONNECTED

    def identify(self) -> str:
        return "FAKE_SCOPE"

    def configure_for_cycle(self, settings: ScopeSettings) -> None:
        self._settings = settings
        self._status = ScopeStatus.CONFIGURED

    def readback_settings(self) -> dict[str, str]:
        return {"waveform_points_mode": self._settings.waveform_points_mode}

    def arm_single(self) -> None:
        self._status = ScopeStatus.ARMING

    def wait_until_armed(self, timeout_s: float, now_monotonic_s: float) -> bool:
        del timeout_s, now_monotonic_s
        self._status = ScopeStatus.ARMED
        return True

    def wait_until_acquisition_complete(self, timeout_s: float, now_monotonic_s: float) -> bool:
        del timeout_s, now_monotonic_s
        self._status = ScopeStatus.COMPLETE
        return True

    def capture_after_acquire(self) -> WaveformCapture:
        return WaveformCapture(
            samples=b"\x01\x02",
            preamble={"x_increment": 1e-7, "points": 2},
            settings_readback={"waveform_points_mode": self._settings.waveform_points_mode},
            scope_png=b"png",
            captured_at_utc=datetime.now(tz=timezone.utc),
        )

    def status(self) -> ScopeStatus:
        return self._status


class _FakeCamera(CameraInterface):
    def __init__(self) -> None:
        self._started = False
        self._latest: CameraFrame | None = None

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        return CameraStateSample(
            led_state=LedState.CHARGING,
            observed_at_monotonic_s=now_monotonic_s,
            health=CameraHealth.HEALTHY,
            frame=self._latest,
        )

    def await_charging_gate(
        self, cycle_index: int, timeout_s: float, now_monotonic_s: float
    ) -> tuple[ChargingGateToken | None, CameraStateSample]:
        del timeout_s
        sample = self.sample_state(now_monotonic_s)
        token = ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=now_monotonic_s)
        return token, sample

    def latest_frame(self) -> CameraFrame | None:
        return self._latest


class _FakeNotify(NotificationInterface):
    def notify_start(self, run_id: str, cycle_start: int, cycle_target: int) -> None:
        del run_id, cycle_start, cycle_target

    def notify_resume(self, run_id: str, last_completed_cycle: int, cycle_target: int) -> None:
        del run_id, last_completed_cycle, cycle_target

    def notify_fault(self, run_id: str, cycle_index: int, reason: str) -> None:
        del run_id, cycle_index, reason

    def notify_complete(self, run_id: str, cycle_target: int) -> None:
        del run_id, cycle_target

    def heartbeat(self, run_id: str, last_completed_cycle: int) -> None:
        del run_id, last_completed_cycle

    def heartbeat_fail(self, run_id: str, last_completed_cycle: int, reason: str) -> None:
        del run_id, last_completed_cycle, reason


class HalProtocolTests(unittest.TestCase):
    def test_fake_implementations_bind_to_protocols(self) -> None:
        contactors = _FakeContactors()
        scope = _FakeScope()
        camera = _FakeCamera()
        notify = _FakeNotify()

        self.assertIsInstance(contactors, ContactorInterface)
        self.assertIsInstance(scope, ScopeInterface)
        self.assertIsInstance(camera, CameraInterface)
        self.assertIsInstance(notify, NotificationInterface)

    def test_scope_contract_shape(self) -> None:
        scope = _FakeScope()
        scope.connect()
        scope.configure_for_cycle(ScopeSettings())
        scope.arm_single()
        self.assertTrue(scope.wait_until_armed(timeout_s=2.0, now_monotonic_s=1.0))
        self.assertTrue(scope.wait_until_acquisition_complete(timeout_s=5.0, now_monotonic_s=2.0))
        capture = scope.capture_after_acquire()
        self.assertIn("x_increment", capture.preamble)
        self.assertEqual(scope.status(), ScopeStatus.COMPLETE)

    def test_default_scope_settings_preserve_pre_and_post_trigger_history(self) -> None:
        settings = ScopeSettings()

        self.assertEqual(settings.timebase_scale_s_per_div, 0.05)
        self.assertEqual(settings.timebase_reference, "CENTER")


if __name__ == "__main__":
    unittest.main()
