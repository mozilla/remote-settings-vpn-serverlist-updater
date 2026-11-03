import pytest

from src.fastly import FastlySchemaError, validate_fastly_data
from src.records import record_key
from src.transform import (
    KINTO_SERVER_PORT,
    LEGACY_FILTER_EXPRESSIONS,
    add_legacy_filter_expressions,
    assign_ids,
    drop_unsynced_locations,
    fastly_to_kinto,
    one_location_per_country,
    pop_prefix,
)


def entry(id, country=None, available=True):
    return {
        "id": id,
        "fqdn": f"{id}.m1.fastly-masque.net",
        "human_readable": id,
        "available": available,
        **({} if country is None else {"country": country}),
    }


def test_validate_accepts_expected_schema():
    servers = [entry("closest"), entry("kewr174", "US"), entry("de", "DE", available=False)]

    assert validate_fastly_data(servers) == servers


@pytest.mark.parametrize(
    "bad,error",
    [
        (entry("xx"), r"\$\[1\]: 'country' is a required property"),
        # Unsynced locations are still checked, so that upstream drift is caught.
        ({**entry("closest"), "port": 2499}, r"\('port' was unexpected\)"),
        ({k: v for k, v in entry("closest").items() if k != "fqdn"}, "'fqdn' is a required"),
        ({k: v for k, v in entry("de", "DE").items() if k != "fqdn"}, "'fqdn' is a required"),
        ({**entry("de", "DE"), "port": 2499}, r"\('port' was unexpected\)"),
        (entry("de", ["DE"]), r"\$\[1\]\.country: \['DE'\] is not of type 'string'"),
        ({**entry("de", "DE"), "available": "true"}, "available: 'true' is not of type"),
        ({**entry("de", "DE"), "human_readable": ""}, "human_readable: '' should be non-empty"),
        ({**entry("de", "DE"), "fqdn": None}, "fqdn: None is not of type 'string'"),
        ("de", r"\$\[1\]: 'de' is not of type 'object'"),
    ],
)
def test_validate_rejects_structure_mismatch(bad, error):
    with pytest.raises(FastlySchemaError, match=error):
        validate_fastly_data([entry("de", "DE"), bad])


@pytest.mark.parametrize(
    "bad,error",
    [
        (entry("de", "de"), "invalid 'country'"),
        (entry("rec", "REC"), "invalid 'country'"),
        (entry("closest", "UK"), "invalid 'country'"),
        (entry("uk", "UK"), "invalid 'country'"),
        (entry("xx", "XX"), "invalid 'country'"),
        (entry("de", "DE\n"), "invalid 'country'"),
        ({**entry("de", "DE"), "fqdn": "de.example.com\n"}, "invalid 'fqdn'"),
        (entry("de-1", "DE"), "invalid 'id'"),
        (entry("123", "DE"), "invalid 'id'"),
    ],
)
def test_validate_rejects_semantic_mismatch(bad, error):
    with pytest.raises(FastlySchemaError, match=error):
        validate_fastly_data([entry("de", "DE"), bad])


@pytest.mark.parametrize("response", [[], {}, None])
def test_validate_rejects_empty_response(response):
    with pytest.raises(FastlySchemaError):
        validate_fastly_data(response)


def test_drop_unsynced_locations():
    servers = [entry("closest"), entry("de", "DE"), entry("closest", "US")]

    assert drop_unsynced_locations(servers) == [entry("de", "DE")]
    assert len(servers) == 3  # input is not mutated


def test_one_location_per_country():
    servers = [entry("us", "US"), entry("kewr174", "US"), entry("yyz45", "CA"), entry("de", "DE")]

    collapsed = one_location_per_country(servers)

    assert [s["id"] for s in collapsed] == ["us", "yyz45", "de"]


def test_one_location_per_country_without_aggregate():
    with pytest.raises(ValueError, match="CA"):
        one_location_per_country([entry("yyz45", "CA"), entry("yul1", "CA")])


def test_one_location_per_country_with_several_aggregates():
    with pytest.raises(ValueError, match="US"):
        one_location_per_country([entry("us", "US"), entry("us", "US"), entry("kewr174", "US")])


@pytest.mark.parametrize(
    "fastly_id,expected",
    [("eglc860", "EGLC"), ("us", "US"), ("kewr174", "KEWR"), ("123", ""), ("", "")],
)
def test_pop_prefix(fastly_id, expected):
    assert pop_prefix(fastly_id) == expected


def test_fastly_to_kinto():
    records = fastly_to_kinto([entry("eglc860", "GB"), entry("de", "DE", available=False)])

    assert records == [
        {
            "code": "GB",
            "name": "eglc860",
            "cities": [
                {
                    "code": "EGLC",
                    "name": "eglc860",
                    "servers": [
                        {"port": KINTO_SERVER_PORT, "hostname": "eglc860.m1.fastly-masque.net"}
                    ],
                }
            ],
        },
        {
            "code": "DE",
            "name": "de",
            "cities": [
                {
                    "code": "DE",
                    "name": "de",
                    "servers": [
                        {
                            "port": KINTO_SERVER_PORT,
                            "hostname": "de.m1.fastly-masque.net",
                            "quarantined": True,
                        }
                    ],
                }
            ],
        },
    ]


def test_add_legacy_filter_expressions():
    records = [{"code": "US"}, {"code": "DE"}]

    stamped = add_legacy_filter_expressions(records)

    assert stamped == [
        {"code": "US", "filter_expression": LEGACY_FILTER_EXPRESSIONS["US"]},
        {"code": "DE"},
    ]
    assert records[0] == {"code": "US"}  # input is not mutated


def test_record_key_includes_filter_expression():
    assert record_key({"code": "US"}) == ("US", "")
    assert record_key({"code": "US", "filter_expression": "x"}) == ("US", "x")


def test_assign_ids_matches_on_code_and_filter():
    desired = [
        {"code": "US", "filter_expression": "new"},
        {"code": "US", "filter_expression": "old"},
    ]
    existing = [
        {"id": "a", "code": "US", "filter_expression": "old"},
        {"id": "b", "code": "US", "filter_expression": "new"},
    ]

    assert [r["id"] for r in assign_ids(desired, existing)] == ["b", "a"]


@pytest.mark.parametrize("locked", [True, False])
def test_assign_ids_carries_locked(locked):
    (result,) = assign_ids([{"code": "DE"}], [{"id": "a", "code": "DE", "locked": locked}])

    assert result == {"id": "a", "code": "DE", "locked": locked}


def test_assign_ids_leaves_absent_locked_absent():
    (result,) = assign_ids([{"code": "DE"}], [{"id": "a", "code": "DE"}])

    assert result == {"id": "a", "code": "DE"}


def test_assign_ids_locks_new_countries():
    desired = [{"code": "IS"}]

    (result,) = assign_ids(desired, [{"id": "a", "code": "DE", "locked": False}])

    assert result["locked"] is True
    assert result["id"]
    assert desired == [{"code": "IS"}]  # input is not mutated
