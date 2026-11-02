import re

import pycountry
import requests

from .util import structure_errors


FASTLY_LOCATIONS_URL = "https://locations.m1.fastly-masque.net/v1/locations"

# Fastly locations that have no country, and that the job validates but does not sync. The
# `REC` (Recommended) record that stands for "closest" is maintained in Remote Settings.
UNSYNCED_LOCATIONS = frozenset({"closest"})

# Records are matched on the country code, so a non-ISO one (Fastly used `UK` for `GB` in the
# past) must not get through.
ISO_COUNTRY_CODES = frozenset(country.alpha_2 for country in pycountry.countries)


def matches(pattern):
    return lambda value: re.fullmatch(pattern, value) is not None


def is_country_code(value):
    return value in ISO_COUNTRY_CODES


# The structure of the Fastly response this job expects. Every field is required (except the
# country of an unsynced location) and no other field is allowed. Anything else aborts the sync.
TEXT = {"type": "string", "minLength": 1}
FASTLY_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["id", "fqdn", "human_readable", "available"],
        "properties": {
            "id": TEXT,
            "fqdn": TEXT,
            "country": TEXT,
            "human_readable": TEXT,
            "available": {"type": "boolean"},
        },
        "if": {"required": ["id"], "properties": {"id": {"enum": sorted(UNSYNCED_LOCATIONS)}}},
        "else": {"required": ["country"]},
    },
}

# What the values of a structurally valid entry must mean: field -> check of its value.
FASTLY_SEMANTICS = {
    # The city code is derived from the leading letters (see `transform.pop_prefix`).
    "id": matches(r"[a-z][a-z0-9]*"),
    "fqdn": matches(r"[a-z0-9.-]+"),
    # Records are matched on the country code.
    "country": is_country_code,
}


class FastlySchemaError(ValueError):
    pass


def fetch_fastly():
    resp = requests.get(FASTLY_LOCATIONS_URL, timeout=30)
    resp.raise_for_status()
    return resp.json()


def semantic_errors(entry):
    """
    List the values of a single, structurally valid location entry that `FASTLY_SEMANTICS`
    rejects. A field the schema lets be absent (the country of an unsynced location) is only
    checked when present.
    """
    return [
        f"invalid {k!r}: {entry[k]!r}"
        for k, is_valid in FASTLY_SEMANTICS.items()
        if k in entry and not is_valid(entry[k])
    ]


def validate_fastly_data(servers):
    """
    Raise if the Fastly response does not match `FASTLY_SCHEMA`, or if its values do not mean
    what `FASTLY_SEMANTICS` expects, listing every mismatch.
    """
    errors = structure_errors(FASTLY_SCHEMA, servers) or [
        f"$[{i}] {entry['id']}: {e}"
        for i, entry in enumerate(servers)
        for e in semantic_errors(entry)
    ]
    if errors:
        raise FastlySchemaError(
            "Fastly locations do not match the expected schema:\n" + "\n".join(errors)
        )
    return servers
