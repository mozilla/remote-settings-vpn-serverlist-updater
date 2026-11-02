from unittest import mock

import pytest

import script
from script import main
from src.fastly import FastlySchemaError
from src.records import RecordError


US_FILTER = 'env.version|versionCompare("151.0a1") >= 0'


@pytest.fixture
def mocked_client():
    # Mock the kinto_http.Client where it is imported.
    with mock.patch("script.kinto_http.Client", spec=True) as mocked_class:
        client = mocked_class()
        client.get_records.return_value = []
        yield client


@pytest.fixture
def mocked_fastly():
    # Mock the Fastly fetch where it is imported.
    with mock.patch("script.fetch_fastly") as mock_fetch:
        mock_fetch.return_value = [fastly_entry("de", "DE", "Germany")]
        yield mock_fetch


@pytest.fixture
def batch_client(mocked_client):
    # Get a handle on the context manager from ``client.batch()``:
    return mocked_client.batch().__enter__()


def fastly_entry(id, country, name, available=True):
    return {
        "id": id,
        "fqdn": f"{id}.m1.fastly-masque.net",
        "country": country,
        "human_readable": name,
        "available": available,
    }


def record(id, code, name, city, locked=None, filter_expression=None):
    return {
        "id": id,
        "last_modified": 42,
        "code": code,
        "name": name,
        "cities": [
            {
                "code": city.upper(),
                "name": name,
                "servers": [{"port": 2499, "hostname": f"{city}.m1.fastly-masque.net"}],
            }
        ],
        **({} if locked is None else {"locked": locked}),
        **({} if filter_expression is None else {"filter_expression": filter_expression}),
    }


def test_cannot_call_unknown_method(mocked_client):
    # Thanks to the spec=True argument, the mock will raise an
    # AttributeError if we try to call a method that doesn't exist
    with pytest.raises(AttributeError):
        mocked_client.unknown_method()


def test_main_anonymous(mocked_client, mocked_fastly, capsys):
    mocked_client.server_info.return_value = {}

    main()

    mocked_client.server_info.assert_called_once()
    assert "⚠️ Anonymous" in capsys.readouterr().out


def test_main_logged_in(mocked_client, mocked_fastly, capsys):
    mocked_client.server_info.return_value = {"user": {"id": "account:bot"}}

    main()

    mocked_client.server_info.assert_called_once()
    assert "Logged in as account:bot" in capsys.readouterr().out


def test_up_to_date(mocked_client, mocked_fastly, batch_client, capsys):
    mocked_fastly.return_value = [
        fastly_entry("de", "DE", "Germany"),
        fastly_entry("us", "US", "United States"),
        fastly_entry("kewr174", "US", "Newark"),
        {
            "id": "closest",
            "fqdn": "p.m1.fastly-masque.net",
            "human_readable": "Closest",
            "available": True,
        },
    ]
    mocked_client.get_records.return_value = [
        record("a", "DE", "Germany", "de", locked=False),
        record("b", "US", "United States", "us", filter_expression=US_FILTER),
        # The production REC record is maintained independently of Fastly's "closest".
        {
            **record("c", "REC", "Recomended Location", "rec", locked=False),
            "cities": [
                {
                    "code": "REC",
                    "name": "Recomended City",
                    "servers": [{"port": 2499, "hostname": "p.m1.fastly-masque.net"}],
                }
            ],
        },
    ]

    main()

    assert "Records are in sync" in capsys.readouterr().out
    batch_client.create_record.assert_not_called()
    batch_client.update_record.assert_not_called()
    mocked_client.request_review.assert_not_called()


CHANGED_CLOSEST = {
    "id": "closest",
    "fqdn": "new.example.com",
    "human_readable": "Closest",
    "available": False,
}


def test_closest_changes_do_not_update_recommended_record(
    mocked_client, mocked_fastly, batch_client
):
    mocked_fastly.return_value = [CHANGED_CLOSEST]
    mocked_client.get_records.return_value = [
        record("rec-id", "REC", "Recomended Location", "rec", locked=False)
    ]

    main()

    batch_client.create_record.assert_not_called()
    batch_client.update_record.assert_not_called()
    batch_client.delete_record.assert_not_called()
    mocked_client.request_review.assert_not_called()


def test_closest_does_not_create_missing_recommended_record(
    mocked_client, mocked_fastly, batch_client
):
    mocked_fastly.return_value = [CHANGED_CLOSEST]
    mocked_client.get_records.return_value = []

    main()

    batch_client.create_record.assert_not_called()
    mocked_client.request_review.assert_not_called()


def test_outdated(mocked_client, mocked_fastly, batch_client, capsys):
    mocked_fastly.return_value = [
        fastly_entry("de", "DE", "Germany"),
        fastly_entry("fr", "FR", "France", available=False),
        fastly_entry("is", "IS", "Iceland"),
        fastly_entry("jp", "JP", "Japan"),
    ]
    mocked_client.get_records.return_value = [
        record("a", "DE", "Germany", "de", locked=False),
        record("b", "FR", "France", "fr", locked=True),
        record("c", "JP", "Old Japan", "jp"),
        record("d", "REC", "Recomended Location", "p"),
    ]
    batch_client.results.return_value = [{}, {}, {}]

    main()

    assert "3 operations ✅" in capsys.readouterr().out
    batch_client.delete_record.assert_not_called()

    # New countries are locked.
    (created,) = [c.kwargs["data"] for c in batch_client.create_record.call_args_list]
    assert created["code"] == "IS"
    assert created["locked"] is True

    # Existing records keep their id and their locked flag, absent included.
    updated = {
        c.kwargs["data"]["id"]: c.kwargs["data"] for c in batch_client.update_record.call_args_list
    }
    assert set(updated) == {"b", "c"}
    assert updated["b"]["locked"] is True
    assert updated["b"]["cities"][0]["servers"][0]["quarantined"] is True
    assert "locked" not in updated["c"]
    assert updated["c"]["name"] == "Japan"

    mocked_client.request_review.assert_called_once_with(message="r?")


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "xx", "fqdn": "xx.m1.fastly-masque.net", "human_readable": "X", "available": True},
        {**fastly_entry("de", "DE", "Germany"), "port": 2499},
        {**fastly_entry("de", "DE", "Germany"), "available": "yes"},
        fastly_entry("uk", "UK", "United Kingdom"),
    ],
)
def test_aborts_when_fastly_does_not_match_schema(
    mocked_client, mocked_fastly, batch_client, entry
):
    mocked_fastly.return_value = [fastly_entry("de", "DE", "Germany"), entry]

    with pytest.raises(FastlySchemaError):
        main()

    mocked_client.get_records.assert_not_called()
    batch_client.create_record.assert_not_called()
    batch_client.update_record.assert_not_called()


def test_aborts_when_a_live_record_has_unmodelled_fields(
    mocked_client, mocked_fastly, batch_client
):
    # Updating it would erase the field.
    mocked_client.get_records.return_value = [{**record("a", "DE", "Germany", "de"), "extra": 1}]

    with pytest.raises(RecordError, match="'extra' was unexpected"):
        main()

    batch_client.create_record.assert_not_called()
    batch_client.update_record.assert_not_called()
    mocked_client.request_review.assert_not_called()


def test_dry_run_does_not_write(mocked_client, mocked_fastly):
    # Writes are disabled by the client itself in dry mode.
    with mock.patch("script.IS_DRY_RUN", True):
        main()

    assert script.kinto_http.Client.call_args.kwargs["dry_mode"] is True


def test_self_approves_on_dev(mocked_client, mocked_fastly, batch_client):
    with mock.patch("script.ENVIRONMENT", "dev"):
        main()

    mocked_client.approve_changes.assert_called_once()
    mocked_client.request_review.assert_not_called()


def test_availability_change_requests_review(mocked_client, mocked_fastly, batch_client):
    mocked_fastly.return_value = [fastly_entry("de", "DE", "Germany", available=False)]
    mocked_client.get_records.return_value = [record("a", "DE", "Germany", "de")]

    main()

    # Every change goes through a human review, even availability alone.
    mocked_client.approve_changes.assert_not_called()
    mocked_client.request_review.assert_called_once_with(message="r?")
