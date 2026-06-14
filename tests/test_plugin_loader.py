"""Tests for the plugin loader system.

Covers:
    - Plugin discovery (directory scanning)
    - Plugin metadata parsing (JSON + TOML manifests)
    - Plugin lifecycle (load → init → start → pause → resume → stop → unload)
    - Dependency resolution and error handling
    - Import sandbox
    - Registry operations
"""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict

import pytest

from src.plugins.interface import (
    IPlugin,
    PluginLoadError,
    PluginInitError,
    PluginDependencyError,
    PluginValidationError,
    PluginMetadata,
    PluginState,
    PluginType,
)
from src.plugins.loader import (
    PluginLoader,
    PluginRegistry,
    ImportSandbox,
    sandbox_context,
    check_version_satisfied,
)


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    """Reset the singleton registry between tests."""
    registry = PluginRegistry.instance()
    registry.clear()
    yield
    registry.clear()


@pytest.fixture
def tmp_plugins_dir():
    """Create a temporary plugins directory and return its path."""
    with TemporaryDirectory(prefix="algo_plugins_") as tmp:
        yield Path(tmp)


# ── Test Plugin Classes ───────────────────────────────────────────


class _TestPlugin(IPlugin):
    """A simple test plugin used across multiple tests."""

    def __init__(self) -> None:
        self._started = False
        self._stopped = False
        self._init_config: Dict[str, Any] = {}
        self._state = PluginState.DISCOVERED

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="test-plugin",
            version="1.0.0",
            plugin_type=PluginType.GENERAL,
            author="Test",
            description="Test plugin",
        )

    def on_load(self) -> None:
        pass

    def on_init(self, config: Dict[str, Any]) -> None:
        self._init_config = config

    def on_start(self) -> None:
        self._started = True

    def on_stop(self) -> None:
        self._stopped = True

    @property
    def state(self) -> PluginState:
        return self._state

    @state.setter
    def state(self, value: PluginState) -> None:
        self._state = value


class _FailingPlugin(_TestPlugin):
    """Plugin that fails during on_init."""

    def on_init(self, config: Dict[str, Any]) -> None:
        raise RuntimeError("Simulated init failure")


class _InvalidPlugin(_TestPlugin):
    """Plugin that fails self-validation."""

    def validate(self) -> bool:
        return False


class _StrategyPlugin(IPlugin):
    """A plugin pretending to be a trading strategy."""

    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="strategy-plugin",
            version="2.0.0",
            plugin_type=PluginType.STRATEGY,
        )


# ── Version Checking ──────────────────────────────────────────────


class TestVersionChecking:
    def test_exact_match(self):
        assert check_version_satisfied("1.0.0", "1.0.0", "1.0.0")

    def test_within_range(self):
        assert check_version_satisfied("1.5.0", "1.0.0", "2.0.0")

    def test_below_minimum(self):
        assert not check_version_satisfied("0.9.0", "1.0.0", "2.0.0")

    def test_above_maximum(self):
        assert not check_version_satisfied("3.0.0", "1.0.0", "2.0.0")

    def test_min_only(self):
        assert check_version_satisfied("2.0.0", "1.0.0")

    def test_max_only(self):
        assert check_version_satisfied("0.5.0", maximum="1.0.0")

    def test_none_actual_no_min(self):
        assert check_version_satisfied(None)

    def test_none_actual_with_min(self):
        assert not check_version_satisfied(None, "1.0.0")

    def test_invalid_actual(self):
        assert not check_version_satisfied("abc", "1.0.0")

    def test_invalid_minimum(self):
        assert check_version_satisfied("1.0.0", "invalid")

    def test_prerelease_version(self):
        assert check_version_satisfied("1.0.0-alpha")

    def test_build_metadata(self):
        assert check_version_satisfied("1.0.0+build.1", "1.0.0")


# ── Registry ──────────────────────────────────────────────────────


class TestPluginRegistry:
    def test_singleton(self):
        r1 = PluginRegistry.instance()
        r2 = PluginRegistry.instance()
        assert r1 is r2

    def test_register_and_retrieve_metadata(self):
        registry = PluginRegistry.instance()
        meta = PluginMetadata(name="test", version="1.0")
        registry.register_discovered(meta)
        assert registry.get_metadata("test") is meta

    def test_duplicate_discovery_silently_ignored(self):
        registry = PluginRegistry.instance()
        meta1 = PluginMetadata(name="test", version="1.0")
        meta2 = PluginMetadata(name="test", version="2.0")
        registry.register_discovered(meta1)
        registry.register_discovered(meta2)
        assert len(registry.list_discovered()) == 1
        assert registry.get_metadata("test").version == "1.0"

    def test_list_by_type(self):
        registry = PluginRegistry.instance()
        registry.register_discovered(
            PluginMetadata(name="s1", version="1.0",
                           plugin_type=PluginType.STRATEGY)
        )
        registry.register_discovered(
            PluginMetadata(name="i1", version="1.0",
                           plugin_type=PluginType.INDICATOR)
        )
        strategies = registry.list_by_type(PluginType.STRATEGY)
        assert len(strategies) == 1
        assert strategies[0].name == "s1"

    def test_error_management(self):
        registry = PluginRegistry.instance()
        registry.set_error("p1", "failed")
        assert "p1" in registry.get_errors()
        registry.clear_error("p1")
        assert "p1" not in registry.get_errors()

    def test_summary(self):
        registry = PluginRegistry.instance()
        registry.register_discovered(PluginMetadata(name="a", version="1.0"))
        summary = registry.summary()
        assert summary["discovered"] == 1
        assert summary["loaded"] == 0
        assert summary["errors"] == 0

    def test_clear(self):
        registry = PluginRegistry.instance()
        registry.register_discovered(PluginMetadata(name="a", version="1.0"))
        registry.clear()
        assert len(registry.list_discovered()) == 0


# ── Discovery ─────────────────────────────────────────────────────


class TestDiscovery:
    def test_discover_empty_directory(self, tmp_plugins_dir):
        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        result = loader.discover()
        assert result == []

    def test_discover_plugin_with_json_manifest(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "my-plugin"
        pkg.mkdir()
        manifest = {
            "name": "my-plugin",
            "version": "1.2.3",
            "plugin_type": "STRATEGY",
            "description": "A test plugin",
            "author": "Test Author",
            "license": "MIT",
            "tags": ["demo"],
            "dependencies": [
                {"name": "other-plugin", "version_min": "0.1.0"}
            ],
        }
        (pkg / "plugin.json").write_text(json.dumps(manifest))
        (pkg / "__init__.py").write_text("")

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        result = loader.discover()
        assert len(result) == 1
        meta = result[0]
        assert meta.name == "my-plugin"
        assert meta.version == "1.2.3"
        assert meta.plugin_type == PluginType.STRATEGY
        assert "demo" in meta.tags
        assert len(meta.dependencies) == 1
        assert meta.dependencies[0].name == "other-plugin"

    def test_discover_plugin_py_file(self, tmp_plugins_dir):
        py_file = tmp_plugins_dir / "my_plugin.py"
        py_file.write_text("""
class MyPlugin(IPlugin):
    def metadata(self):
        return PluginMetadata(name="my_plugin", version="1.0")
""")

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        result = loader.discover()
        assert len(result) == 1
        assert result[0].name == "my_plugin"
        assert result[0].version == "0.1.0"

    def test_discover_hidden_files_ignored(self, tmp_plugins_dir):
        hidden = tmp_plugins_dir / ".hidden_plugin"
        hidden.mkdir()
        (hidden / "__init__.py").write_text("")

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        result = loader.discover()
        assert result == []

    def test_discover_dir_without_init(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "incomplete"
        pkg.mkdir()
        # No __init__.py, no manifest

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        result = loader.discover()
        assert result == []


# ── Loading ───────────────────────────────────────────────────────


class TestLoading:
    def test_load_simple_plugin(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "test-plugin"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState

class TestPlugin(IPlugin):
    def __init__(self):
        self._state = PluginState.DISCOVERED
    def metadata(self):
        return PluginMetadata(name="test-plugin", version="1.0", plugin_type=PluginType.GENERAL)
    def on_load(self): pass
    def on_init(self, config): pass
    def on_start(self): pass
    def on_stop(self): pass
    @property
    def state(self): return self._state
    @state.setter
    def state(self, v): self._state = v
""")
        (pkg / "plugin.json").write_text(json.dumps({
            "name": "test-plugin",
            "version": "1.0.0",
        }))

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        loaded = loader.load_all()

        assert "test-plugin" in loaded
        assert loaded["test-plugin"] is not None
        assert loaded["test-plugin"].state == PluginState.INITIALIZED

    def test_load_missing_plugin(self, tmp_plugins_dir):
        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        with pytest.raises(PluginLoadError):
            loader._instantiate_plugin(
                PluginMetadata(name="nonexistent", version="1.0")
            )

    def test_load_failing_plugin(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "failing"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState

class FailingPlugin(IPlugin):
    def metadata(self):
        return PluginMetadata(name="failing", version="1.0")
    def on_init(self, config):
        raise RuntimeError("intentional")
    @property
    def state(self): return PluginState.DISCOVERED
    @state.setter
    def state(self, v): pass
""")
        (pkg / "plugin.json").write_text(json.dumps({
            "name": "failing", "version": "1.0.0"
        }))

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        with pytest.raises(PluginInitError, match="intentional"):
            loader.load_plugin("failing")

    def test_load_invalid_plugin(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "invalid"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState

class InvalidPlugin(IPlugin):
    def metadata(self):
        return PluginMetadata(name="invalid", version="1.0")
    def validate(self):
        return False
    @property
    def state(self): return PluginState.DISCOVERED
    @state.setter
    def state(self, v): pass
""")
        (pkg / "plugin.json").write_text(json.dumps({
            "name": "invalid", "version": "1.0.0"
        }))

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        with pytest.raises(PluginValidationError):
            loader.load_plugin("invalid")

    def test_load_with_dependency_missing(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "needs-dep"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "plugin.json").write_text(json.dumps({
            "name": "needs-dep",
            "version": "1.0.0",
            "dependencies": [
                {"name": "missing-plugin", "required": True}
            ],
        }))

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        with pytest.raises(PluginDependencyError, match="not found"):
            loader.load_plugin("needs-dep")

    def test_load_with_optional_missing_dependency(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "optional-dep"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState

class OptionalDepPlugin(IPlugin):
    def __init__(self):
        object.__setattr__(self, "_state", PluginState.DISCOVERED)
    def metadata(self):
        return PluginMetadata(name="optional-dep", version="1.0")
    def on_load(self): pass
    def on_init(self, c): pass
    def on_start(self): pass
    def on_stop(self): pass
    @property
    def state(self): return self.__dict__.get("_state", PluginState.DISCOVERED)
    @state.setter
    def state(self, v): object.__setattr__(self, "_state", v)
""")
        (pkg / "plugin.json").write_text(json.dumps({
            "name": "optional-dep",
            "version": "1.0.0",
            "dependencies": [
                {"name": "missing-plugin", "required": False}
            ],
        }))

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        plugin = loader.load_plugin("optional-dep")
        assert plugin is not None
        assert plugin.state == PluginState.INITIALIZED

    def test_load_all_mixed_results(self, tmp_plugins_dir):
        good_pkg = tmp_plugins_dir / "good"
        good_pkg.mkdir()
        (good_pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState
class GoodPlugin(IPlugin):
    def metadata(self):
        return PluginMetadata(name="good", version="1.0")
    def on_load(self): pass
    def on_init(self, c): pass
    def on_start(self): pass
    def on_stop(self): pass
    @property
    def state(self): return PluginState.DISCOVERED
    @state.setter
    def state(self, v): pass
""")
        (good_pkg / "plugin.json").write_text(
            json.dumps({"name": "good", "version": "1.0.0"})
        )

        bad_pkg = tmp_plugins_dir / "bad-dep"
        bad_pkg.mkdir()
        (bad_pkg / "__init__.py").write_text("")
        (bad_pkg / "plugin.json").write_text(json.dumps({
            "name": "bad-dep",
            "version": "1.0.0",
            "dependencies": [{"name": "ghost", "required": True}],
        }))

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        results = loader.load_all()

        assert "good" in results
        assert results["good"] is not None
        assert "bad-dep" in results
        assert results["bad-dep"] is None


# ── Lifecycle ─────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_stop_cycle(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "lifecycle"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState

class LifecyclePlugin(IPlugin):
    def __init__(self):
        self._state = PluginState.DISCOVERED
        self.started = False
        self.stopped = False
        self.paused = False
        self.resumed = False
    def metadata(self):
        return PluginMetadata(name="lifecycle", version="1.0")
    def on_load(self): pass
    def on_init(self, c): pass
    def on_start(self):
        self.started = True
    def on_pause(self):
        self.paused = True
    def on_resume(self):
        self.resumed = True
    def on_stop(self):
        self.stopped = True
    @property
    def state(self): return self._state
    @state.setter
    def state(self, v): self._state = v
""")
        (pkg / "plugin.json").write_text(
            json.dumps({"name": "lifecycle", "version": "1.0.0"})
        )

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        plugin = loader.load_plugin("lifecycle")
        assert plugin is not None

        loader.start_plugin("lifecycle")
        assert plugin.state == PluginState.RUNNING

        loader.pause_plugin("lifecycle")
        assert plugin.state == PluginState.PAUSED

        loader.resume_plugin("lifecycle")
        assert plugin.state == PluginState.RUNNING

        loader.stop_plugin("lifecycle")
        assert plugin.state == PluginState.STOPPED

        loader.unload_plugin("lifecycle")
        assert plugin.state == PluginState.UNLOADED

    def test_start_all_stop_all(self, tmp_plugins_dir):
        for i in range(3):
            pkg = tmp_plugins_dir / f"multi-{i}"
            pkg.mkdir()
            (pkg / "__init__.py").write_text(f"""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState
class Plugin{i}(IPlugin):
    def __init__(self):
        self._state = PluginState.DISCOVERED
    def metadata(self):
        return PluginMetadata(name="multi-{i}", version="1.0")
    def on_load(self): pass
    def on_init(self, c): pass
    def on_start(self): pass
    def on_stop(self): pass
    @property
    def state(self): return self._state
    @state.setter
    def state(self, v): self._state = v
""")
            (pkg / "plugin.json").write_text(
                json.dumps({"name": f"multi-{i}", "version": "1.0.0"})
            )

        loader = PluginLoader(plugins_dir=str(tmp_plugins_dir))
        loader.discover()
        loader.load_all()
        started = loader.start_all()
        assert started == 3

        stopped = loader.stop_all()
        assert stopped == 3


# ── Import Sandbox ────────────────────────────────────────────────


class TestImportSandbox:
    def test_allows_whitelisted_module(self):
        sandbox = ImportSandbox({"json"})
        assert sandbox.find_spec("json", None) is None

    def test_blocks_disallowed_module(self):
        sandbox = ImportSandbox({"json"})
        with pytest.raises(ImportError, match="os"):
            sandbox.find_spec("os", None)

    def test_allows_submodule_of_allowed(self):
        sandbox = ImportSandbox({"json"})
        assert sandbox.find_spec("json.decoder", None) is None

    def test_context_manager_activates_and_deactivates(self):
        original_count = len(sys.meta_path)
        with sandbox_context({"json"}):
            assert len(sys.meta_path) == original_count + 1
        assert len(sys.meta_path) == original_count

    def test_context_manager_removes_only_its_own(self):
        with sandbox_context({"json"}):
            assert any(isinstance(f, ImportSandbox) for f in sys.meta_path)
        assert not any(isinstance(f, ImportSandbox) for f in sys.meta_path)

    def test_sandbox_with_plugin_loading(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "sandboxed"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
from src.plugins.interface import IPlugin, PluginMetadata, PluginType, PluginState

class SandboxedPlugin(IPlugin):
    def metadata(self):
        return PluginMetadata(name="sandboxed", version="1.0")
    def on_load(self): pass
    def on_init(self, c): pass
    def on_start(self): pass
    def on_stop(self): pass
    @property
    def state(self): return PluginState.DISCOVERED
    @state.setter
    def state(self, v): pass
""")
        (pkg / "plugin.json").write_text(
            json.dumps({"name": "sandboxed", "version": "1.0.0"})
        )

        loader = PluginLoader(
            plugins_dir=str(tmp_plugins_dir),
            sandbox_enabled=True,
        )
        loader.discover()
        plugin = loader.load_plugin("sandboxed")
        assert plugin is not None

    def test_sandbox_blocks_forbidden_import(self, tmp_plugins_dir):
        pkg = tmp_plugins_dir / "bad-import"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("""
import pstats
""")
        (pkg / "plugin.json").write_text(
            json.dumps({"name": "bad-import", "version": "1.0.0"})
        )

        loader = PluginLoader(
            plugins_dir=str(tmp_plugins_dir),
            sandbox_enabled=True,
        )
        loader.discover()
        with pytest.raises(PluginLoadError):
            loader.load_plugin("bad-import")


# ── Metadata Parsing ──────────────────────────────────────────────


class TestManifestParsing:
    def test_invalid_json_returns_none(self, tmp_plugins_dir):
        path = tmp_plugins_dir / "bad.json"
        path.write_text("{invalid json")
        result = PluginLoader._parse_json_manifest(path)
        assert result is None

    def test_valid_json_with_minimal_fields(self, tmp_plugins_dir):
        path = tmp_plugins_dir / "minimal.json"
        path.write_text(json.dumps({"name": "minimal"}))
        meta = PluginLoader._parse_json_manifest(path)
        assert meta is not None
        assert meta.name == "minimal"
        assert meta.version == "0.1.0"
        assert meta.plugin_type == PluginType.GENERAL

    def test_invalid_plugin_type_falls_back(self, tmp_plugins_dir):
        path = tmp_plugins_dir / "badtype.json"
        path.write_text(json.dumps({
            "name": "weird",
            "plugin_type": "SUPER_WEIRD_TYPE",
        }))
        meta = PluginLoader._parse_json_manifest(path)
        assert meta is not None
        assert meta.plugin_type == PluginType.GENERAL
