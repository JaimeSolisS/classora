"""Money through the API conventions (M6-02 D5): a test-only probe route.

The production app gets no money route here (``make check-api-client`` shows
no OpenAPI diff).
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.conventions import install_conventions
from app.core.logging import configure_logging
from app.core.money import MONEY_PATTERN, Money


class Amount(BaseModel):
    amount: Money


def build_money_app() -> FastAPI:
    app = FastAPI()
    install_conventions(app)

    @app.post("/money/echo")
    async def echo(body: Amount) -> Amount:
        return Amount(amount=body.amount)

    return app


@pytest.fixture
def money_client() -> Iterator[TestClient]:
    configure_logging()
    with TestClient(build_money_app(), raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize(
    ("sent", "echoed"),
    [("12.5", "12.50"), ("-0", "0.00"), ("999999999999.99", "999999999999.99")],
)
def test_a_money_string_is_echoed_with_two_places(money_client, sent, echoed):
    response = money_client.post("/money/echo", json={"amount": sent})
    assert response.status_code == 200
    assert response.json() == {"amount": echoed}


@pytest.mark.parametrize(
    ("sent", "code"),
    [
        (12.5, "money_invalid"),
        (12, "money_invalid"),
        (87.5, "money_invalid"),
        ("87,50", "money_invalid"),
        ("1.234", "money_precision"),
        ("8.765", "money_precision"),
        ("1234567890123", "money_out_of_range"),
        ("8765432109876", "money_out_of_range"),
    ],
)
def test_invalid_money_is_one_field_error(money_client, sent, code):
    response = money_client.post("/money/echo", json={"amount": sent})
    assert response.status_code == 422
    problem = response.json()
    assert problem["code"] == "validation_error"
    assert [(e["path"], e["code"]) for e in problem["errors"]] == [
        (["body", "amount"], code)
    ]
    # The submitted value never appears in the problem body (#12); the
    # distinctive 8... values are not part of any message.
    if str(sent).startswith("8"):
        assert str(sent) not in response.text
    assert "input" not in problem["errors"][0]


def test_the_probe_openapi_shows_the_money_schema(money_client):
    document = money_client.get("/openapi.json").json()
    schemas = document["components"]["schemas"]
    amount = schemas["Amount"]["properties"]["amount"]
    assert amount == {
        "type": "string",
        "format": "decimal",
        "pattern": MONEY_PATTERN,
        "examples": ["1250.00"],
        "title": "Amount",
    }
