import os
import subprocess
import tempfile
import unittest

from dtu_watchdog import DtuWatchdog, WatchdogConfig


class DtuWatchdogTests(unittest.TestCase):
    def make_state_path(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return os.path.join(tmpdir.name, "state.json")

    def test_observe_mode_never_runs_reconnect_actions(self):
        calls = []

        def runner(command, timeout):
            calls.append(command)
            return subprocess.CompletedProcess(command, 1)

        watchdog = DtuWatchdog(
            WatchdogConfig(state_path=self.make_state_path(), mode="observe", networkmanager_connection="wwan0"),
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
            WatchdogConfig(state_path=self.make_state_path(), mode="auto", networkmanager_connection="wwan0"),
            runner=runner,
            now=lambda: 1_000,
        )
        result = watchdog.evaluate()

        self.assertEqual(result["general_connectivity"], "healthy")
        self.assertEqual(result["exporter_health"], "degraded")
        self.assertEqual(result["actions"], [])
        self.assertFalse(any(command[0] == "nmcli" for command in calls))

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


if __name__ == "__main__":
    unittest.main()
