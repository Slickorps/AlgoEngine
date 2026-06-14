"""Plugin system interfaces and metadata types.

Defines the contract that all AlgoEngine plugins must implement,
along with metadata structures for discovery and dependency management.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Callable


class PluginType(Enum):
    """Categories of plugins supported by the engine."""

    STRATEGY = auto()
    INDICATOR = auto()
    BROKER_ADAPTER = auto()
    DATA_PROVIDER = auto()
    RISK_MODEL = auto()
    COMMISSION_MODEL = auto()
    REPORT_GENERATOR = auto()
    SLIPPAGE_MODEL = auto()
    EXECUTION_MODEL = auto()
    GENERAL = auto()


class PluginState(Enum):
    """Lifecycle states for a plugin instance."""

    DISCOVERED = auto()
    LOADED = auto()
    VALIDATED = auto()
    INITIALIZED = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    UNLOADED = auto()
    ERROR = auto()


class PluginError(Exception):
    """Base exception for plugin-related errors."""

    pass


class PluginLoadError(PluginError):
    """Raised when a plugin fails to load."""

    pass


class PluginInitError(PluginError):
    """Raised when a plugin fails to initialize."""

    pass


class PluginDependencyError(PluginError):
    """Raised when plugin dependencies are not satisfied."""

    pass


class PluginValidationError(PluginError):
    """Raised when plugin metadata or structure is invalid."""

    pass


@dataclass
class PluginDependency:
    """Describes a single plugin dependency."""

    name: str
    version_min: Optional[str] = None
    version_max: Optional[str] = None
    required: bool = True

    def __hash__(self) -> int:
        return hash((self.name, self.version_min, self.version_max))


@dataclass
class PluginMetadata:
    """Metadata describing a plugin's identity, capabilities, and requirements.

    This structure is discovered from a plugin's entry point or a
    ``plugin.toml`` / ``plugin.json`` file in the plugin package.
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    license: str = "MIT"
    plugin_type: PluginType = PluginType.GENERAL

    homepage: str = ""
    repository: str = ""

    python_version_min: str = "3.8"
    engine_version_min: str = "1.0.0"

    dependencies: List[PluginDependency] = field(default_factory=list)
    provides: Set[str] = field(default_factory=set)
    entry_point: Optional[str] = None
    config_schema: Dict[str, Any] = field(default_factory=dict)

    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "license": self.license,
            "plugin_type": self.plugin_type.name,
            "homepage": self.homepage,
            "repository": self.repository,
            "python_version_min": self.python_version_min,
            "engine_version_min": self.engine_version_min,
            "dependencies": [
                {"name": d.name, "version_min": d.version_min,
                 "version_max": d.version_max, "required": d.required}
                for d in self.dependencies
            ],
            "provides": list(self.provides),
            "tags": self.tags,
        }

    def __hash__(self) -> int:
        return hash(self.name)


class IPlugin(ABC):
    """Base interface for all AlgoEngine plugins.

    Every plugin MUST implement this interface. The engine calls
    lifecycle methods in order::

        discover → load → validate → initialize → start → stop → unload
    """

    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return immutable metadata describing this plugin."""

    def on_load(self) -> None:
        """Called after the plugin module is imported."""

    def validate(self) -> bool:
        """Validate that the plugin's environment is properly set up.

        Returns True if validation passes. Override to add custom checks.
        """
        return True

    def on_init(self, config: Dict[str, Any]) -> None:
        """Initialize the plugin with runtime configuration."""

    def on_start(self) -> None:
        """Start plugin execution."""

    def on_pause(self) -> None:
        """Pause plugin activity without unloading."""

    def on_resume(self) -> None:
        """Resume after pause."""

    def on_stop(self) -> None:
        """Gracefully stop plugin execution and release resources."""

    def on_unload(self) -> None:
        """Final cleanup before the module reference is dropped."""

    @property
    def state(self) -> PluginState:
        """Return the current lifecycle state."""
        return getattr(self, "_state", PluginState.DISCOVERED)

    @state.setter
    def state(self, value: PluginState) -> None:
        object.__setattr__(self, "_state", value)


PluginFactory = Callable[[], IPlugin]
"""Callable that returns a new plugin instance."""
