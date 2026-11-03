from functools import reduce

from jsonschema import Draft7Validator


def pipe(value, *fns):
    """`pipe(x, f, g)` is `g(f(x))`, like `x | f | g` in a shell."""
    return reduce(lambda acc, fn: fn(acc), fns, value)


def structure_errors(schema, value):
    """
    List how `value` differs from the JSON `schema` (draft-07, like the collection's declared
    schema), one message per mismatch. Only the structure: what the values mean is checked
    separately.
    """
    return [f"{e.json_path}: {e.message}" for e in Draft7Validator(schema).iter_errors(value)]
