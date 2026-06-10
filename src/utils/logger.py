"""Logging system for AlgoEngine"""

import json
import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class ColoredFormatter(logging.Formatter):
    """Colored log formatter for console output"""
    
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m', # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def format(self, record: logging.LogRecord) -> str:
        log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        record.levelname = f"{log_color}{record.levelname}{reset}"
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """JSON log formatter for ELK stack compatibility.

    Produces structured JSON log entries that Logstash can parse
    without grok patterns, improving indexing performance and
    enabling rich aggregations in Kibana.
    """

    def __init__(
        self,
        service: str = "algoengine",
        environment: str = "production",
        extra_fields: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__()
        self._service = service
        self._environment = environment
        self._extra_fields = extra_fields or {}

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, object] = {
            "timestamp": datetime.utcfromtimestamp(
                record.created
            ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
            "message": record.getMessage(),
            "service": self._service,
            "environment": self._environment,
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
            log_entry["traceback"] = self.formatException(record.exc_info)
        if record.stack_info:
            log_entry["stack_info"] = record.stack_info
        log_entry.update(self._extra_fields)
        return json.dumps(log_entry, default=str)


class Logger:
    """Centralized logging manager for the trading engine"""
    
    _instance: Optional['Logger'] = None
    _loggers: Dict[str, logging.Logger] = {}
    
    def __new__(cls) -> 'Logger':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._log_dir: Path = Path("logs")
        self._log_level = logging.INFO
        self._max_bytes = 10 * 1024 * 1024  # 10MB
        self._backup_count = 5
    
    def setup(
        self,
        log_dir: Optional[str] = None,
        log_level: int = logging.INFO,
        console_output: bool = True,
        file_output: bool = True,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        json_output: bool = False,
        service_name: str = "algoengine",
    ) -> None:
        """Configure global logging settings"""
        if log_dir:
            self._log_dir = Path(log_dir)
        self._log_level = log_level
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        
        self._log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup root logger
        root_logger = logging.getLogger("algoengine")
        root_logger.setLevel(log_level)
        root_logger.handlers = []
        
        # Console handler
        if console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(log_level)
            console_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
            console_handler.setFormatter(ColoredFormatter(console_format))
            root_logger.addHandler(console_handler)
        
        # File handler
        if file_output:
            log_file = self._log_dir / f"algoengine_{datetime.now().strftime('%Y%m%d')}.log"
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            file_handler.setLevel(log_level)
            if json_output:
                file_handler.setFormatter(
                    JsonFormatter(service=service_name)
                )
            else:
                file_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
                file_handler.setFormatter(logging.Formatter(file_format))
            root_logger.addHandler(file_handler)
            
            # Error file handler
            error_file = self._log_dir / f"algoengine_error_{datetime.now().strftime('%Y%m%d')}.log"
            error_handler = logging.handlers.RotatingFileHandler(
                error_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding='utf-8'
            )
            error_handler.setLevel(logging.ERROR)
            if json_output:
                error_handler.setFormatter(
                    JsonFormatter(service=service_name)
                )
            else:
                file_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
                error_handler.setFormatter(logging.Formatter(file_format))
            root_logger.addHandler(error_handler)
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get or create a logger instance"""
        if name not in self._loggers:
            logger = logging.getLogger(f"algoengine.{name}")
            self._loggers[name] = logger
        return self._loggers[name]


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance"""
    return Logger().get_logger(name)


def setup_logging(
    log_dir: Optional[str] = None,
    log_level: int = logging.INFO,
    console_output: bool = True,
    file_output: bool = True,
    json_output: bool = False,
    service_name: str = "algoengine",
) -> None:
    """Setup logging configuration"""
    Logger().setup(
        log_dir=log_dir,
        log_level=log_level,
        console_output=console_output,
        file_output=file_output,
        json_output=json_output,
        service_name=service_name,
    )
