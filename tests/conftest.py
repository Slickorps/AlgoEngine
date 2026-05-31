"""Pytest configuration and fixtures"""

import sys
import pytest
import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.utils.config import Config
from src.utils.logger import setup_logging


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_config():
    """Test configuration fixture"""
    config = Config.load()
    config.debug = True
    config.logging.level = "DEBUG"
    config.env = "test"
    return config


@pytest.fixture(scope="function")
def temp_log_dir(tmp_path):
    """Temporary log directory"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    return str(log_dir)


@pytest.fixture(scope="function")
def setup_test_logging(temp_log_dir):
    """Setup test logging"""
    setup_logging(
        log_dir=temp_log_dir,
        log_level=10,  # DEBUG
        console_output=False,
        file_output=True
    )


@pytest.fixture
def sample_symbol():
    """Sample symbol fixture"""
    from src.engine.interfaces import Symbol
    return Symbol(ticker="AAPL", security_type="EQUITY", exchange="NASDAQ")


@pytest.fixture
def sample_bar():
    """Sample bar fixture"""
    from src.engine.interfaces import Bar, Symbol
    return Bar(
        symbol=Symbol(ticker="AAPL", security_type="EQUITY"),
        timestamp=datetime.now(),
        open=Decimal("150.00"),
        high=Decimal("155.00"),
        low=Decimal("148.00"),
        close=Decimal("152.00"),
        volume=Decimal("1000000")
    )
