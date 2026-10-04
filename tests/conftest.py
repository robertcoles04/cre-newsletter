"""Pytest configuration and fixtures."""

import os
from pathlib import Path


# Ensure tests run from the project root
os.chdir(Path(__file__).parent.parent)
