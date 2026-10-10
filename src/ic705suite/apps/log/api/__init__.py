"""Integration surface: Prometheus metrics and an optional REST API.

Neither module is imported by the TUI. Both are optional extras so the core
application keeps a minimal dependency footprint:

    pip install hamrlog[metrics]   # Prometheus exporter
    pip install hamrlog[api]       # FastAPI server
"""
