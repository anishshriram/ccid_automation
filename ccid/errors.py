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


class AnalysisError(CcidError):
    """Waveform analysis subsystem error."""


class WaveformAnalysisError(AnalysisError):
    """A waveform could not be analysed."""


class WaveformFormatError(WaveformAnalysisError):
    """Stored waveform container or preamble is missing, malformed, or unusable."""


class VisionError(CcidError):
    """Vision/classification subsystem error. Never allowed to halt the run."""


class VisionFrameError(VisionError):
    """Camera frame is missing, malformed, or incompatible with the configured ROI."""
