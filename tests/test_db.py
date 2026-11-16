import datetime as dt
import uuid
from decimal import Decimal

import pytest

from sprocguard.db import normalize_value


class _Custom:
    def __str__(self) -> str:
        return "custom-value"


@pytest.mark.parametrize("value", [None, "text", 42, 1.5])
def test_plain_values_are_unchanged(value):
    assert normalize_value(value) == value
    assert type(normalize_value(value)) is type(value)


@pytest.mark.parametrize(("value", "expected"), [(True, 1), (False, 0)])
def test_bool_becomes_int(value, expected):
    result = normalize_value(value)
    assert result == expected
    assert type(result) is int


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("33.750"), "33.750"),
        (Decimal("1E+2"), "100"),
        (Decimal("-0.0001"), "-0.0001"),
    ],
)
def test_decimal_keeps_precision_without_scientific_notation(value, expected):
    assert normalize_value(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (dt.datetime(2026, 11, 16, 9, 30, 15, 123000), "2026-11-16T09:30:15.123000"),
        (dt.date(2026, 11, 16), "2026-11-16"),
        (dt.time(9, 30, 15), "09:30:15"),
    ],
)
def test_dates_and_times_use_iso_format(value, expected):
    assert normalize_value(value) == expected


@pytest.mark.parametrize("value", [b"\x01\xab", bytearray(b"\x01\xab"), memoryview(b"\x01\xab")])
def test_binary_becomes_upper_case_hex(value):
    assert normalize_value(value) == "0x01AB"


def test_uuid_becomes_upper_case_string():
    value = uuid.UUID("6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b")
    assert normalize_value(value) == "6F1C2A3B-4D5E-4F60-8A7B-9C0D1E2F3A4B"


def test_other_objects_use_str():
    assert normalize_value(_Custom()) == "custom-value"
