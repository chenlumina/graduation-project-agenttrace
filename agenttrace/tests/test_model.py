import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.model import TraceEvent, TraceValidationError


def make_event(**overrides):
    base = dict(
        id="e1",
        attempt_id="a1",
        seq=1,
        ts="2026-11-07T00:00:00.000+00:00",
        type="tool_call",
        parent_id=None,
        data={"tool": "read_file", "args": {}, "result": "x", "status": "ok"},
    )
    base.update(overrides)
    return TraceEvent(**base)


class TestTraceEvent(unittest.TestCase):
    def test_roundtrip(self):
        ev = make_event()
        ev2 = TraceEvent.from_dict(ev.to_dict())
        self.assertEqual(ev.to_dict(), ev2.to_dict())

    def test_unknown_type_rejected(self):
        with self.assertRaises(TraceValidationError):
            make_event(type="nope").validate()

    def test_missing_required_field_rejected(self):
        bad = make_event()
        del bad.data["result"]
        with self.assertRaises(TraceValidationError):
            bad.validate()

    def test_bad_seq_rejected(self):
        with self.assertRaises(TraceValidationError):
            make_event(seq=0).validate()

    def test_from_dict_validates(self):
        d = make_event(type="error", data={"message": "x"}).to_dict()
        self.assertEqual(TraceEvent.from_dict(d).type, "error")


if __name__ == "__main__":
    unittest.main()
