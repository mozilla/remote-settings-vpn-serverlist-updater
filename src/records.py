"""
The `vpn-serverlist` record, as modelled by this job.

The structure is a JSON Schema, like the collection's declared one but stricter: no unknown
fields, no empty strings or lists. The job checks the live records against it before writing
anything, since its full-record updates would erase whatever it does not model.
"""

from .util import structure_errors


TEXT = {"type": "string", "minLength": 1}
FLAG = {"type": "boolean"}


def object_of(properties, required):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required),
        "properties": properties,
    }


def list_of(items):
    return {"type": "array", "minItems": 1, "items": items}


SERVER_SCHEMA = object_of(
    {"port": {"type": "integer"}, "hostname": TEXT, "quarantined": FLAG},
    required={"port", "hostname"},
)

CITY_SCHEMA = object_of(
    {"code": TEXT, "name": TEXT, "servers": list_of(SERVER_SCHEMA)},
    required={"code", "name", "servers"},
)

COUNTRY_SCHEMA = object_of(
    {
        "code": TEXT,
        "name": TEXT,
        "cities": list_of(CITY_SCHEMA),
        "filter_expression": TEXT,
        "locked": FLAG,
    },
    required={"code", "name", "cities"},
)

# A record as stored on Remote Settings, with the fields it assigns rather than the job.
STORED_RECORD_SCHEMA = object_of(
    {**COUNTRY_SCHEMA["properties"], "id": TEXT, "last_modified": {}, "schema": {}},
    required=COUNTRY_SCHEMA["required"],
)


def stored_record_errors(record):
    return structure_errors(STORED_RECORD_SCHEMA, record)


class RecordError(ValueError):
    pass


def validate_stored_records(records):
    """
    Raise if a live record is not shaped as modelled, listing every problem. An update replaces
    the whole record, so a field the job does not model would otherwise be silently erased.
    """
    errors = [
        f"Record {r.get('id')} ({r.get('code')}): {e}"
        for r in records
        for e in stored_record_errors(r)
    ]
    if errors:
        raise RecordError("Records do not match the model:\n" + "\n".join(errors))
    return records


def record_key(record):
    """
    A country can have several records, distinguished by their filter expression.
    """
    return (record["code"], record.get("filter_expression", ""))
