from src.util import pipe, structure_errors


def test_pipe_applies_functions_left_to_right():
    assert pipe(2, lambda x: x + 1, lambda x: x * 10) == 30


def test_pipe_without_functions_returns_the_value():
    assert pipe(2) == 2


def test_structure_errors_lists_every_mismatch_with_its_path():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}}

    assert structure_errors(schema, {"a": 1, "b": "x"}) == []
    assert structure_errors(schema, {"a": "1", "b": 2}) == [
        "$.a: '1' is not of type 'integer'",
        "$.b: 2 is not of type 'string'",
    ]
