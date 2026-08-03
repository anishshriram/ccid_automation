from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import json
from pathlib import Path
from typing import Callable

from ccid.errors import HardwareInterfaceError
from ccid.hal.base import (
    CameraFrame,
    CameraHealth,
    CameraInterface,
    CameraStateSample,
    ChargingGateToken,
)
from ccid.states import LedState


class CameraSimError(HardwareInterfaceError):
    pass


@dataclass(frozen=True)
class CameraSimFrameFixture:
    led_state: LedState
    frame_bgr: bytes
    width: int = 1
    height: int = 1


class CameraSim(CameraInterface):
    """Camera replay simulator.

    If replay footage metadata is available, it is used. Otherwise deterministic test
    fixtures are used and explicitly marked as fixtures in frame metadata.
    """

    def __init__(
        self,
        monotonic_now: Callable[[], float] | None = None,
        replay_file: str | Path | None = None,
        fixture_sequence: list[CameraSimFrameFixture] | None = None,
        frame_interval_s: float = 1.0 / 15.0,
        fail_after_samples: int | None = None,
    ) -> None:
        self._monotonic_now = monotonic_now or __import__("time").monotonic
        self._frame_interval_s = frame_interval_s
        self._started = False
        self._cursor = 0
        self._sample_calls = 0
        self._fail_after_samples = fail_after_samples
        self._fixtures = (
            self._load_replay_file(replay_file)
            if replay_file is not None
            else (fixture_sequence if fixture_sequence is not None else self._default_fixtures())
        )
        if not self._fixtures:
            raise ValueError("CameraSim requires at least one fixture frame")
        self._using_replay = replay_file is not None and len(self._fixtures) > 0
        self._latest_frame: CameraFrame | None = None
        self._last_sample_monotonic_s: float | None = None

    def start(self) -> None:
        self._started = True
        self._cursor = 0
        self._sample_calls = 0
        self._latest_frame = None
        self._last_sample_monotonic_s = None

    def stop(self) -> None:
        self._started = False

    def sample_state(self, now_monotonic_s: float) -> CameraStateSample:
        if not self._started:
            raise CameraSimError("CameraSim is not started")
        self._sample_calls += 1
        if self._fail_after_samples is not None and self._sample_calls > self._fail_after_samples:
            return CameraStateSample(
                led_state=LedState.CAMERA_UNAVAILABLE,
                observed_at_monotonic_s=now_monotonic_s,
                health=CameraHealth.FAILED,
                frame=self._latest_frame,
            )

        fixture = self._fixtures[self._cursor]
        self._cursor = min(self._cursor + 1, len(self._fixtures) - 1)

        frame = CameraFrame(
            frame_bgr=fixture.frame_bgr,
            width=fixture.width,
            height=fixture.height,
            captured_at_utc=datetime.now(tz=timezone.utc),
            captured_at_monotonic_s=now_monotonic_s,
            metadata={
                "source": "replay" if self._using_replay else "fixture",
                "fixture_note": "" if self._using_replay else "deterministic_test_fixture",
            },
        )
        self._latest_frame = frame
        self._last_sample_monotonic_s = now_monotonic_s
        return CameraStateSample(
            led_state=fixture.led_state,
            observed_at_monotonic_s=now_monotonic_s,
            health=CameraHealth.HEALTHY,
            frame=frame,
        )

    def await_charging_gate(
        self,
        cycle_index: int,
        timeout_s: float,
        now_monotonic_s: float,
    ) -> tuple[ChargingGateToken | None, CameraStateSample]:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        steps = max(1, int(timeout_s / self._frame_interval_s))
        current_time = now_monotonic_s
        last_sample = self.sample_state(current_time)
        if last_sample.led_state == LedState.CHARGING:
            return (
                ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=current_time),
                last_sample,
            )

        for _ in range(steps - 1):
            current_time += self._frame_interval_s
            last_sample = self.sample_state(current_time)
            if last_sample.led_state == LedState.CHARGING:
                return (
                    ChargingGateToken(cycle_index=cycle_index, granted_at_monotonic_s=current_time),
                    last_sample,
                )
            if last_sample.health == CameraHealth.FAILED:
                return (None, last_sample)
        return (None, last_sample)

    def latest_frame(self) -> CameraFrame | None:
        return self._latest_frame

    def _load_replay_file(self, replay_file: str | Path) -> list[CameraSimFrameFixture]:
        path = Path(replay_file)
        if not path.exists():
            raise CameraSimError(f"Replay file not found: {path}")
        if not path.is_file():
            raise CameraSimError(f"Replay file path is not a file: {path}")
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CameraSimError(f"Failed reading replay file: {path}") from exc
        except json.JSONDecodeError as exc:
            raise CameraSimError(f"Replay file is not valid JSON: {path}") from exc

        if not isinstance(parsed, list):
            raise CameraSimError("Replay file must be a list of frame fixtures")

        fixtures: list[CameraSimFrameFixture] = []
        for i, item in enumerate(parsed):
            if not isinstance(item, dict):
                raise CameraSimError(f"Replay frame at index {i} must be a JSON object")
            led_state_raw = item.get("led_state")
            frame_b64 = item.get("frame_bgr_base64")
            width = item.get("width", 1)
            height = item.get("height", 1)
            if not isinstance(led_state_raw, str):
                raise CameraSimError(f"Replay frame {i} missing string led_state")
            if not isinstance(frame_b64, str):
                raise CameraSimError(f"Replay frame {i} missing string frame_bgr_base64")
            if not isinstance(width, int) or width <= 0:
                raise CameraSimError(f"Replay frame {i} width must be positive integer")
            if not isinstance(height, int) or height <= 0:
                raise CameraSimError(f"Replay frame {i} height must be positive integer")
            try:
                led_state = LedState[led_state_raw]
            except KeyError as exc:
                raise CameraSimError(f"Replay frame {i} has unknown led_state: {led_state_raw}") from exc
            try:
                frame = base64.b64decode(frame_b64.encode("ascii"), validate=True)
            except (ValueError, OSError) as exc:
                raise CameraSimError(f"Replay frame {i} has invalid base64 payload") from exc
            fixtures.append(
                CameraSimFrameFixture(
                    led_state=led_state,
                    frame_bgr=frame,
                    width=width,
                    height=height,
                )
            )
        return fixtures

    @staticmethod
    def _default_fixtures() -> list[CameraSimFrameFixture]:
        return [
            CameraSimFrameFixture(led_state=LedState.BOOTING, frame_bgr=b"\x00\x00\x00"),
            CameraSimFrameFixture(led_state=LedState.BOOTING, frame_bgr=b"\x10\x10\x10"),
            CameraSimFrameFixture(led_state=LedState.READY, frame_bgr=b"\x20\x00\x20"),
            CameraSimFrameFixture(led_state=LedState.CHARGING, frame_bgr=b"\x00\x20\x00"),
        ]

