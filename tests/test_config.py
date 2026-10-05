"""Tests for configuration loading."""

import os
import pytest
from src.config import load_sources, env


def test_sources_has_feeds_and_series():
    """Test that sources configuration has the correct feeds and FRED series."""
    sources = load_sources("config/sources.yaml")

    # Check feeds
    feeds = sources.get("feeds", [])
    assert len(feeds) == 6, f"Expected 6 feeds, got {len(feeds)}"

    # Check FRED series
    expected_fred_series = ["DGS10", "DGS5", "SOFR", "DFF", "DGS2", "T10Y2Y",
                            "BAMLH0A0HYM2", "MORTGAGE30US", "CREACBW027SBOG",
                            "DRCRELEXFACBS"]
    actual_fred_series = sources.get("fred_series", [])
    assert actual_fred_series == expected_fred_series, (
        f"FRED series mismatch. Expected {expected_fred_series}, got {actual_fred_series}"
    )


def test_env_missing_raises():
    """Test that env() raises RuntimeError for missing required variables."""
    # Make sure the env var doesn't exist
    test_var_name = "TEST_NONEXISTENT_VAR_XYZ123"
    if test_var_name in os.environ:
        del os.environ[test_var_name]

    # Should raise RuntimeError for missing required env var
    with pytest.raises(RuntimeError, match=f"missing env {test_var_name}"):
        env(test_var_name, required=True)


def test_env_empty_string_raises():
    """Test that env() treats empty strings as missing and raises RuntimeError when required."""
    # Set up an empty environment variable
    test_var_name = "TEST_EMPTY_VAR_ABC456"
    os.environ[test_var_name] = ""

    try:
        # Should raise RuntimeError for empty required env var
        with pytest.raises(RuntimeError, match=f"missing env {test_var_name}"):
            env(test_var_name, required=True)

        # Should return None for empty non-required env var
        result = env(test_var_name, required=False)
        assert result is None, f"Expected None for empty non-required env var, got {result!r}"
    finally:
        # Clean up
        if test_var_name in os.environ:
            del os.environ[test_var_name]
