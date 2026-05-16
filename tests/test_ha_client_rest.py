from __future__ import annotations

from typing import Any

import httpx
import pytest

from caesar.ha.client import HAClient
from caesar.ha.models import ServiceCall
from tests.fakeha import VALID_TOKEN, make_rest_app


async def test_list_states(mock_ha: HAClient) -> None:
    states = await mock_ha.list_states()
    assert len(states) == 1
    assert states[0].entity_id == "light.kitchen"
    assert states[0].state == "off"


async def test_get_state_hit(mock_ha: HAClient) -> None:
    state = await mock_ha.get_state("light.kitchen")
    assert state is not None
    assert state.attributes["friendly_name"] == "Kitchen Light"


async def test_get_state_miss_returns_none(mock_ha: HAClient) -> None:
    assert await mock_ha.get_state("light.unknown") is None


async def test_call_service_records_body(
    mock_ha: HAClient, ha_service_calls: list[dict[str, Any]]
) -> None:
    await mock_ha.call_service(
        ServiceCall(
            domain="light",
            service="turn_on",
            target={"entity_id": "light.kitchen"},
            data={"brightness": 128},
        )
    )
    assert ha_service_calls == [
        {
            "domain": "light",
            "service": "turn_on",
            "body": {"brightness": 128, "target": {"entity_id": "light.kitchen"}},
        }
    ]


async def test_list_states_raises_on_http_error() -> None:
    ha_app = make_rest_app(states={}, fail_states_with=500)
    transport = httpx.ASGITransport(app=ha_app)
    http = httpx.AsyncClient(
        base_url="http://ha.test",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        transport=transport,
    )
    client = HAClient(url="http://ha.test", token=VALID_TOKEN, http=http)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.list_states()
    finally:
        await client.aclose()


def test_base_url_is_normalized() -> None:
    client = HAClient(url="http://ha.test/", token="t")
    assert client.base_url == "http://ha.test"


async def test_call_service_with_no_target(
    mock_ha: HAClient, ha_service_calls: list[dict[str, Any]]
) -> None:
    """When target is None, the body must NOT contain a `target` key."""

    await mock_ha.call_service(ServiceCall(domain="homeassistant", service="check_config"))
    assert ha_service_calls == [{"domain": "homeassistant", "service": "check_config", "body": {}}]
