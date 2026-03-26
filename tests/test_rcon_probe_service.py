import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services import rcon_probe_service


def _build_ctx(intent="starting", status="active"):
    return SimpleNamespace(
        OFF_STATES={"inactive", "failed"},
        MC_QUERY_INTERVAL_SECONDS=30,
        RCON_HOST="127.0.0.1",
        RCON_PORT=25575,
        SERVER_PROPERTIES_CANDIDATES=[],
        rcon_config_lock=threading.Lock(),
        rcon_last_config_read_at=0.0,
        rcon_cached_password="secret",
        rcon_cached_port=25575,
        rcon_cached_enabled=True,
        rcon_startup_lock=threading.Lock(),
        rcon_startup_ready=False,
        mc_query_lock=threading.Lock(),
        mc_cached_players_online="unknown",
        mc_cached_tick_rate="--",
        mc_last_query_at=0.0,
        get_status=lambda: status,
        get_service_status_intent=lambda: intent,
        log_mcweb_exception=lambda *_args, **_kwargs: None,
    )


class RconProbeServiceTests(unittest.TestCase):
    def test_probe_runtime_metrics_marks_startup_ready_when_rcon_list_succeeds(self):
        ctx = _build_ctx()

        def _run(_ctx, command, timeout=4):
            if command == "list":
                return SimpleNamespace(returncode=0, stdout="There are 2 of a max of 20 players online", stderr="")
            if command == "forge tps":
                return SimpleNamespace(returncode=0, stdout="Mean tick time: 50 ms", stderr="")
            raise AssertionError(command)

        with patch.object(rcon_probe_service, "run_mcrcon", side_effect=_run):
            players_online, tick_rate = rcon_probe_service.probe_minecraft_runtime_metrics(ctx, force=True)

        self.assertEqual(players_online, "2")
        self.assertEqual(tick_rate, "50.0 ms")
        self.assertTrue(ctx.rcon_startup_ready)

    def test_starting_state_stays_unknown_when_rcon_is_not_ready(self):
        ctx = _build_ctx()
        with patch.object(rcon_probe_service, "run_mcrcon", side_effect=RuntimeError("offline")):
            players_online, tick_rate = rcon_probe_service.probe_minecraft_runtime_metrics(ctx, force=True)

        self.assertEqual(players_online, "unknown")
        self.assertEqual(tick_rate, "--")
        self.assertFalse(ctx.rcon_startup_ready)


if __name__ == "__main__":
    unittest.main()
