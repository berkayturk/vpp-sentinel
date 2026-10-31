from httpx import ASGITransport, AsyncClient

from ingestion.api import create_app


async def test_healthz_is_503_until_ready_then_200():
    ready = {"value": False}
    app = create_app(lambda: ready["value"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/healthz")).status_code == 503
        ready["value"] = True
        r = await c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_metrics_endpoint_exposes_ingest_counters():
    app = create_app(lambda: True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/metrics")
        assert r.status_code == 200
        assert "vpp_ingest_messages_total" in r.text
        assert r.headers["content-type"].startswith("text/plain")
