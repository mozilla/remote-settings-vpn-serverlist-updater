import re

import pytest

from src.records import RecordError, stored_record_errors, validate_stored_records


def record(id="a", code="DE", **fields):
    return {
        "id": id,
        "code": code,
        "name": "Germany",
        "cities": [
            {
                "code": "DE",
                "name": "Germany",
                "servers": [{"port": 2499, "hostname": "de.m1.fastly-masque.net"}],
            }
        ],
        **fields,
    }


def server(record):
    return record["cities"][0]["servers"][0]


def test_stored_record_errors_accepts_the_model():
    quarantined = record("c", "FR")
    server(quarantined)["quarantined"] = True

    for r in [record("a", "US", filter_expression="x", locked=True), record("b"), quarantined]:
        assert stored_record_errors(r) == []


@pytest.mark.parametrize(
    "change,error",
    [
        (lambda r: r.update(extra=1), r"\$: Additional properties .* \('extra' was unexpected\)"),
        (lambda r: r.update(name=""), r"\$\.name: '' should be non-empty"),
        (lambda r: r.update(locked="yes"), r"\$\.locked: 'yes' is not of type 'boolean'"),
        (lambda r: r.update(cities=[]), r"\$\.cities: \[\] should be non-empty"),
        (lambda r: r["cities"][0].pop("servers"), r"\$\.cities\[0\]: 'servers' is a required"),
        (lambda r: server(r).update(port="2499"), r"port: '2499' is not of type 'integer'"),
        (lambda r: server(r).update(port=True), r"port: True is not of type 'integer'"),
        (lambda r: server(r).update(quarantined=1), r"quarantined: 1 is not of type 'boolean'"),
        (lambda r: server(r).update(extra=1), r"servers\[0\]: .* \('extra' was unexpected\)"),
    ],
)
def test_stored_record_errors_rejects_records_off_the_model(change, error):
    bad = record("b", "FR")
    change(bad)

    assert any(re.search(error, e) for e in stored_record_errors(bad)), stored_record_errors(bad)


def test_stored_records_may_carry_server_metadata():
    assert stored_record_errors(record(last_modified=42, schema=1)) == []
    assert stored_record_errors({k: v for k, v in record().items() if k != "id"}) == []


def test_validate_stored_records_accepts_the_model():
    records = [record("a", last_modified=42), record("b", "FR", locked=False)]

    assert validate_stored_records(records) == records


def test_validate_stored_records_rejects_unmodelled_fields():
    with pytest.raises(RecordError, match=r"Record b \(FR\): .*\('extra' was unexpected\)"):
        validate_stored_records([record("a"), record("b", "FR", extra=1)])
