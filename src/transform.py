import uuid
from collections import Counter

from .fastly import UNSYNCED_LOCATIONS
from .records import record_key


# Every server entry uses this port; Fastly does not provide one.
KINTO_SERVER_PORT = 2499

# Filter expressions that must be carried on a country's record for historical reasons:
# the live US record is gated to recent app versions.
LEGACY_FILTER_EXPRESSIONS = {
    "US": 'env.version|versionCompare("151.0a1") >= 0',
}


def pop_prefix(fastly_id):
    """
    Leading run of letters of a Fastly id, uppercased ("eglc860" -> "EGLC").
    """
    prefix = ""
    for char in fastly_id:
        if not char.isalpha():
            break
        prefix += char
    return prefix.upper()


def is_aggregate(server):
    return server["id"].lower() == server["country"].lower()


def drop_unsynced_locations(servers):
    """
    Drop the locations the job validates but does not sync (see `UNSYNCED_LOCATIONS`), like
    "closest", whose `REC` record is maintained in Remote Settings.
    """
    return [s for s in servers if s["id"] not in UNSYNCED_LOCATIONS]


def one_location_per_country(servers):
    """
    Collapse countries that appear several times down to their aggregate entry, the one
    whose id is the country code (e.g. "us" -> us.m1.fastly-masque.net), dropping per-POP
    entries like "kewr174". A country with several entries but not exactly one aggregate is
    ambiguous and raises.
    """
    counts = Counter(s["country"] for s in servers)
    aggregates = Counter(s["country"] for s in servers if is_aggregate(s))

    ambiguous = sorted(c for c, n in counts.items() if n > 1 and aggregates[c] != 1)
    if ambiguous:
        raise ValueError(
            f"Countries with several entries but not exactly one aggregate: {ambiguous}"
        )

    return [s for s in servers if counts[s["country"]] == 1 or is_aggregate(s)]


def fastly_to_kinto(servers):
    """
    Turn (cleaned) Fastly entries into records: one country holding a single city with a
    single server.
    """
    return [
        {
            "code": s["country"],
            "name": s["human_readable"],
            "cities": [
                {
                    "code": pop_prefix(s["id"]),
                    "name": s["human_readable"],
                    "servers": [
                        {
                            "port": KINTO_SERVER_PORT,
                            "hostname": s["fqdn"],
                            **({} if s["available"] else {"quarantined": True}),
                        }
                    ],
                }
            ],
        }
        for s in servers
    ]


def add_legacy_filter_expressions(records):
    """
    Put back the `filter_expression` that live records carry, which Fastly knows nothing about.

    Records are matched on code + filter expression (see `assign_ids`). The live US record
    synced from Fastly is gated to Firefox >= 151, while older versions get the hand-maintained
    US "CatchAll Anycast" record (`< 151.0a1`). Without the filter, Fastly's US record would
    match neither, and the job would create a second, ungated US record next to them.
    """
    return [
        {**r, "filter_expression": LEGACY_FILTER_EXPRESSIONS[r["code"]]}
        if r["code"] in LEGACY_FILTER_EXPRESSIONS
        else r
        for r in records
    ]


def assign_ids(desired, existing):
    """
    Give each desired record the id of the existing record it replaces, matched on code and
    filter expression, so that they can be diffed by id.

    The `locked` flag is owned by Remote Settings: it is carried over from the existing record
    as-is (absent stays absent). Records for new countries get a fresh id and are locked.
    """
    existing_by_key = {record_key(r): r for r in existing}

    def with_id(record):
        match = existing_by_key.get(record_key(record))
        if match is None:
            return {**record, "id": str(uuid.uuid4()), "locked": True}
        locked = {"locked": match["locked"]} if "locked" in match else {}
        return {**record, "id": match["id"], **locked}

    return [with_id(r) for r in desired]
