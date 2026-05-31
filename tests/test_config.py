"""Tests for configuration management"""

from src.utils.config import Config, get_config


class TestConfig:
    """Test configuration management"""
    
    def test_config_singleton(self, test_config):
        """Test that Config is a singleton"""
        config1 = get_config()
        config2 = Config.load()
        assert config1 is config2
    
    def test_default_values(self, test_config):
        """Test default configuration values"""
        # Config may already be loaded, just verify core structure
        assert test_config.timezone == "UTC"
        assert test_config.database.host == "localhost"
        assert test_config.database.port == 5432
    
    def test_get_value(self, test_config):
        """Test getting values by key path"""
        assert test_config.get("database.host") == "localhost"
        assert test_config.get("database.port") == 5432
        assert test_config.get("nonexistent", "default") == "default"
    
    def test_set_value(self, test_config):
        """Test setting values by key path"""
        test_config.set("database.host", "newhost")
        assert test_config.database.host == "newhost"
    
    def test_env_override(self, monkeypatch):
        """Test environment variable overrides"""
        monkeypatch.setenv("ALGO_DB_HOST", "override_host")
        monkeypatch.setenv("ALGO_DB_PORT", "9999")
        
        config = Config.load()
        assert config.database.host == "override_host"
        assert config.database.port == 9999


class TestConfigSaveLoad:
    """Test configuration save and load"""
    
    def test_save_and_load(self, test_config, tmp_path):
        """Test saving and loading configuration"""
        config_path = tmp_path / "config.yaml"
        test_config.save(config_path)
        
        assert config_path.exists()
        
        # Load and verify
        loaded = Config.load(config_path)
        assert loaded.env == test_config.env
