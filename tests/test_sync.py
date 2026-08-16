import unittest

from src.mivra.sync import OfflineSyncEngine, Operation, retry_delay_seconds


class OfflineSyncTests(unittest.TestCase):
    def op(self, **kw):
        defaults = dict(
            entity="note",
            entity_id="n1",
            kind="upsert",
            payload={"text": "hello"},
            base_version=0,
            created_at=1,
        )
        defaults.update(kw)
        return Operation.build(**defaults)

    def test_content_derived_ids_are_deterministic(self):
        self.assertEqual(self.op().op_id, self.op().op_id)

    def test_apply_and_duplicate_are_idempotent(self):
        engine = OfflineSyncEngine()
        op = self.op()
        first = engine.apply([op])
        second = engine.apply([op])
        self.assertEqual(first.applied, [op.op_id])
        self.assertEqual(second.duplicate, [op.op_id])

    def test_conflict_is_explicit(self):
        engine = OfflineSyncEngine()
        first = self.op()
        engine.apply([first])
        stale = self.op(payload={"text": "stale"}, base_version=0, created_at=2)
        result = engine.apply([stale])
        self.assertEqual(result.conflicts, [stale.op_id])
        self.assertEqual(result.state["note"]["n1"]["data"]["text"], "hello")

    def test_update_requires_current_version(self):
        engine = OfflineSyncEngine()
        engine.apply([self.op()])
        update = self.op(payload={"done": True}, base_version=1, created_at=2)
        result = engine.apply([update])
        self.assertEqual(result.state["note"]["n1"]["version"], 2)
        self.assertTrue(result.state["note"]["n1"]["data"]["done"])

    def test_delete_creates_tombstone(self):
        engine = OfflineSyncEngine()
        engine.apply([self.op()])
        delete = self.op(kind="delete", payload={}, base_version=1, created_at=2)
        result = engine.apply([delete])
        item = result.state["note"]["n1"]
        self.assertTrue(item["deleted"])
        self.assertIsNone(item["data"])

    def test_input_order_does_not_change_result(self):
        a = self.op(entity_id="a", created_at=2, payload={"v": 2})
        b = self.op(entity_id="b", created_at=1, payload={"v": 1})
        x = OfflineSyncEngine().apply([a, b]).state
        y = OfflineSyncEngine().apply([b, a]).state
        self.assertEqual(x, y)

    def test_retry_backoff_is_bounded(self):
        self.assertEqual(retry_delay_seconds(0), 1)
        self.assertEqual(retry_delay_seconds(3), 8)
        self.assertEqual(retry_delay_seconds(100, cap=30), 30)
        with self.assertRaises(ValueError):
            retry_delay_seconds(-1)


if __name__ == "__main__":
    unittest.main()
