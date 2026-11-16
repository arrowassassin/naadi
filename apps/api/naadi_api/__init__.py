"""Naadi API."""

# Single source of the API version: pyproject reads it (hatch dynamic version), the app reports it in
# OpenAPI `info.version`, and it exists in the container (which installs dependencies, not the project).
__version__ = "0.1.0"
