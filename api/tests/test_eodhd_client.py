"""The client must surface EODHD failures, never invent data. Responses here are labeled fixtures."""

import httpx
import pytest
import respx

from api.services.eodhd import BASE_URL, OPTIONS_CONTRACTS_PATH, EODHDClient, EODHDError

FIXTURE_QUOTE = {"code": "USO.US", "timestamp": 1788552480, "close": 141.96, "previousClose": 142.09}


@respx.mock
def test_real_time_normalizes_single_object_to_list():
    respx.get(f"{BASE_URL}/real-time/USO.US").mock(return_value=httpx.Response(200, json=FIXTURE_QUOTE))
    quotes = EODHDClient(token="t").real_time(["USO.US"])
    assert quotes == [FIXTURE_QUOTE]


@respx.mock
def test_real_time_batches_extra_symbols_into_s_param():
    route = respx.get(f"{BASE_URL}/real-time/SPY.US").mock(return_value=httpx.Response(200, json=[FIXTURE_QUOTE]))
    EODHDClient(token="t").real_time(["SPY.US", "XLE.US", "FXY.US"])
    assert route.calls.last.request.url.params["s"] == "XLE.US,FXY.US"


@respx.mock
def test_non_200_raises_with_status_and_body():
    respx.get(f"{BASE_URL}/eod/USO.US").mock(return_value=httpx.Response(402, text="Payment Required"))
    from datetime import date

    with pytest.raises(EODHDError) as exc:
        EODHDClient(token="t").eod("USO.US", date(2026, 9, 1), date(2026, 9, 4))
    assert exc.value.status == 402
    assert "Payment Required" in (exc.value.body or "")


@respx.mock
def test_options_probe_reports_403_as_no_entitlement():
    respx.get(f"{BASE_URL}{OPTIONS_CONTRACTS_PATH}").mock(
        return_value=httpx.Response(403, text="<html>Forbidden</html>")
    )
    probe = EODHDClient(token="t").probe_options("AAPL")
    assert probe["ok"] is False
    assert probe["status"] == 403


def test_missing_token_is_an_error_not_a_guess():
    with pytest.raises(EODHDError):
        EODHDClient(token="").user()


@respx.mock
def test_options_probe_reports_200_jsonapi_shape_as_entitled():
    # Labeled fixture: trimmed copy of the real 2026-09-04 response shape.
    fixture = {
        "meta": {"offset": 0, "limit": 2, "fields": ["contract", "bid", "ask", "volatility"]},
        "data": [
            {
                "id": "USO260911C00140000",
                "type": "options-contracts",
                "attributes": {"contract": "USO260911C00140000", "bid": 4.15, "ask": 4.75, "volatility": 0.3881},
            }
        ],
    }
    route = respx.get(f"{BASE_URL}{OPTIONS_CONTRACTS_PATH}").mock(return_value=httpx.Response(200, json=fixture))
    probe = EODHDClient(token="t").probe_options("USO")
    assert probe["ok"] is True and probe["rows"] == 1 and "volatility" in probe["fields"]
    assert route.calls.last.request.url.params["filter[exp_date_from]"]  # expired contracts must be excluded


@respx.mock
def test_options_probe_with_empty_data_is_not_entitled():
    respx.get(f"{BASE_URL}{OPTIONS_CONTRACTS_PATH}").mock(
        return_value=httpx.Response(200, json={"meta": {}, "data": []})
    )
    assert EODHDClient(token="t").probe_options("USO")["ok"] is False
