import os
import json
import subprocess
import tempfile
import unittest

from dtu_watchdog import DtuWatchdog, WatchdogConfig


class DtuWatchdogTests(unittest.TestCase):
    def make_state_path(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return os.path.join(tmpdir.name, "state.json")

    def make_event_path(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return os.path.join(tmpdir.name, "events.jsonl")

    def test_observe_mode_never_runs_reconnect_actions(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1)

        watchdog = DtuWatchdog(
            WatchdogConfig(
                state_path=self.make_state_path(),
                event_log_path=self.make_event_path(),
                mode="observe",
                networkmanager_connection="wwan0",
            ),
            runner=runner,
            now=lambda: 1_000,
        )

        result = watchdog.evaluate()
        self.assertEqual(result["general_connectivity"], "degraded")
        self.assertEqual(result["actions"], [])
        self.assertFalse(any(command[:3] == ["nmcli", "connection", "down"] for command in calls))

    def test_auto_mode_reconnects_only_after_three_network_failures(self):
        calls = []
        now = 1_000

        def runner(command, timeout):
            calls.append(command)
            if command[0] == "ping":
                return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0, stdout="")

        path = self.make_state_path()
        for _ in range(3):
            watchdog = DtuWatchdog(
                WatchdogConfig(state_path=path, mode="auto", networkmanager_connection="wwan0"),
                runner=runner,
                now=lambda: now,
            )
            result = watchdog.evaluate()

        self.assertEqual(result["actions"], ["reconnected NetworkManager connection wwan0"])
        self.assertIn(["nmcli", "connection", "down", "wwan0"], calls)
        self.assertIn(["nmcli", "connection", "up", "wwan0"], calls)

    def test_exporter_only_failure_does_not_reconnect_modem(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            if command[0] == "curl":
                return subprocess.CompletedProcess(command, 1, stdout="")
            return subprocess.CompletedProcess(command, 0, stdout="")

        watchdog = DtuWatchdog(
            WatchdogConfig(
                state_path=self.make_state_path(),
                event_log_path=self.make_event_path(),
                mode="auto",
                networkmanager_connection="wwan0",
            ),
            runner=runner,
            now=lambda: 1_000,
        )
        result = watchdog.evaluate()

        self.assertEqual(result["general_connectivity"], "healthy")
        self.assertEqual(result["exporter_health"], "degraded")
        self.assertEqual(result["actions"], [])
        self.assertFalse(any(command[:3] == ["nmcli", "connection", "down"] for command in calls))
        self.assertFalse(any(command[:3] == ["nmcli", "connection", "up"] for command in calls))

    def test_tailscale_degraded_with_general_connectivity_restarts_tailscale_first(self):
        calls = []
        now = 1_000

        def runner(command, timeout):
            calls.append(command)
            if command[:2] == ["tailscale", "netcheck"]:
                return subprocess.CompletedProcess(command, 1, stdout="")
            return subprocess.CompletedProcess(command, 0, stdout="throttled=0x0")

        path = self.make_state_path()
        for _ in range(3):
            watchdog = DtuWatchdog(
                WatchdogConfig(state_path=path, mode="auto", networkmanager_connection="Wired connection 2"),
                runner=runner,
                now=lambda: now,
            )
            result = watchdog.evaluate()

        self.assertEqual(result["general_connectivity"], "healthy")
        self.assertEqual(result["tailscale_connectivity"], "degraded")
        self.assertEqual(result["actions"], ["restarted tailscaled"])
        self.assertIn(["systemctl", "restart", "tailscaled"], calls)
        self.assertFalse(any(command[:3] == ["nmcli", "connection", "down"] for command in calls))

    def test_persistent_tailscale_degradation_can_reconnect_network_after_restart_path(self):
        calls = []
        now = 1_000

        def runner(command, timeout):
            calls.append(command)
            if command[:2] == ["tailscale", "netcheck"]:
                return subprocess.CompletedProcess(command, 1, stdout="")
            return subprocess.CompletedProcess(command, 0, stdout="")

        path = self.make_state_path()
        for _ in range(6):
            watchdog = DtuWatchdog(
                WatchdogConfig(
                    state_path=path,
                    mode="auto",
                    networkmanager_connection="Wired connection 2",
                    tailscale_restart_cooldown_seconds=9999,
                ),
                runner=runner,
                now=lambda: now,
            )
            result = watchdog.evaluate()

        self.assertEqual(result["actions"], ["reconnected NetworkManager connection Wired connection 2"])
        self.assertIn(["nmcli", "connection", "down", "Wired connection 2"], calls)
        self.assertIn(["nmcli", "connection", "up", "Wired connection 2"], calls)

    def test_degraded_check_writes_compact_event_log_with_evidence(self):
        event_path = self.make_event_path()

        def runner(command, timeout):
            if command[:2] == ["tailscale", "netcheck"]:
                return subprocess.CompletedProcess(command, 1, stdout='{"UDP":false}')
            if command[:3] == ["ip", "route", "get"]:
                return subprocess.CompletedProcess(command, 0, stdout="1.1.1.1 via 192.168.0.1 dev eth1\n")
            if command[:2] == ["nmcli", "-t"]:
                return subprocess.CompletedProcess(command, 0, stdout="eth1:ethernet:connected:Wired connection 2\n")
            return subprocess.CompletedProcess(command, 0, stdout="")

        watchdog = DtuWatchdog(
            WatchdogConfig(
                state_path=self.make_state_path(),
                event_log_path=event_path,
                mode="observe",
            ),
            runner=runner,
            now=lambda: 1_000,
        )
        result = watchdog.evaluate()

        self.assertEqual(result["tailscale_connectivity"], "degraded")
        with open(event_path) as handle:
            events = [json.loads(line) for line in handle]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tailscale_connectivity"], "degraded")
        self.assertEqual(events[0]["evidence"]["route_to_internet"], "1.1.1.1 via 192.168.0.1 dev eth1")
        self.assertEqual(events[0]["evidence"]["tailscale_netcheck"], {"UDP": False})

    def test_throttled_nonzero_creates_warning_without_recovery_action(self):
        event_path = self.make_event_path()

        def runner(command, timeout):
            if command[0] == "vcgencmd":
                return subprocess.CompletedProcess(command, 0, stdout="throttled=0x50000\n")
            return subprocess.CompletedProcess(command, 0, stdout="")

        watchdog = DtuWatchdog(
            WatchdogConfig(
                state_path=self.make_state_path(),
                event_log_path=event_path,
                mode="auto",
                networkmanager_connection="Wired connection 2",
            ),
            runner=runner,
            now=lambda: 1_000,
        )
        result = watchdog.evaluate()

        self.assertIn("pi_throttled_nonzero", result["warnings"])
        self.assertEqual(result["actions"], [])
        with open(event_path) as handle:
            event = json.loads(handle.readline())
        self.assertEqual(event["throttled"], "throttled=0x50000")
        self.assertIn("pi_throttled_nonzero", event["warnings"])

    def test_last_healthy_timestamps_are_recorded(self):
        path = self.make_state_path()

        def runner(command, timeout):
            return subprocess.CompletedProcess(command, 0, stdout="")

        watchdog = DtuWatchdog(
            WatchdogConfig(state_path=path, event_log_path=self.make_event_path()),
            runner=runner,
            now=lambda: 1_234,
        )
        result = watchdog.evaluate()

        self.assertEqual(result["evidence"]["last_healthy"]["gateway"], 1_234)
        self.assertEqual(result["evidence"]["last_healthy"]["general"], 1_234)
        self.assertEqual(result["evidence"]["last_healthy"]["exporter"], 1_234)
        self.assertEqual(result["evidence"]["last_healthy"]["tailscale"], 1_234)


if __name__ == "__main__":
    unittest.main()
