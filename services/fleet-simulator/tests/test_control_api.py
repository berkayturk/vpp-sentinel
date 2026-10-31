from httpx import ASGITransport, AsyncClient

from fleet_simulator.control_api import create_app
from fleet_simulator.faults import FaultState

DRIFT = {"site": "como", "device_type": "charger", "fraction": 0.4}


async def test_post_fault_then_get_reflects_it():
    app = create_app(FaultState())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/faults/firmware-drift", json=DRIFT)
        assert r.status_code == 200
        assert (await c.get("/faults")).json()["firmware_drift"]["site"] == "como"
        assert (await c.delete("/faults")).status_code == 200
        assert (await c.get("/faults")).json()["firmware_drift"] is None


async def test_bad_fault_is_400():
    app = create_app(FaultState())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/faults/meteor", json={})
        assert r.status_code == 400
        assert "meteor" in r.json()["detail"]


async def test_state_getter_sees_latest_state():
    app = create_app(FaultState())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/faults/firmware-drift", json=DRIFT)
    assert app.state.get_faults().firmware_drift is not None
