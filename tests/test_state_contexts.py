import unittest
from pathlib import Path

from app.state import AppState, REQUIRED_STATE_KEYS


class AppStateContextTests(unittest.TestCase):
    def _build_state(self):
        data = {key: None for key in REQUIRED_STATE_KEYS}
        data["SERVICE"] = "minecraft"
        data["WORLD_DIR"] = Path(".")
        data["get_status"] = lambda: "inactive"
        data["set_service_status_intent"] = lambda _intent: None
        return AppState(data)

    def test_exposes_typed_contexts(self):
        state = self._build_state()
        self.assertEqual("minecraft", state.config.SERVICE)
        self.assertTrue(callable(state.ports.get_status))
        self.assertEqual(Path("."), state.runtime.WORLD_DIR)

    def test_config_context_is_immutable(self):
        state = self._build_state()
        with self.assertRaises(TypeError):
            state["SERVICE"] = "other"
        with self.assertRaises(TypeError):
            state.SERVICE = "other"

    def test_runtime_context_is_mutable(self):
        state = self._build_state()
        next_world = Path("new_world")
        state.WORLD_DIR = next_world
        self.assertEqual(next_world, state.runtime.WORLD_DIR)
        self.assertEqual(next_world, state["WORLD_DIR"])

    def test_unified_ctx_reads_ports_runtime_and_config(self):
        state = self._build_state()
        self.assertEqual("inactive", state.ctx.get_status())
        self.assertEqual("minecraft", state.ctx.SERVICE)
        state.ctx.WORLD_DIR = Path("another_world")
        self.assertEqual(Path("another_world"), state.runtime.WORLD_DIR)


if __name__ == "__main__":
    unittest.main()
