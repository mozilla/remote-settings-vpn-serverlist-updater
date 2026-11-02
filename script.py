import os
import sys

import kinto_http
import sentry_sdk
from kinto_http.utils import collection_diff
from sentry_sdk.integrations.gcp import GcpIntegration

from src.fastly import fetch_fastly, validate_fastly_data
from src.records import validate_stored_records
from src.transform import (
    add_legacy_filter_expressions,
    assign_ids,
    drop_unsynced_locations,
    fastly_to_kinto,
    one_location_per_country,
)
from src.util import pipe


AUTHORIZATION = os.getenv("AUTHORIZATION", "")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local").lower()
if ENVIRONMENT not in {"local", "dev", "stage", "prod"}:
    raise ValueError(f"'ENVIRONMENT={ENVIRONMENT}' is not a valid value")
SERVER = os.getenv(
    "SERVER",
    {
        "local": "http://localhost:8888/v1",
        "dev": "https://remote-settings-dev.allizom.org/v1",
        "stage": "https://remote-settings.allizom.org/v1",
        "prod": "https://remote-settings.mozilla.org/v1",
    }[ENVIRONMENT],
)
IS_DRY_RUN = os.getenv("DRY_RUN", "0") in "1yY"
SENTRY_DSN = os.getenv("SENTRY_DSN")
SENTRY_ENV = os.getenv("SENTRY_ENV", ENVIRONMENT)


COLLECTION_NAME = "vpn-serverlist"


def get_source_records():
    """
    Turn the Fastly VPN locations into `vpn-serverlist` records.
    Raises if the Fastly response does not match the expected schema, which aborts the
    sync before anything is written (and reports the error to Sentry).
    """
    return pipe(
        fetch_fastly(),
        validate_fastly_data,
        # "closest" is validated, but its REC record is maintained in Remote Settings.
        drop_unsynced_locations,
        one_location_per_country,
        fastly_to_kinto,
        # Fastly has no filter expressions: put back the ones live records are gated by
        # (the US record targets Firefox >= 151), so that they still match.
        add_legacy_filter_expressions,
    )


def main():
    if SENTRY_DSN:
        # Initialize Sentry for error reporting.
        sentry_sdk.init(SENTRY_DSN, integrations=[GcpIntegration()], environment=SENTRY_ENV)
    else:
        print("⚠️ Sentry is not configured. Set SENTRY_DSN environment variable to enable it.")

    client = kinto_http.Client(
        server_url=SERVER,
        auth=AUTHORIZATION,
        bucket="main-workspace",
        collection=COLLECTION_NAME,
        dry_mode=IS_DRY_RUN,
    )

    # Check credentials.
    print("Fetch server info...", end="")
    server_info = client.server_info()
    print("✅")
    if "user" not in server_info:
        print("⚠️ Anonymous")
    else:
        print(f"Logged in as {server_info['user']['id']}")

    # Get source records.
    print("Fetch records from source of truth...", end="")
    source_records = get_source_records()
    print("✅")

    # Get current destination records. Abort if one carries a field the job does not model,
    # since updating it would erase that field (the error is reported to Sentry).
    print("Fetch current destination records...", end="")
    dest_records = validate_stored_records(client.get_records())
    print("✅")

    # Create or update the destination records. Records that only exist on the server (e.g. the
    # US "CatchAll Anycast") are maintained by hand, and are never deleted.
    desired_records = assign_ids(source_records, dest_records)
    to_create, to_update, _ = collection_diff(desired_records, dest_records)

    has_pending_changes = to_create or to_update
    if not has_pending_changes:
        print("Records are in sync. Nothing to do ✅.")
        return os.EX_OK

    # Use a batch request for all operations.
    # Note, if you need to add attachments on records, use individual calls.
    # See https://github.com/Kinto/kinto-http.py/?tab=readme-ov-file#attachments
    print("Apply changes...", end="")
    with client.batch() as batch:
        for r in to_create:
            batch.create_record(data=r)
        for old, new in to_update:
            # Let the server assign a new timestamp.
            new.pop("last_modified", None)
            batch.update_record(data=new)
    ops_count = len(batch.results())
    print(f"{ops_count} operations ✅")

    if ENVIRONMENT == "dev":
        # Self approve changes on DEV.
        client.approve_changes()
    else:
        # Request review.
        print("Request review...", end="")
        client.request_review(message="r?")
        print("✅")

    return os.EX_OK


if __name__ == "__main__":
    sys.exit(main())
