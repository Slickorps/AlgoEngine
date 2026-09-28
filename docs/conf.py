"""Sphinx configuration for the AlgoEngine documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

project = "AlgoEngine"
author = "AlgoEngine Contributors"
release = "0.1.0"
version = "0.1"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
}
autodoc_mock_imports = [
    "aiohttp",
    "websockets",
    "yfinance",
    "pandas",
    "numpy",
    "pyarrow",
    "redis",
    "prometheus_client",
    "docker",
    "kubernetes",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

myst_enable_extensions = ["colon_fence", "deflist"]
