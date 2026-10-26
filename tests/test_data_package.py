"""Tests for the data package exports and optional storage fallback."""

import importlib
import sys
from unittest.mock import patch


def test_public_exports():
    import src.data as data

    assert data.Symbol is not None
    assert data.Tick is not None
    assert data.DataFeed is not None
    assert data.StreamingDataFeed is not None
    assert data.DataCache is not None
    assert data.DataAggregator is not None
    assert "DataStorage" in data.__all__


def test_missing_storage_falls_back_to_none():
    import src.data as data

    with patch.dict(sys.modules, {"src.data.storage": None}):
        reloaded = importlib.reload(data)
        assert reloaded.DataStorage is None

    restored = importlib.reload(data)
    assert restored.DataStorage is not None
