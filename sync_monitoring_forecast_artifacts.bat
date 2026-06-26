@echo off
setlocal

uv run python scripts/python/sync_monitoring_forecast_artifacts.py %*
