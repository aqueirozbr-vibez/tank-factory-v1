"""Unit 03: RFC 8785 canonical JSON."""

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from orchestrator.errors import TraceError
from orchestrator.trace.canonical import (
    canonical_bytes,
    canonical_json,
    es6_number,
    parse_canonical,
)

RFC_INPUT = (
    "{\n"
    '  "numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],\n'
    '  "string": "\\u20ac$\\u000F\\u000aA\'\\u0042\\u0022\\u005c\\\\\\"\\/",\n'
    '  "literals": [null, true, false]\n'
    "}"
)
RFC_OUTPUT = (
    '{"literals":[null,true,false],'
    '"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
    '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
)


def test_rfc_8785_example_vector() -> None:
    assert canonical_json(json.loads(RFC_INPUT)) == RFC_OUTPUT
    assert canonical_bytes(json.loads(RFC_INPUT)) == RFC_OUTPUT.encode("utf-8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (0.1, "0.1"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (1e-7, "1e-7"),
        (0.000001, "0.000001"),
        (123456789012345680000.0, "123456789012345680000"),
        (5e-324, "5e-324"),
        (-0.0, "0"),
        (-1.5, "-1.5"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (0.002, "0.002"),
        (333333333.33333329, "333333333.3333333"),
    ],
)
def test_es6_numbers(value: float, expected: str) -> None:
    assert es6_number(value) == expected


def test_rejects_non_finite_and_unsafe_numbers() -> None:
    with pytest.raises(TraceError):
        canonical_json(math.nan)
    with pytest.raises(TraceError):
        canonical_json(math.inf)
    with pytest.raises(TraceError):
        canonical_json(2**53 + 1)
    assert canonical_json(2**53) == "9007199254740992"
    assert canonical_json(10**17) == "100000000000000000"  # exactly representable
    assert canonical_json(10**21) == "1e+21"


def test_rejects_non_json_types_and_keys() -> None:
    with pytest.raises(TraceError, match="not JSON"):
        canonical_json({"a": {1, 2}})
    with pytest.raises(TraceError, match="not a string"):
        canonical_json({1: "a"})


def test_keys_sort_by_utf16_code_units() -> None:
    astral = "\U0001f600"  # surrogate pair D83D DE00
    bmp_high = ""  # above the surrogate range in code units
    assert canonical_json({bmp_high: 1, astral: 2}) == f'{{"{astral}":2,"{bmp_high}":1}}'
    assert canonical_json({"b": 1, "a": 2, "aa": 3}) == '{"a":2,"aa":3,"b":1}'


def test_string_escapes() -> None:
    assert canonical_json("\x1f/é\x7f") == '"\\u001f/é\x7f"'
    assert canonical_json('"\\\b\f\n\r\t') == '"\\"\\\\\\b\\f\\n\\r\\t"'


def test_no_whitespace_and_nesting() -> None:
    assert canonical_json({"z": [1, {"y": None}], "a": True}) == '{"a":true,"z":[1,{"y":null}]}'


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**53), max_value=2**53)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)


@given(json_values)
def test_canonical_round_trips_and_is_idempotent(value: object) -> None:
    text = canonical_json(value)
    parsed = parse_canonical(text)
    assert parsed == value
    assert canonical_json(parsed) == text


def test_parse_canonical_reads_big_integer_literals_as_doubles() -> None:
    big = 1.2345678901234568e20
    text = canonical_json(big)
    assert text == "123456789012345680000"
    assert json.loads(text) != big  # an exact int no longer equals the double
    assert parse_canonical(text) == big
    assert canonical_json(parse_canonical(text)) == text
    with pytest.raises(TraceError):
        parse_canonical("NaN")
    with pytest.raises(TraceError):
        parse_canonical("{not json")
