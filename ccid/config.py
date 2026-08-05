"""Strict configuration loading, validation, and hash-freezing.

Every section is validated against an explicit key set (`_reject_unknown_keys`)
and built into a frozen `AppConfig` - unknown keys, missing required keys, and
out-of-range values all fail loudly at load time rather than surfacing later
as a mysterious runtime default. `AppConfig.canonical_hash()` is the other
half of the contract: it's computed from a canonical (key-order-independent)
JSON payload of the loaded config and is what `RunRecorder`/`Sequencer` use to
detect a run being resumed against a silently different configuration, so a
new required field belongs in this hash unless there's a specific reason it
shouldn't be (see `_canonical_for_hash`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ccid.analysis import (
    DEFAULT_ENDPOINT_DEFINITION,
    AnalysisConfig,
    AnalysisVersion,
)
from ccid.errors import ConfigFileError, ConfigValidationError

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment dependent
    raise ConfigFileError(
        "PyYAML is required to load config.yaml; install dependencies from requirements.txt"
    ) from exc


_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "gpio",
        "vision",
        "camera",
        "timing",
        "modes",
        "paths",
        "monitoring",
        "analysis",
    }
)
_GPIO_KEYS = frozenset({"k1", "k2", "k3"})
_VISION_KEYS = frozenset(
    {
        "roi_x",
        "roi_y",
        "roi_width",
        "roi_height",
        "charging_green_window_s",
        "charging_green_required_frames",
    }
)
_TIMING_KEYS = frozenset(
    {
        "cooldown_s",
        "cooldown_retry_s",
        "boot_timeout_s",
        "scope_arm_timeout_s",
        "scope_acquisition_timeout_s",
        "k3_backstop_s",
        "pass_limit_s",
        "no_trip_limit_s",
        "heartbeat_grace_s",
        "mains_stagger_ms",
    }
)
_MODES_KEYS = frozenset({"gpio_mode", "scope_mode", "camera_mode"})
_SUPPORTED_GPIO_MODES = frozenset({"real", "sim"})
_PATHS_KEYS = frozenset({"run_root", "output_root", "min_free_disk_gb"})
_CAMERA_KEYS = frozenset({"device_index"})
_MONITORING_KEYS = frozenset({"heartbeat_url_env"})
_ANALYSIS_KEYS = frozenset(
    {
        "algorithm_version",
        "endpoint_definition",
        "line_frequency_hz",
        "envelope_window_cycles",
        "envelope_on_fraction",
        "envelope_off_fraction",
        "noise_floor_v",
        "collapse_persistence_cycles",
        "signal_present_rms_v",
        "pretrigger_leakage_rms_v",
        "burst_start_tolerance_s",
        "pretrigger_leakage_guard_cycles",
        "residual_floor_noise_multiple",
        "noise_collapse_multiple",
        "endpoint_uncertainty_s",
    }
)
_SUPPORTED_SCOPE_MODES = frozenset({"real", "sim"})
_SUPPORTED_CAMERA_MODES = frozenset({"real", "sim"})


@dataclass(frozen=True)
class GpioConfig:
    k1: int
    k2: int
    k3: int


@dataclass(frozen=True)
class TimingConfig:
    cooldown_s: float
    cooldown_retry_s: float
    boot_timeout_s: float
    scope_arm_timeout_s: float
    scope_acquisition_timeout_s: float
    k3_backstop_s: float
    pass_limit_s: float
    no_trip_limit_s: float
    heartbeat_grace_s: float
    mains_stagger_ms: int


@dataclass(frozen=True)
class ModesConfig:
    gpio_mode: str
    scope_mode: str
    camera_mode: str


@dataclass(frozen=True)
class PathsConfig:
    run_root: Path
    output_root: Path
    min_free_disk_gb: int


@dataclass(frozen=True)
class MonitoringConfig:
    heartbeat_url_env: str | None


@dataclass(frozen=True)
class VisionConfig:
    roi_x: int
    roi_y: int
    roi_width: int
    roi_height: int
    charging_green_window_s: float
    charging_green_required_frames: int


@dataclass(frozen=True)
class CameraHardwareConfig:
    device_index: int


@dataclass(frozen=True)
class AppConfig:
    schema_version: int
    gpio: GpioConfig
    vision: VisionConfig
    camera: CameraHardwareConfig
    timing: TimingConfig
    modes: ModesConfig
    paths: PathsConfig
    monitoring: MonitoringConfig
    analysis: AnalysisConfig

    def canonical_hash(self) -> str:
        canonical_payload = _canonical_for_hash(self)
        encoded = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    def resolve_heartbeat_url(self) -> str | None:
        if self.monitoring.heartbeat_url_env is None:
            return None
        return os.environ.get(self.monitoring.heartbeat_url_env)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigFileError(f"Configuration file does not exist: {config_path}")
    if not config_path.is_file():
        raise ConfigFileError(f"Configuration path is not a file: {config_path}")

    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigFileError(f"Could not read configuration file: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigFileError(f"Invalid YAML in configuration file: {config_path}") from exc

    if not isinstance(parsed, dict):
        raise ConfigValidationError("Top-level YAML document must be a mapping")
    return _validate_and_build(parsed)


def _validate_and_build(raw: dict[str, Any]) -> AppConfig:
    _reject_unknown_keys("top-level", raw, _TOP_LEVEL_KEYS)

    schema_version = _require_int(raw, "schema_version", minimum=1)
    gpio_map = _require_mapping(raw, "gpio")
    vision_map = _require_mapping(raw, "vision")
    camera_map = _require_mapping(raw, "camera")
    timing_map = _require_mapping(raw, "timing")
    modes_map = _require_mapping(raw, "modes")
    paths_map = _require_mapping(raw, "paths")
    monitoring_map = _require_mapping(raw, "monitoring")

    _reject_unknown_keys("gpio", gpio_map, _GPIO_KEYS)
    _reject_unknown_keys("vision", vision_map, _VISION_KEYS)
    _reject_unknown_keys("camera", camera_map, _CAMERA_KEYS)
    _reject_unknown_keys("timing", timing_map, _TIMING_KEYS)
    _reject_unknown_keys("modes", modes_map, _MODES_KEYS)
    _reject_unknown_keys("paths", paths_map, _PATHS_KEYS)
    _reject_unknown_keys("monitoring", monitoring_map, _MONITORING_KEYS)

    gpio = GpioConfig(
        k1=_require_int(gpio_map, "k1", minimum=0),
        k2=_require_int(gpio_map, "k2", minimum=0),
        k3=_require_int(gpio_map, "k3", minimum=0),
    )
    if len({gpio.k1, gpio.k2, gpio.k3}) != 3:
        raise ConfigValidationError("GPIO numbers must be unique across K1, K2, and K3")

    vision = VisionConfig(
        roi_x=_require_int(vision_map, "roi_x", minimum=0),
        roi_y=_require_int(vision_map, "roi_y", minimum=0),
        roi_width=_require_int(vision_map, "roi_width", minimum=1),
        roi_height=_require_int(vision_map, "roi_height", minimum=1),
        charging_green_window_s=_require_float(vision_map, "charging_green_window_s", positive=True),
        charging_green_required_frames=_require_int(
            vision_map, "charging_green_required_frames", minimum=1
        ),
    )

    camera = CameraHardwareConfig(
        device_index=_require_int(camera_map, "device_index", minimum=0),
    )

    timing = TimingConfig(
        cooldown_s=_require_float(timing_map, "cooldown_s", positive=True),
        cooldown_retry_s=_require_float(timing_map, "cooldown_retry_s", positive=True),
        boot_timeout_s=_require_float(timing_map, "boot_timeout_s", positive=True),
        scope_arm_timeout_s=_require_float(timing_map, "scope_arm_timeout_s", positive=True),
        scope_acquisition_timeout_s=_require_float(
            timing_map, "scope_acquisition_timeout_s", positive=True
        ),
        k3_backstop_s=_require_float(timing_map, "k3_backstop_s", positive=True),
        pass_limit_s=_require_float(timing_map, "pass_limit_s", positive=True),
        no_trip_limit_s=_require_float(timing_map, "no_trip_limit_s", positive=True),
        heartbeat_grace_s=_require_float(timing_map, "heartbeat_grace_s", positive=True),
        mains_stagger_ms=_require_int(timing_map, "mains_stagger_ms", minimum=0),
    )
    if timing.pass_limit_s >= timing.no_trip_limit_s:
        raise ConfigValidationError("pass_limit_s must be lower than no_trip_limit_s")
    if timing.k3_backstop_s <= timing.no_trip_limit_s:
        raise ConfigValidationError("k3_backstop_s must be greater than no_trip_limit_s")

    scope_mode = _require_str(modes_map, "scope_mode")
    if scope_mode not in _SUPPORTED_SCOPE_MODES:
        raise ConfigValidationError(
            f"Unsupported scope mode '{scope_mode}'; supported: {sorted(_SUPPORTED_SCOPE_MODES)}"
        )
    gpio_mode = modes_map.get("gpio_mode", "sim")
    if not isinstance(gpio_mode, str):
        raise ConfigValidationError("gpio_mode must be a string")
    if gpio_mode not in _SUPPORTED_GPIO_MODES:
        raise ConfigValidationError(
            f"Unsupported gpio mode '{gpio_mode}'; supported: {sorted(_SUPPORTED_GPIO_MODES)}"
        )
    camera_mode = _require_str(modes_map, "camera_mode")
    if camera_mode not in _SUPPORTED_CAMERA_MODES:
        raise ConfigValidationError(
            f"Unsupported camera mode '{camera_mode}'; supported: {sorted(_SUPPORTED_CAMERA_MODES)}"
        )
    modes = ModesConfig(gpio_mode=gpio_mode, scope_mode=scope_mode, camera_mode=camera_mode)

    run_root = _require_non_empty_path(paths_map, "run_root")
    output_root = _require_non_empty_path(paths_map, "output_root")
    min_free_disk_gb = _require_int(paths_map, "min_free_disk_gb", minimum=1)
    paths = PathsConfig(run_root=run_root, output_root=output_root, min_free_disk_gb=min_free_disk_gb)

    heartbeat_url_env = monitoring_map.get("heartbeat_url_env")
    if heartbeat_url_env is not None:
        if not isinstance(heartbeat_url_env, str) or not heartbeat_url_env.strip():
            raise ConfigValidationError("heartbeat_url_env must be a non-empty string when set")
    monitoring = MonitoringConfig(heartbeat_url_env=heartbeat_url_env)

    analysis = _build_analysis(
        raw.get("analysis"),
        pass_limit_s=timing.pass_limit_s,
        no_trip_limit_s=timing.no_trip_limit_s,
    )

    return AppConfig(
        schema_version=schema_version,
        gpio=gpio,
        vision=vision,
        camera=camera,
        timing=timing,
        modes=modes,
        paths=paths,
        monitoring=monitoring,
        analysis=analysis,
    )


def _build_analysis(
    raw_analysis: Any,
    *,
    pass_limit_s: float,
    no_trip_limit_s: float,
) -> AnalysisConfig:
    """Build the analysis configuration; the whole section is optional.

    The endpoint definition lives here on purpose (handoff section 4): if
    UL 2231-2 does not define the measurement endpoints, the chosen definition
    must be written into `config.yaml` before the run and frozen by the config
    hash.
    """

    if raw_analysis is None:
        raw_analysis = {}
    if not isinstance(raw_analysis, dict):
        raise ConfigValidationError("'analysis' must be a mapping")
    _reject_unknown_keys("analysis", raw_analysis, _ANALYSIS_KEYS)

    version_text = raw_analysis.get("algorithm_version")
    if version_text is None:
        algorithm_version = AnalysisConfig.algorithm_version
    else:
        if not isinstance(version_text, str):
            raise ConfigValidationError("'algorithm_version' must be a string")
        try:
            algorithm_version = AnalysisVersion(version_text)
        except ValueError as exc:
            supported = sorted(member.value for member in AnalysisVersion)
            raise ConfigValidationError(
                f"Unsupported analysis algorithm_version '{version_text}'; supported: {supported}"
            ) from exc

    endpoint_definition = raw_analysis.get("endpoint_definition", DEFAULT_ENDPOINT_DEFINITION)
    if not isinstance(endpoint_definition, str) or not endpoint_definition.strip():
        raise ConfigValidationError("'endpoint_definition' must be a non-empty string")

    numeric_keys = (
        "line_frequency_hz",
        "envelope_window_cycles",
        "envelope_on_fraction",
        "envelope_off_fraction",
        "noise_floor_v",
        "collapse_persistence_cycles",
        "signal_present_rms_v",
        "pretrigger_leakage_rms_v",
        "burst_start_tolerance_s",
        "pretrigger_leakage_guard_cycles",
        "residual_floor_noise_multiple",
        "noise_collapse_multiple",
        "endpoint_uncertainty_s",
    )
    numeric: dict[str, float] = {}
    for key in numeric_keys:
        if key in raw_analysis:
            numeric[key] = _require_float(raw_analysis, key)

    try:
        return AnalysisConfig(
            pass_limit_s=pass_limit_s,
            no_trip_limit_s=no_trip_limit_s,
            algorithm_version=algorithm_version,
            endpoint_definition=endpoint_definition,
            **numeric,
        )
    except ValueError as exc:
        raise ConfigValidationError(f"Invalid analysis configuration: {exc}") from exc


def _canonical_for_hash(config: AppConfig) -> dict[str, Any]:
    raw = asdict(config)
    analysis = dict(raw["analysis"])
    analysis["algorithm_version"] = config.analysis.algorithm_version.value
    return {
        "schema_version": raw["schema_version"],
        "gpio": raw["gpio"],
        "vision": raw["vision"],
        "camera": raw["camera"],
        "timing": raw["timing"],
        "modes": raw["modes"],
        # The endpoint definition is part of the frozen campaign contract.
        "analysis": analysis,
        "paths": {
            "run_root": str(config.paths.run_root),
            "output_root": str(config.paths.output_root),
            "min_free_disk_gb": config.paths.min_free_disk_gb,
        },
        # Keep only environment variable name in canonical hash; never secret value.
        "monitoring": {"heartbeat_url_env": config.monitoring.heartbeat_url_env},
    }


def _reject_unknown_keys(section: str, raw: dict[str, Any], allowed: frozenset[str]) -> None:
    extras = set(raw.keys()) - set(allowed)
    if extras:
        extras_text = ", ".join(sorted(extras))
        raise ConfigValidationError(f"Unknown keys in {section}: {extras_text}")


def _require_mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ConfigValidationError(f"'{key}' must be a mapping")
    return value


def _require_int(raw: dict[str, Any], key: str, minimum: int | None = None) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigValidationError(f"'{key}' must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigValidationError(f"'{key}' must be >= {minimum}")
    return value


def _require_float(raw: dict[str, Any], key: str, positive: bool = False) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(f"'{key}' must be numeric")
    as_float = float(value)
    if positive and as_float <= 0.0:
        raise ConfigValidationError(f"'{key}' must be > 0")
    return as_float


def _require_str(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"'{key}' must be a non-empty string")
    return value


def _require_non_empty_path(raw: dict[str, Any], key: str) -> Path:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"'{key}' must be a non-empty path string")
    return Path(value).expanduser()
