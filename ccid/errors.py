class CcidError(Exception):
    """Base error for all CCID automation exceptions."""


class ConfigError(CcidError):
    """Configuration input, loading, or validation error."""


class ConfigFileError(ConfigError):
    """Configuration file could not be read or parsed."""


class ConfigValidationError(ConfigError):
    """Configuration failed strict schema or value validation."""


class HardwareInterfaceError(CcidError):
    """HAL-level hardware I/O error."""


class SafetyViolationError(CcidError):
    """Safety invariant would be violated by requested operation."""


class TimeoutError(CcidError):
    """Monotonic deadline expired."""


class PersistenceError(CcidError):
    """Artifact, CSV, or runstate persistence failure."""


class ResumeBlockedError(CcidError):
    """Resume is blocked because run state is sticky halted."""


class ConfigHashMismatchError(CcidError):
    """Resume config hash mismatch; explicit override required."""
