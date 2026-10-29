"""JSON-serialization safety at the watcher's result boundary.

The watcher runs on IronPython 2.7, where a CLR string arrives as BYTES in the
process ANSI code page (cp1252 on a Spanish Windows). `json.dumps` decodes
bytes as UTF-8, so the first non-ASCII byte makes the encoder raise
UnicodeDecodeError — and because the error message embeds those same bytes,
the "last-ditch" error envelope fails too. The caller then waits forever for a
result file that never appears.

These tests pin the byte -> text coercion and the UTF-8 serialization at that
boundary. They run on CPython 3 (where `bytes` is a distinct type), but they
exercise the same coercion code the IronPython side uses.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_helpers():
    spec = importlib.util.spec_from_file_location(
        "_codesys_helpers_under_test", SCRIPTS / "_codesys_helpers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


h = _load_helpers()


class TestSafeText:
    def test_ascii_bytes_pass_through(self):
        assert h.safe_text(b"plain") == "plain"

    def test_utf8_bytes_are_decoded(self):
        assert h.safe_text("Caf\u00e9".encode("utf-8")) == "Caf\u00e9"

    def test_ansi_bytes_become_encodable_text(self):
        out = h.safe_text(b"Caf\xe9")
        assert isinstance(out, type(u""))
        # The whole point: json.dumps must not raise on it.
        assert json.dumps(out)

    def test_undecodable_bytes_do_not_raise(self):
        out = h.safe_text(b"\xff\xfe\x00bad")
        assert json.dumps(out)

    def test_non_text_is_stringified(self):
        assert h.safe_text(42) == "42"

    def test_text_stays_text(self):
        assert h.safe_text(u"caf\u00e9") == u"caf\u00e9"

    def test_raising_str_does_not_escape(self):
        class Hostile(object):
            def __str__(self):
                raise RuntimeError("no string for you")

        # The log path must never raise: a raising __str__ here would abort
        # the error handling that writes the fallback envelope.
        assert h.safe_text(Hostile())


class TestCoerceForJson:
    def test_bytes_become_json_encodable_text(self):
        out = h.coerce_for_json({"name": b"Caf\xe9"})
        assert json.dumps(out)

    def test_nested_bytes_are_coerced(self):
        payload = {"devices": [{"name": b"m\xf3dulo"}]}
        assert json.dumps(h.coerce_for_json(payload))

    def test_numbers_and_bools_pass_through(self):
        assert h.coerce_for_json({"a": 1, "b": 2.5, "c": True}) == {
            "a": 1,
            "b": 2.5,
            "c": True,
        }

    def test_unknown_objects_fall_back_to_str(self):
        class Weird(object):
            def __str__(self):
                return "weird"

        assert h.coerce_for_json({"x": Weird()}) == {"x": "weird"}

    def test_lists_are_coerced_recursively(self):
        assert h.coerce_for_json([1, b"a", {"k": b"b"}]) == [1, "a", {"k": "b"}]


class TestJsonPayload:
    """The serializer that actually caused the outage.

    Before the fix the payload was built with the default ensure_ascii=True,
    which in IronPython raises UnicodeDecodeError on the first non-ASCII
    character. The host writes and reads these files as UTF-8, so the payload
    must be UTF-8 bytes with the characters intact.
    """

    def test_payload_is_bytes(self):
        payload = h.json_payload({"a": 1})
        assert isinstance(payload, bytes)

    def test_escaped_before_but_raw_now(self):
        # The old behaviour produced b'{"a": "\\u00e9"}'.
        payload = h.json_payload({"a": "caf\u00e9"})
        assert payload == '{"a": "caf\u00e9"}'.encode("utf-8")

    def test_payload_round_trips_as_utf8(self):
        payload = h.json_payload({"a": "caf\u00e9", "b": "ni\u00f1o"})
        assert json.loads(payload.decode("utf-8")) == {"a": "caf\u00e9", "b": "ni\u00f1o"}

    def test_payload_is_pure_ascii_when_it_can_be(self):
        payload = h.json_payload({"a": "plain"})
        assert payload == b'{"a": "plain"}'


class TestAtomicWrite:
    def test_writes_utf8_bytes_verbatim(self, tmp_path: Path):
        target = tmp_path / "out.json"
        payload = h.json_payload({"a": "caf\u00e9"})
        h.atomic_write(str(target), payload)
        assert target.read_bytes() == payload
        assert target.read_text(encoding="utf-8") == '{"a": "caf\u00e9"}'

    def test_overwrites_an_existing_file(self, tmp_path: Path):
        target = tmp_path / "out.json"
        target.write_text("old", encoding="utf-8")
        h.atomic_write(str(target), b"new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_leaves_no_temp_file_behind(self, tmp_path: Path):
        target = tmp_path / "out.json"
        h.atomic_write(str(target), b"x")
        assert not (tmp_path / "out.json.tmp").exists()


class TestReadJson:
    """The host -> watcher direction.

    The host writes command files as UTF-8. Reading them with `open(path, "r")`
    on IronPython decodes with the ANSI code page, which silently corrupts
    every accented character in the arguments.
    """

    def test_reads_utf8_without_mojibake(self, tmp_path: Path):
        target = tmp_path / "cmd.json"
        target.write_text('{"a": "caf\u00e9 ni\u00f1o"}', encoding="utf-8")
        assert h.read_json(str(target)) == {"a": "caf\u00e9 ni\u00f1o"}

    def test_missing_file_returns_none(self, tmp_path: Path):
        assert h.read_json(str(tmp_path / "nope.json")) is None

    def test_invalid_json_returns_none(self, tmp_path: Path):
        target = tmp_path / "cmd.json"
        target.write_text("{not json", encoding="utf-8")
        assert h.read_json(str(target)) is None

    def test_empty_file_returns_none(self, tmp_path: Path):
        target = tmp_path / "cmd.json"
        target.write_text("", encoding="utf-8")
        assert h.read_json(str(target)) is None


class TestNoBareJsonDumps:
    """Guard: no writer path may reintroduce the ensure_ascii=True encoder.

    `json.dumps` outside `_json_payload` is fine on CPython, but on IronPython
    it is exactly the call that raises UnicodeDecodeError.
    """

    def test_scripts_only_dump_through_the_helper(self):
        offenders = []
        for path in SCRIPTS.rglob("*.py"):
            in_helper = False
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("def "):
                    in_helper = stripped.startswith("def json_payload(")
                if "json.dumps(" not in line or in_helper:
                    continue
                if stripped.startswith("#"):
                    continue
                offenders.append("%s:%d: %s" % (path.name, number, stripped))
        assert not offenders, "bare json.dumps outside the helper: " + "; ".join(offenders)
