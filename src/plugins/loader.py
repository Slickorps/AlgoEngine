"""Plugin loader: discovery, lifecycle, sandbox, and dependency resolution.

Supports two discovery modes:
    1. **Entry-point discovery** — ``algoengine.plugins`` setuptools entry point group
    2. **Directory scanning** — scan a configured ``plugins_dir`` for Python packages

Also provides import sandboxing to restrict which modules plugins may import.
"""

import importlib
import importlib.util
import inspect
import json
import re
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Type,
    Tuple,
)

from .interface import (
    IPlugin,
    PluginDependency,
    PluginError,
    PluginLoadError,
    PluginInitError,
    PluginDependencyError,
    PluginValidationError,
    PluginMetadata,
    PluginState,
    PluginType,
)
from ..utils.logger import get_logger

logger = get_logger("plugins.loader")

PluginFactory = Callable[[], IPlugin]

# ---------------------------------------------------------------------------
# Import sandbox
# ---------------------------------------------------------------------------

DEFAULT_ALLOWED_MODULES: Set[str] = {
    "abc", "asyncio", "builtins", "collections", "collections.abc",
    "contextlib", "copy", "dataclasses", "datetime", "decimal",
    "enum", "functools", "hashlib", "inspect", "io", "itertools",
    "json", "logging", "math", "operator", "os.path",
    "pathlib", "pickle", "random", "re", "statistics", "string",
    "struct", "textwrap", "threading", "time", "traceback",
    "types", "typing", "uuid", "warnings", "weakref",
    "numpy", "pandas",
    "src", "src.data", "src.data.models", "src.engine", "src.engine.events",
    "src.engine.interfaces", "src.algorithms", "src.algorithms.indicators",
    "src.trading", "src.trading.models", "src.portfolio",
    "src.risk", "src.utils", "src.plugins",
}


class ImportSandbox(importlib.abc.MetaPathFinder):
    """Meta-path finder that restricts plugin imports to an allow-list."""

    def __init__(self, allowed: Optional[Set[str]] = None) -> None:
        self._allowed = allowed or DEFAULT_ALLOWED_MODULES

    def find_spec(
        self,
        fullname: str,
        path: Optional[List[str]],
        target: Optional[Any] = None,
    ):
        for prefix in self._allowed:
            if fullname == prefix or fullname.startswith(prefix + "."):
                return None
        raise ImportError(
            f"Plugin tried to import '{fullname}' which is not allowed"
        )


@contextmanager
def sandbox_context(allowed: Optional[Set[str]] = None):
    sandbox = ImportSandbox(allowed)
    sys.meta_path.insert(0, sandbox)
    try:
        yield
    finally:
        if sandbox in sys.meta_path:
            sys.meta_path.remove(sandbox)


@contextmanager
def _no_sandbox():
    yield


# ---------------------------------------------------------------------------
# Version parsing helpers (no external deps)
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _parse_semver(v: str) -> Optional[Tuple[int, int, int]]:
    m = _VERSION_RE.match(v.strip())
    if not m:
        return None
    return (int(m.group("major")), int(m.group("minor")), int(m.group("patch")))


def _compare_versions(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    for x, y in zip(a, b):
        if x < y:
            return -1
        if x > y:
            return 1
    return len(a) - len(b)


def check_version_satisfied(
    actual: Optional[str],
    minimum: Optional[str] = None,
    maximum: Optional[str] = None,
) -> bool:
    if actual is None:
        return minimum is None
    actual_t = _parse_semver(actual)
    if actual_t is None:
        return False
    if minimum is not None:
        min_t = _parse_semver(minimum)
        if min_t is not None and _compare_versions(actual_t, min_t) < 0:
            return False
    if maximum is not None:
        max_t = _parse_semver(maximum)
        if max_t is not None and _compare_versions(actual_t, max_t) > 0:
            return False
    return True


# ---------------------------------------------------------------------------
# Plugin Registry
# ---------------------------------------------------------------------------


class PluginRegistry:
    """Thread-safe registry of discovered and loaded plugins."""

    _instance: Optional["PluginRegistry"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._discovered: Dict[str, PluginMetadata] = {}
        self._instances: Dict[str, IPlugin] = {}
        self._factories: Dict[str, PluginFactory] = {}
        self._errors: Dict[str, str] = {}

    @classmethod
    def instance(cls) -> "PluginRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register_discovered(self, meta: PluginMetadata) -> None:
        name = meta.name
        if name in self._discovered:
            return
        self._discovered[name] = meta

    def register_instance(self, name: str, plugin: IPlugin) -> None:
        self._instances[name] = plugin

    def register_factory(self, name: str, factory: PluginFactory) -> None:
        self._factories[name] = factory

    def set_error(self, name: str, message: str) -> None:
        self._errors[name] = message

    def clear_error(self, name: str) -> None:
        self._errors.pop(name, None)

    def get_metadata(self, name: str) -> Optional[PluginMetadata]:
        return self._discovered.get(name)

    def get_instance(self, name: str) -> Optional[IPlugin]:
        return self._instances.get(name)

    def is_loaded(self, name: str) -> bool:
        return name in self._instances

    def list_discovered(self) -> List[PluginMetadata]:
        return list(self._discovered.values())

    def list_loaded(self) -> List[str]:
        return list(self._instances.keys())

    def list_by_type(self, plugin_type: PluginType) -> List[PluginMetadata]:
        return [
            m for m in self._discovered.values()
            if m.plugin_type == plugin_type
        ]

    def get_errors(self) -> Dict[str, str]:
        return dict(self._errors)

    def summary(self) -> Dict[str, Any]:
        loaded = self.list_loaded()
        return {
            "discovered": len(self._discovered),
            "loaded": len(loaded),
            "errors": len(self._errors),
            "names_loaded": loaded,
            "errors_detail": self.get_errors(),
        }

    def stop_all(self) -> int:
        count = 0
        for name, plugin in list(self._instances.items()):
            try:
                plugin.on_stop()
                plugin.state = PluginState.STOPPED
                count += 1
            except Exception:
                logger.error(f"Error stopping plugin {name}", exc_info=True)
        return count

    def clear(self) -> None:
        self._discovered.clear()
        self._instances.clear()
        self._factories.clear()
        self._errors.clear()


# ---------------------------------------------------------------------------
# Plugin Loader
# ---------------------------------------------------------------------------


class PluginLoader:
    """Loads, validates, and manages the lifecycle of AlgoEngine plugins.

    Typical usage::

        loader = PluginLoader(plugins_dir="./plugins")
        loader.discover()
        loader.load_all()
        loader.start_all()
        loader.stop_all()
    """

    def __init__(
        self,
        plugins_dir: Optional[str] = None,
        auto_discover: bool = False,
        sandbox_enabled: bool = False,
        allowed_modules: Optional[Set[str]] = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir) if plugins_dir else None
        self._auto_discover = auto_discover
        self._sandbox_enabled = sandbox_enabled
        self._allowed_modules = allowed_modules or DEFAULT_ALLOWED_MODULES
        self._registry = PluginRegistry.instance()

        if self._plugins_dir is not None:
            self._plugins_dir.mkdir(parents=True, exist_ok=True)

        if auto_discover:
            self.discover()

    # ── Discovery ──────────────────────────────────────────────────

    def discover(
        self,
        plugins_dir: Optional[str] = None,
    ) -> List[PluginMetadata]:
        discovered: List[PluginMetadata] = []

        scan_dir = Path(plugins_dir) if plugins_dir else self._plugins_dir
        if scan_dir is not None:
            discovered.extend(self._discover_from_directory(scan_dir))

        discovered.extend(self._discover_from_entry_points())

        for meta in discovered:
            self._registry.register_discovered(meta)

        logger.info(f"Plugin discovery complete: {len(discovered)} found")
        return discovered

    def _discover_from_directory(
        self, directory: Path
    ) -> List[PluginMetadata]:
        found: List[PluginMetadata] = []
        if not directory.is_dir():
            return found

        for candidate in sorted(directory.iterdir()):
            meta = self._read_plugin_metadata(candidate)
            if meta is not None:
                found.append(meta)
        return found

    def _discover_from_entry_points(self) -> List[PluginMetadata]:
        found: List[PluginMetadata] = []
        try:
            from importlib.metadata import entry_points
            eps = entry_points(group="algoengine.plugins")
            for ep in eps:
                factory = ep.load()
                try:
                    plugin = factory()
                except Exception:
                    logger.warning(
                        f"Failed to instantiate plugin from entry point "
                        f"'{ep.name}'",
                        exc_info=True,
                    )
                    continue
                if hasattr(plugin, "metadata"):
                    meta = plugin.metadata()
                    meta.entry_point = ep.name
                    self._registry.register_factory(ep.name, factory)
                    found.append(meta)
        except Exception:
            logger.debug("entry_points not available", exc_info=True)
        return found

    @staticmethod
    def _read_plugin_metadata(
        candidate: Path,
    ) -> Optional[PluginMetadata]:
        if candidate.name.startswith(".") or candidate.name.startswith("_"):
            return None

        if candidate.is_dir():
            manifest = candidate / "plugin.json"
            if manifest.exists():
                return PluginLoader._parse_json_manifest(manifest)
            manifest_toml = candidate / "plugin.toml"
            if manifest_toml.exists():
                return PluginLoader._parse_toml_manifest(manifest_toml)
            py_init = candidate / "__init__.py"
            if py_init.exists():
                return PluginMetadata(
                    name=candidate.name,
                    version="0.1.0",
                    plugin_type=PluginType.GENERAL,
                )
        elif candidate.suffix == ".py":
            return PluginMetadata(
                name=candidate.stem,
                version="0.1.0",
                plugin_type=PluginType.GENERAL,
            )
        return None

    @staticmethod
    def _parse_json_manifest(path: Path) -> Optional[PluginMetadata]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return PluginLoader._dict_to_metadata(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(f"Invalid plugin manifest {path}: {exc}")
            return None

    @staticmethod
    def _parse_toml_manifest(path: Path) -> Optional[PluginMetadata]:
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                logger.debug(f"Cannot parse TOML {path}: no parser")
                return None
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            plugin_data = data.get("plugin", data)
            return PluginLoader._dict_to_metadata(plugin_data)
        except Exception as exc:
            logger.warning(f"Invalid TOML manifest {path}: {exc}")
            return None

    @staticmethod
    def _dict_to_metadata(data: Dict[str, Any]) -> PluginMetadata:
        deps = [
            PluginDependency(
                name=d.get("name", ""),
                version_min=d.get("version_min"),
                version_max=d.get("version_max"),
                required=d.get("required", True),
            )
            for d in data.get("dependencies", [])
        ]

        ptype_str = data.get("plugin_type", "GENERAL")
        try:
            ptype = PluginType[ptype_str.upper()]
        except KeyError:
            ptype = PluginType.GENERAL

        return PluginMetadata(
            name=data["name"],
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            license=data.get("license", "MIT"),
            plugin_type=ptype,
            homepage=data.get("homepage", ""),
            repository=data.get("repository", ""),
            python_version_min=data.get("python_version_min", "3.8"),
            engine_version_min=data.get("engine_version_min", "1.0.0"),
            dependencies=deps,
            provides=set(data.get("provides", [])),
            entry_point=data.get("entry_point"),
            config_schema=data.get("config_schema", {}),
            tags=data.get("tags", []),
        )

    # ── Dependency checking ────────────────────────────────────────

    def check_dependencies(self, name: str) -> List[str]:
        unresolved: List[str] = []
        meta = self._registry.get_metadata(name)
        if meta is None:
            return [f"Unknown plugin: {name}"]

        for dep in meta.dependencies:
            dep_meta = self._registry.get_metadata(dep.name)
            if dep_meta is None:
                if dep.required:
                    unresolved.append(
                        f"Dependency '{dep.name}' not found for '{name}'"
                    )
                continue
            if not check_version_satisfied(
                dep_meta.version, dep.version_min, dep.version_max
            ):
                unresolved.append(
                    f"Dependency '{dep.name}' version mismatch for "
                    f"'{name}': found {dep_meta.version}"
                )
        return unresolved

    # ── Loading ────────────────────────────────────────────────────

    def load_plugin(self, name: str) -> Optional[IPlugin]:
        meta = self._registry.get_metadata(name)
        if meta is None:
            logger.error(f"Cannot load unknown plugin: {name}")
            return None

        unresolved = self.check_dependencies(name)
        if unresolved:
            msg = "; ".join(unresolved)
            logger.error(msg)
            self._registry.set_error(name, msg)
            raise PluginDependencyError(unresolved[0])

        plugin = self._instantiate_plugin(meta)
        return self._initialize_plugin(name, plugin, {})

    def load_all(self) -> Dict[str, Optional[IPlugin]]:
        results: Dict[str, Optional[IPlugin]] = {}
        for meta in self._registry.list_discovered():
            name = meta.name
            try:
                results[name] = self.load_plugin(name)
            except PluginError as exc:
                logger.error(f"Plugin load failed for {name}: {exc}")
                results[name] = None
            except Exception:
                logger.error(
                    f"Unexpected error loading plugin {name}",
                    exc_info=True,
                )
                results[name] = None
        return results

    def _instantiate_plugin(self, meta: PluginMetadata) -> IPlugin:
        factory = self._registry._factories.get(meta.name)
        if factory is not None:
            ctx = (
                sandbox_context(self._allowed_modules)
                if self._sandbox_enabled
                else _no_sandbox()
            )
            with ctx:
                return factory()

        candidates: List[Path] = []
        if self._plugins_dir is not None:
            pkg_dir = self._plugins_dir / meta.name
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                candidates.append(pkg_dir)
            py_file = self._plugins_dir / f"{meta.name}.py"
            if py_file.exists():
                candidates.append(py_file)

        for candidate in candidates:
            plugin = self._load_from_path(candidate, meta.name)
            if plugin is not None:
                return plugin

        raise PluginLoadError(
            f"No loadable module found for plugin '{meta.name}'"
        )

    def _load_from_path(
        self, path: Path, name: str
    ) -> Optional[IPlugin]:

        module_name = f"_plugin_{name}".replace("-", "_")

        if path.is_dir():
            spec = importlib.util.spec_from_file_location(
                module_name, str(path / "__init__.py")
            )
        else:
            spec = importlib.util.spec_from_file_location(
                module_name, str(path)
            )

        if spec is None or spec.loader is None:
            return None

        ctx = (
            sandbox_context(self._allowed_modules)
            if self._sandbox_enabled
            else _no_sandbox()
        )
        try:
            with ctx:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
        except ImportError as exc:
            raise PluginLoadError(
                f"Sandbox blocked import in plugin '{name}': {exc}"
            ) from exc
        except Exception as exc:
            raise PluginLoadError(
                f"Failed to load plugin '{name}': {exc}"
            ) from exc

        plugin_cls = self._find_plugin_class(module, name)
        if plugin_cls is None:
            raise PluginLoadError(
                f"No IPlugin implementation found in '{name}'"
            )

        try:
            return plugin_cls()
        except Exception as exc:
            raise PluginLoadError(
                f"Failed to instantiate plugin class in '{name}': {exc}"
            ) from exc

    @staticmethod
    def _find_plugin_class(
        module: Any, name: str
    ) -> Optional[Type[IPlugin]]:
        candidates: List[Type[IPlugin]] = []
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is IPlugin:
                continue
            try:
                if issubclass(obj, IPlugin) and not inspect.isabstract(obj):
                    candidates.append(obj)
            except TypeError:
                pass

        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        for cls in candidates:
            if hasattr(cls, "metadata"):
                try:
                    meta = cls().metadata()
                    if meta.name == name:
                        return cls
                except Exception:
                    pass
        return candidates[0]

    def _initialize_plugin(
        self,
        name: str,
        plugin: IPlugin,
        config: Dict[str, Any],
    ) -> IPlugin:
        plugin.state = PluginState.LOADED

        try:
            plugin.on_load()
        except Exception as exc:
            raise PluginLoadError(
                f"on_load failed for '{name}': {exc}"
            ) from exc

        plugin.state = PluginState.VALIDATED

        try:
            if not plugin.validate():
                raise PluginValidationError(
                    f"Plugin '{name}' failed self-validation"
                )
        except PluginValidationError:
            raise
        except Exception as exc:
            raise PluginValidationError(
                f"Validation error in '{name}': {exc}"
            ) from exc

        plugin.state = PluginState.INITIALIZED

        try:
            plugin.on_init(config)
        except Exception as exc:
            raise PluginInitError(
                f"on_init failed for '{name}': {exc}"
            ) from exc

        self._registry.register_instance(name, plugin)
        logger.info(f"Plugin loaded and initialized: {name}")
        return plugin

    # ── Lifecycle ──────────────────────────────────────────────────

    def start_plugin(self, name: str) -> bool:
        plugin = self._registry.get_instance(name)
        if plugin is None:
            logger.error(f"Plugin not loaded: {name}")
            return False
        try:
            plugin.on_start()
            plugin.state = PluginState.RUNNING
            logger.info(f"Plugin started: {name}")
            return True
        except Exception:
            logger.error(f"Error starting plugin {name}", exc_info=True)
            plugin.state = PluginState.ERROR
            return False

    def start_all(self) -> int:
        count = 0
        for name in self._registry.list_loaded():
            if self.start_plugin(name):
                count += 1
        return count

    def pause_plugin(self, name: str) -> bool:
        plugin = self._registry.get_instance(name)
        if plugin is None:
            return False
        try:
            plugin.on_pause()
            plugin.state = PluginState.PAUSED
            return True
        except Exception:
            logger.error(f"Error pausing plugin {name}", exc_info=True)
            return False

    def resume_plugin(self, name: str) -> bool:
        plugin = self._registry.get_instance(name)
        if plugin is None:
            return False
        try:
            plugin.on_resume()
            plugin.state = PluginState.RUNNING
            return True
        except Exception:
            logger.error(f"Error resuming plugin {name}", exc_info=True)
            return False

    def stop_plugin(self, name: str) -> bool:
        plugin = self._registry.get_instance(name)
        if plugin is None:
            return False
        try:
            plugin.on_stop()
            plugin.state = PluginState.STOPPED
            logger.info(f"Plugin stopped: {name}")
            return True
        except Exception:
            logger.error(f"Error stopping plugin {name}", exc_info=True)
            plugin.state = PluginState.ERROR
            return False

    def stop_all(self) -> int:
        return self._registry.stop_all()

    def unload_plugin(self, name: str) -> bool:
        plugin = self._registry.get_instance(name)
        if plugin is None:
            return False
        try:
            if plugin.state in (PluginState.RUNNING, PluginState.PAUSED):
                plugin.on_stop()
            plugin.on_unload()
            plugin.state = PluginState.UNLOADED
        except Exception:
            logger.error(f"Error unloading plugin {name}", exc_info=True)
            return False
        self._registry._instances.pop(name, None)
        self._registry._factories.pop(name, None)
        logger.info(f"Plugin unloaded: {name}")
        return True

    @property
    def registry(self) -> PluginRegistry:
        return self._registry

    def summary(self) -> Dict[str, Any]:
        return self._registry.summary()
