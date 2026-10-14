"""Testes do to_jsonable — nada do que vem da lib meshtastic pode rebentar o JSON."""

import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.serialize import MAX_DEPTH, to_epoch, to_jsonable


def test_passthrough_scalars():
    for v in (None, True, False, 0, -1, 3.5, "texto"):
        assert to_jsonable(v) == v


def test_datetime_becomes_epoch_seconds():
    dt = datetime(2026, 1, 2, 3, 4, 5)
    assert to_jsonable(dt) == dt.timestamp()


def test_date_becomes_epoch():
    assert to_jsonable(date(2026, 1, 2)) == datetime(2026, 1, 2).timestamp()


def test_bytes_become_base64():
    assert to_jsonable(b"\x01\x02") == "AQI="
    assert to_jsonable(bytearray(b"hi")) == "aGk="
    assert to_jsonable(memoryview(b"hi")) == "aGk="


def test_int_dict_keys_become_strings():
    """nodesByNum usa nodeNum inteiro como chave; JSON só aceita strings."""
    assert to_jsonable({1: "a", "b": 2}) == {"1": "a", "b": 2}


def test_collections_become_lists():
    assert to_jsonable((1, 2)) == [1, 2]
    assert to_jsonable({3}) == [3]
    assert to_jsonable(frozenset([4])) == [4]


def test_nested_structures():
    out = to_jsonable({"a": [{"b": b"\x00", "c": datetime(2026, 1, 1)}]})
    assert out["a"][0]["b"] == "AA=="
    assert out["a"][0]["c"] == datetime(2026, 1, 1).timestamp()


def test_unknown_object_becomes_text_not_an_error():
    class Protobufish:
        def __str__(self):
            return "portnum: TEXT_MESSAGE_APP"

    assert to_jsonable(Protobufish()) == "portnum: TEXT_MESSAGE_APP"


def test_object_whose_str_raises_does_not_propagate():
    class Hostile:
        def __str__(self):
            raise RuntimeError("não")

    assert to_jsonable(Hostile()) is None


def test_cyclic_structure_is_truncated_not_fatal():
    d = {}
    d["self"] = d
    out = to_jsonable(d)
    json.dumps(out)        # o que importa: continua a serializar

    depth = 0
    cur = out
    while isinstance(cur, dict) and "self" in cur:
        cur = cur["self"]
        depth += 1
    assert depth <= MAX_DEPTH


def test_result_is_always_serialisable():
    hostile = {
        "dt": datetime.now(),
        "psk": b"\x00\xff",
        1: "chave inteira",
        "set": {1, 2},
        "obj": object(),
        "nested": [[[{"deep": b"x"}]]],
    }
    json.dumps(to_jsonable(hostile))


def test_to_epoch():
    dt = datetime(2026, 1, 2, 3, 4, 5)
    assert to_epoch(dt) == dt.timestamp()
    assert to_epoch(1700000000) == 1700000000.0
    assert to_epoch(None) is None
    assert to_epoch("ontem") is None


def test_safe_utf8_text_strips_lone_surrogates():
    from core.serialize import safe_utf8_text
    dirty = "Bo\udcc3Nome"
    cleaned = safe_utf8_text(dirty)
    assert cleaned.encode("utf-8")
    assert "\udcc3" not in cleaned


def test_safe_utf8_text_leaves_clean_text_untouched():
    from core.serialize import safe_utf8_text
    assert safe_utf8_text("TKSC ⭐") == "TKSC ⭐"
    assert safe_utf8_text(123) == 123
