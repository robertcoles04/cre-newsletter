"""Configuration management for the CRE Blurb pipeline."""

import os
from zoneinfo import ZoneInfo
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv


# Time zone for all "today" logic
ET = ZoneInfo("America/New_York")


def env(name: str, required: bool = True) -> str | None:
    """
    Load an environment variable.

    Args:
        name: The environment variable name
        required: If True, raises RuntimeError if the variable is missing

    Returns:
        The environment variable value, or None if not required and missing

    Raises:
        RuntimeError: If the variable is required and not found
    """
    load_dotenv()  # Load from .env if present (no-op if .env doesn't exist)
    value = os.getenv(name)

    if value is None and required:
        raise RuntimeError(f"missing env {name}")

    return value


def load_sources(path: str = "config/sources.yaml") -> dict:
    """
    Load sources configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file

    Returns:
        A dictionary containing feeds, google_news, fred_series, reit_etf,
        reit_tickers, and lookback_hours
    """
    config_path = Path(path)
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    return config


def http_client() -> httpx.Client:
    """
    Create an HTTP client configured with the required settings.

    Returns:
        An httpx.Client with User-Agent, timeout=30s, and retry transport
    """
    # Use a current Chrome user agent string
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # Create transport with retry logic
    transport = httpx.HTTPTransport(retries=2)

    # Create and return client
    return httpx.Client(
        headers={"User-Agent": user_agent},
        timeout=30,
        transport=transport,
        follow_redirects=True
    )
