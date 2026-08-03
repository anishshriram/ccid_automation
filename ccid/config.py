from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

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
        "timing",
        "modes",
        "paths",
        "monitoring",
    }
)
_GPIO_KEYS = frozenset({"k1", "k2", "k3"})
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
_MODES_KEYS = frozenset({"scope_mode", "camera_mode"})
_PATHS_KEYS = frozenset({"run_root", "output_root"})
_MONITORING_KEYS = frozenset({"heartbeat_url_env"})
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
    scope_mode: str
    camera_mode: str


@dataclass(frozen=True)
class PathsConfig:
    run_root: Path
    output_root: Path


@dataclass(frozen=True)
class MonitoringConfig:
    heartbeat_url_env: str | None


@dataclass(frozen=True)
class AppConfig:
    schema_version: int
    gpio: GpioConfig
    timing: TimingConfig
    modes: ModesConfig
    paths: PathsConfig
    monitoring: MonitoringConfig

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
    timing_map = _require_mapping(raw, "timing")
    modes_map = _require_mapping(raw, "modes")
    paths_map = _require_mapping(raw, "paths")
    monitoring_map = _require_mapping(raw, "monitoring")

    _reject_unknown_keys("gpio", gpio_map, _GPIO_KEYS)
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
    camera_mode = _require_str(modes_map, "camera_mode")
    if camera_mode not in _SUPPORTED_CAMERA_MODES:
        raise ConfigValidationError(
            f"Unsupported camera mode '{camera_mode}'; supported: {sorted(_SUPPORTED_CAMERA_MODES)}"
        )
    modes = ModesConfig(scope_mode=scope_mode, camera_mode=camera_mode)

    run_root = _require_non_empty_path(paths_map, "run_root")
    output_root = _require_non_empty_path(paths_map, "output_root")
    paths = PathsConfig(run_root=run_root, output_root=output_root)

    heartbeat_url_env = monitoring_map.get("heartbeat_url_env")
    if heartbeat_url_env is not None:
        if not isinstance(heartbeat_url_env, str) or not heartbeat_url_env.strip():
            raise ConfigValidationError("heartbeat_url_env must be a non-empty string when set")
    monitoring = MonitoringConfig(heartbeat_url_env=heartbeat_url_env)

    return AppConfig(
        schema_version=schema_version,
        gpio=gpio,
        timing=timing,
        modes=modes,
        paths=paths,
        monitoring=monitoring,
    )


def _canonical_for_hash(config: AppConfig) -> dict[str, Any]:
    raw = asdict(config)
    return {
        "schema_version": raw["schema_version"],
        "gpio": raw["gpio"],
        "timing": raw["timing"],
        "modes": raw["modes"],
        "paths": {
            "run_root": str(config.paths.run_root),
            "output_root": str(config.paths.output_root),
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

