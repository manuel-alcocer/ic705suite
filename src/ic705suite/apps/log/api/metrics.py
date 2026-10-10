"""Prometheus exporter.

Metrics are read from the log on every scrape rather than kept as running
counters, so the numbers stay correct across restarts and when contacts are
edited or imported outside this process.
"""

from __future__ import annotations

import logging

from ..i18n import _

logger = logging.getLogger(__name__)

NAMESPACE = "hamrlog"


def is_available() -> bool:
    """True when prometheus_client is installed."""
    try:
        import prometheus_client  # noqa: F401
    except ImportError:
        return False
    return True


def build_collector():  # type: ignore[no-untyped-def]
    """Create the custom collector that queries the log at scrape time."""
    from prometheus_client.core import GaugeMetricFamily

    from ..core.services import QsoService

    class LogCollector:
        """Exposes the log counters as Prometheus gauges."""

        def collect(self):  # type: ignore[no-untyped-def]
            stats = QsoService.stats()

            yield GaugeMetricFamily(
                f"{NAMESPACE}_qso_total", "QSOs logged in total", value=stats.total
            )
            yield GaugeMetricFamily(
                f"{NAMESPACE}_qso_today", "QSOs logged today (UTC)", value=stats.today
            )
            yield GaugeMetricFamily(
                f"{NAMESPACE}_unique_callsigns_total",
                "Distinct callsigns worked",
                value=stats.unique_calls,
            )
            yield GaugeMetricFamily(
                f"{NAMESPACE}_countries_total",
                "Distinct DXCC entities worked",
                value=stats.countries,
            )

            by_band = GaugeMetricFamily(
                f"{NAMESPACE}_qso_by_band", "QSOs per band", labels=["band"]
            )
            for band, count in stats.by_band.items():
                by_band.add_metric([band], count)
            yield by_band

            by_mode = GaugeMetricFamily(
                f"{NAMESPACE}_qso_by_mode", "QSOs per mode", labels=["mode"]
            )
            for mode, count in stats.by_mode.items():
                by_mode.add_metric([mode], count)
            yield by_mode

    return LogCollector()


def start_exporter(port: int = 9119) -> bool:
    """Start the HTTP exporter in a background thread.

    Returns:
        True when it started, False when prometheus_client is missing or the
        port is already taken. Never raises: monitoring must not stop the
        operator from logging.
    """
    if not is_available():
        logger.warning("prometheus_client is not installed; metrics exporter disabled")
        return False

    from prometheus_client import REGISTRY, start_http_server

    try:
        REGISTRY.register(build_collector())
        start_http_server(port)
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.warning("could not start metrics exporter on port %s: %s", port, exc)
        return False

    logger.info("metrics exporter listening on port %s", port)
    return True


def serve_forever(port: int = 9119) -> int:
    """Run only the exporter, for ``hamrlog metrics``. Returns an exit code."""
    import time

    if not is_available():
        print(_("Install the metrics extra: pip install hamrlog[metrics]"))
        return 1
    if not start_exporter(port):
        return 1
    print(
        _("Prometheus exporter at http://localhost:{port}/metrics  (Ctrl+C to quit)")
        .format(port=port)
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0
