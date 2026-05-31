"""Configuration management for AlgoEngine"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, TypeVar, Union

import yaml
from dotenv import load_dotenv


T = TypeVar('T', bound='Config')


@dataclass
class DatabaseConfig:
    """Database connection configuration"""
    host: str = "localhost"
    port: int = 5432
    name: str = "algoengine"
    user: str = "algo"
    password: str = ""
    pool_size: int = 10
    max_overflow: int = 20


@dataclass
class RedisConfig:
    """Redis connection configuration"""
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None


@dataclass
class LogConfig:
    """Logging configuration"""
    level: str = "INFO"
    dir: str = "logs"
    max_bytes: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5
    console_output: bool = True
    file_output: bool = True


@dataclass
class DataConfig:
    """Data source configuration"""
    data_dir: str = "data"
    cache_size: int = 1000
    max_history_days: int = 3650
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class TradingConfig:
    """Trading configuration"""
    default_broker: str = "paper"
    paper_trading: bool = True
    max_position_size: float = 1.0
    max_drawdown_pct: float = 0.1
    default_currency: str = "USD"
    commission_rate: float = 0.001
    slippage_model: str = "fixed"
    slippage_value: float = 0.0


@dataclass
class RiskConfig:
    """Risk management configuration"""
    enabled: bool = True
    max_daily_loss_pct: float = 0.02
    max_position_pct: float = 0.1
    max_leverage: float = 1.0
    stop_loss_pct: float = 0.02
    circuit_breaker_enabled: bool = True


@dataclass
class Config:
    """Main configuration class"""
    
    env: str = "development"
    debug: bool = False
    timezone: str = "UTC"
    
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    logging: LogConfig = field(default_factory=LogConfig)
    data: DataConfig = field(default_factory=DataConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    
    custom: Dict[str, Any] = field(default_factory=dict)
    
    _instance: Optional['Config'] = field(default=None, repr=False)
    
    def __new__(cls, *args, **kwargs) -> 'Config':
        if not hasattr(cls, '_instance') or cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @classmethod
    def load(cls, config_path: Optional[Union[str, Path]] = None) -> 'Config':
        """Load configuration from file and environment"""
        # Load .env file
        load_dotenv()
        
        # Load from YAML if provided
        config_data: Dict[str, Any] = {}
        if config_path:
            config_file = Path(config_path)
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f) or {}
        
        # Override with environment variables
        config_data = cls._apply_env_overrides(config_data)
        
        # Create config instance
        instance = cls._instance or cls.__new__(cls)
        
        # Apply configuration values
        instance.env = config_data.get('env', os.getenv('ALGO_ENV', 'development'))
        instance.debug = config_data.get('debug', os.getenv('ALGO_DEBUG', 'false').lower() == 'true')
        instance.timezone = config_data.get('timezone', 'UTC')
        
        # Apply nested configs
        instance.database = DatabaseConfig(**config_data.get('database', {}))
        instance.redis = RedisConfig(**config_data.get('redis', {}))
        instance.logging = LogConfig(**config_data.get('logging', {}))
        instance.data = DataConfig(**config_data.get('data', {}))
        instance.trading = TradingConfig(**config_data.get('trading', {}))
        instance.risk = RiskConfig(**config_data.get('risk', {}))
        instance.custom = config_data.get('custom', {})
        
        cls._instance = instance
        return instance
    
    @classmethod
    def _apply_env_overrides(cls, config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply environment variable overrides"""
        env_mappings = {
            'ALGO_ENV': ('env', str),
            'ALGO_DEBUG': ('debug', lambda x: x.lower() == 'true'),
            'ALGO_TIMEZONE': ('timezone', str),
            'ALGO_DB_HOST': ('database', 'host', str),
            'ALGO_DB_PORT': ('database', 'port', int),
            'ALGO_DB_NAME': ('database', 'name', str),
            'ALGO_DB_USER': ('database', 'user', str),
            'ALGO_DB_PASSWORD': ('database', 'password', str),
            'ALGO_REDIS_HOST': ('redis', 'host', str),
            'ALGO_REDIS_PORT': ('redis', 'port', int),
            'ALGO_LOG_LEVEL': ('logging', 'level', str),
            'ALGO_DATA_DIR': ('data', 'data_dir', str),
        }
        
        for env_var, mapping in env_mappings.items():
            value = os.getenv(env_var)
            if value:
                if len(mapping) == 2:
                    key, converter = mapping
                    config[key] = converter(value)
                else:
                    section, key, converter = mapping
                    if section not in config:
                        config[section] = {}
                    config[section][key] = converter(value)
        
        return config
    
    def save(self, config_path: Union[str, Path]) -> None:
        """Save configuration to file"""
        config_dict = {
            'env': self.env,
            'debug': self.debug,
            'timezone': self.timezone,
            'database': self.database.__dict__,
            'redis': self.redis.__dict__,
            'logging': self.logging.__dict__,
            'data': self.data.__dict__,
            'trading': self.trading.__dict__,
            'risk': self.risk.__dict__,
            'custom': self.custom,
        }
        
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key path"""
        parts = key.split('.')
        value: Any = self
        
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                return default
            
            if value is None:
                return default
        
        return value
    
    def set(self, key: str, value: Any) -> None:
        """Set configuration value by key path"""
        parts = key.split('.')
        target = self
        
        for part in parts[:-1]:
            if isinstance(target, dict):
                if part not in target:
                    target[part] = {}
                target = target[part]
            elif hasattr(target, part):
                target = getattr(target, part)
            else:
                return
        
        if isinstance(target, dict):
            target[parts[-1]] = value
        elif hasattr(target, parts[-1]):
            setattr(target, parts[-1], value)


def get_config() -> Config:
    """Get the global configuration instance"""
    if Config._instance is None:
        Config.load()
    return Config._instance
