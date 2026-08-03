from __future__ import annotations

from pathlib import Path
import tempfile
import textwrap
import unittest

from ccid.config import AppConfig, load_config
from ccid.errors import ConfigValidationError


class ConfigTests(unittest.TestCase):
    def _write_config(self, body: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "config.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_loads_example_config(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config.yaml"
        config = load_config(path)
        self.assertIsInstance(config, AppConfig)
        self.assertEqual(config.gpio.k1, 17)
        self.assertEqual(config.timing.scope_arm_timeout_s, 2.0)

    def test_rejects_duplicate_gpio(self) -> None:
        path = self._write_config(
            """
            schema_version: 1
            gpio: {k1: 17, k2: 17, k3: 22}
            timing:
              cooldown_s: 10
              cooldown_retry_s: 60
              boot_timeout_s: 90
              scope_arm_timeout_s: 2.0
              scope_acquisition_timeout_s: 5
              k3_backstop_s: 0.3
              pass_limit_s: 0.02497
              no_trip_limit_s: 0.1
              heartbeat_grace_s: 300
              mains_stagger_ms: 0
            modes: {scope_mode: sim, camera_mode: sim}
            paths: {run_root: ./runs, output_root: ./runs}
            monitoring: {heartbeat_url_env: CCID_HEALTHCHECKS_URL}
            """
        )
        with self.assertRaises(ConfigValidationError):
            load_config(path)

    def test_rejects_invalid_limits(self) -> None:
        path = self._write_config(
            """
            schema_version: 1
            gpio: {k1: 17, k2: 27, k3: 22}
            timing:
              cooldown_s: 10
              cooldown_retry_s: 60
              boot_timeout_s: 90
              scope_arm_timeout_s: 2.0
              scope_acquisition_timeout_s: 5
              k3_backstop_s: 0.09
              pass_limit_s: 0.12
              no_trip_limit_s: 0.1
              heartbeat_grace_s: 300
              mains_stagger_ms: 0
            modes: {scope_mode: sim, camera_mode: sim}
            paths: {run_root: ./runs, output_root: ./runs}
            monitoring: {heartbeat_url_env: CCID_HEALTHCHECKS_URL}
            """
        )
        with self.assertRaises(ConfigValidationError):
            load_config(path)

    def test_rejects_unknown_keys(self) -> None:
        path = self._write_config(
            """
            schema_version: 1
            gpio: {k1: 17, k2: 27, k3: 22, k4: 99}
            timing:
              cooldown_s: 10
              cooldown_retry_s: 60
              boot_timeout_s: 90
              scope_arm_timeout_s: 2.0
              scope_acquisition_timeout_s: 5
              k3_backstop_s: 0.3
              pass_limit_s: 0.02497
              no_trip_limit_s: 0.1
              heartbeat_grace_s: 300
              mains_stagger_ms: 0
            modes: {scope_mode: sim, camera_mode: sim}
            paths: {run_root: ./runs, output_root: ./runs}
            monitoring: {heartbeat_url_env: CCID_HEALTHCHECKS_URL}
            """
        )
        with self.assertRaises(ConfigValidationError):
            load_config(path)

    def test_hash_stable_across_key_order(self) -> None:
        path_a = self._write_config(
            """
            schema_version: 1
            gpio: {k1: 17, k2: 27, k3: 22}
            timing:
              cooldown_s: 10
              cooldown_retry_s: 60
              boot_timeout_s: 90
              scope_arm_timeout_s: 2.0
              scope_acquisition_timeout_s: 5
              k3_backstop_s: 0.3
              pass_limit_s: 0.02497
              no_trip_limit_s: 0.1
              heartbeat_grace_s: 300
              mains_stagger_ms: 0
            modes: {scope_mode: sim, camera_mode: sim}
            paths: {run_root: ./runs, output_root: ./runs}
            monitoring: {heartbeat_url_env: CCID_HEALTHCHECKS_URL}
            """
        )
        path_b = self._write_config(
            """
            monitoring:
              heartbeat_url_env: CCID_HEALTHCHECKS_URL
            paths:
              output_root: ./runs
              run_root: ./runs
            modes:
              camera_mode: sim
              scope_mode: sim
            timing:
              mains_stagger_ms: 0
              heartbeat_grace_s: 300
              no_trip_limit_s: 0.1
              pass_limit_s: 0.02497
              k3_backstop_s: 0.3
              scope_acquisition_timeout_s: 5
              scope_arm_timeout_s: 2.0
              boot_timeout_s: 90
              cooldown_retry_s: 60
              cooldown_s: 10
            gpio:
              k3: 22
              k2: 27
              k1: 17
            schema_version: 1
            """
        )
        hash_a = load_config(path_a).canonical_hash()
        hash_b = load_config(path_b).canonical_hash()
        self.assertEqual(hash_a, hash_b)

    def test_rejects_unsupported_modes(self) -> None:
        path = self._write_config(
            """
            schema_version: 1
            gpio: {k1: 17, k2: 27, k3: 22}
            timing:
              cooldown_s: 10
              cooldown_retry_s: 60
              boot_timeout_s: 90
              scope_arm_timeout_s: 2.0
              scope_acquisition_timeout_s: 5
              k3_backstop_s: 0.3
              pass_limit_s: 0.02497
              no_trip_limit_s: 0.1
              heartbeat_grace_s: 300
              mains_stagger_ms: 0
            modes: {scope_mode: visa, camera_mode: sim}
            paths: {run_root: ./runs, output_root: ./runs}
            monitoring: {heartbeat_url_env: CCID_HEALTHCHECKS_URL}
            """
        )
        with self.assertRaises(ConfigValidationError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()

