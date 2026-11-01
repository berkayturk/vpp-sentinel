"""The read clients: happy paths over canned responses, and a 500 becomes ClientError."""

from datetime import UTC, datetime

import pytest

from agent.clients import alertmanager, grafana, loki, prometheus
from agent.clients.errors import ClientError
from tests.fakes import (
    ALERTMANAGER_ALERTS,
    Recorder,
    client,
    failing_router,
    json_router,
    loki_lines,
    prometheus_instant,
    prometheus_range,
)

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 11, 1, 10, 10, tzinfo=UTC)


async def test_active_alerts_keeps_only_active_ones():
    rec = Recorder()
    async with client(json_router({"/api/v2/alerts": ALERTMANAGER_ALERTS}, rec)) as http:
        alerts = await alertmanager.active_alerts(http)
    assert [a.name for a in alerts] == ["VppSiteDevicesDark"]
    assert alerts[0].labels["site"] == "como"
    assert alerts[0].starts_at == datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    assert alerts[0].severity == "critical"
    assert rec.requests[0].url.params["active"] == "true"


async def test_prometheus_instant_returns_samples():
    rec = Recorder()
    body = prometheus_instant([({"site": "como"}, 18.0), ({"site": "varese"}, 15.0)])
    async with client(json_router({"/api/v1/query": body}, rec)) as http:
        samples = await prometheus.instant(http, "vpp_devices_reporting")
    assert [(s.labels["site"], s.value) for s in samples] == [("como", 18.0), ("varese", 15.0)]
    assert rec.requests[0].url.params["query"] == "vpp_devices_reporting"


async def test_prometheus_range_returns_series():
    rec = Recorder()
    body = prometheus_range([({"site": "como"}, [(1761991200, 12.0), (1761991260, 600.0)])])
    async with client(json_router({"/api/v1/query_range": body}, rec)) as http:
        series = await prometheus.range_query(http, "rate(x[1m])", T0, T1, step_seconds=60)
    assert series[0].labels == {"site": "como"}
    assert series[0].values[-1][1] == 600.0
    assert rec.requests[0].url.params["step"] == "60"


async def test_loki_query_range_returns_lines_in_time_order():
    rec = Recorder()
    body = loki_lines([(1761991260, '{"event":"b"}'), (1761991200, '{"event":"a"}')])
    async with client(json_router({"/loki/api/v1/query_range": body}, rec)) as http:
        lines = await loki.query_range(http, '{app="ingestion"}', T0, T1, limit=50)
    assert [ln.line for ln in lines] == ['{"event":"a"}', '{"event":"b"}']
    assert rec.requests[0].url.params["limit"] == "50"


async def test_grafana_annotate_posts_text_and_tags():
    rec = Recorder()
    async with client(json_router({"/api/annotations": {"id": 7}}, rec)) as http:
        annotation_id = await grafana.annotate(http, "page: VppSiteDevicesDark como", ["page"])
    assert annotation_id == 7
    sent = rec.requests[0].read().decode()
    assert "page: VppSiteDevicesDark como" in sent and '"tags"' in sent


@pytest.mark.parametrize(
    "call",
    [
        lambda http: alertmanager.active_alerts(http),
        lambda http: prometheus.instant(http, "up"),
        lambda http: prometheus.range_query(http, "up", T0, T1, step_seconds=60),
        lambda http: loki.query_range(http, '{a="b"}', T0, T1, limit=1),
        lambda http: grafana.annotate(http, "x", ["page"]),
    ],
)
async def test_server_errors_become_client_errors(call):
    async with client(failing_router(Recorder())) as http:
        with pytest.raises(ClientError) as info:
            await call(http)
    assert "500" in str(info.value)


async def test_malformed_json_becomes_client_error():
    rec = Recorder()
    routes = {"/api/v1/query": {"status": "success", "data": {"result": "not-a-list"}}}
    async with client(json_router(routes, rec)) as http:
        with pytest.raises(ClientError):
            await prometheus.instant(http, "up")
