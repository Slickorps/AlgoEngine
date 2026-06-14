"""Plugin system for AlgoEngine.

Provides plugin discovery, lifecycle management, import sandboxing,
and dependency resolution for third-party extensions.

Quick start::

    from src.plugins import PluginLoader

    loader = PluginLoader(plugins_dir="./plugins", auto_discover=True)
    loader.load_all()
    loader.start_all()

    for meta in loader.registry.list_discovered():
        print(meta.name, meta.version)

    loader.stop_all()
"""

from .interface import (
    IPlugin,
    PluginDependency,
    PluginError,
    PluginInitError,
    PluginLoadError,
    PluginDependencyError,
    PluginValidationError,
    PluginMetadata,
    PluginState,
    PluginType,
    PluginFactory,
)
from .loader import (
    PluginLoader,
    PluginRegistry,
    ImportSandbox,
    sandbox_context,
    DEFAULT_ALLOWED_MODULES,
    check_version_satisfied,
)

__all__ = [
    "IPlugin",
    "PluginDependency",
    "PluginError",
    "PluginInitError",
    "PluginLoadError",
    "PluginDependencyError",
    "PluginValidationError",
    "PluginMetadata",
    "PluginState",
    "PluginType",
    "PluginFactory",
    "PluginLoader",
    "PluginRegistry",
    "ImportSandbox",
    "sandbox_context",
    "DEFAULT_ALLOWED_MODULES",
    "check_version_satisfied",
]
