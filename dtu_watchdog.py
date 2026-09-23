#!/usr/bin/env python3
import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional


CommandRunner = Callable[[List[str], int], subprocess.CompletedProcess]


@dataclass
class WatchdogConfig:
    state_path: str = "/var/lib/huawei-exporter/dtu-watchdog-state.json"
    mode: str = "observe"
    networkmanager_connection: Optional[str] = None
    gateway_ip: str = "192.168.0.1"
    exporter_health_url: str = "http://127.0.0.1:8080/health"
    tailscale_control_host: str = "controlplane.tailscale.com"
    reconnect_cooldown_seconds: int = 900
    max_reconnects_per_hour: int = 3
    tailscale_restart_cooldown_seconds: int = 900
    tailscale_failure_threshold: int = 3
    network_failure_threshold: int = 3


class DtuWatchdog:
    def __init__(self, config: WatchdogConfig, runner: CommandRunner = None, now: Callable[[], float] = time.time):
        self.config = config
        self.runner = runner or self.default_runner
        self.now = now
        self.state = self.load_state()

    def evaluate(self) -> Dict[str, object]:
        gateway = self.check_command(["ping", "-c", "1", "-W", "5", self.config.gateway_ip])
        general = self.check_command(["ping", "-c", "1", "-W", "5", "1.1.1.1"])
        exporter = self.check_command(["curl", "-fsS", "--max-time", "5", self.config.exporter_health_url])
        tailscaled = self.check_command(["systemctl", "is-active", "--quiet", "tailscaled"])
        tailscale_status = self.check_command(["tailscale", "status", "--json"])
        tailscale_netcheck = self.check_command(["tailscale", "netcheck", "--format=json"])
        tailscale_control_dns = self.check_command(["getent", "hosts", self.config.tailscale_control_host])
        throttled = self.run_text(["vcgencmd", "get_throttled"], 5)

        tailscale_degraded = not (tailscaled and tailscale_status and tailscale_netcheck and tailscale_control_dns)
        network_degraded = not (gateway and general)

        actions: List[str] = []
        self.update_counter("network_failures", network_degraded)
        self.update_counter("tailscale_failures", tailscale_degraded)

        if (
            self.config.mode == "auto"
            and tailscale_degraded
            and general
            and self.state["tailscale_failures"] >= self.config.tailscale_failure_threshold
        ):
            if self.cooldown_elapsed("last_tailscale_restart", self.config.tailscale_restart_cooldown_seconds):
                actions.append(self.restart_tailscale())

        if self.config.mode == "auto" and self.state["network_failures"] >= self.config.network_failure_threshold:
            if self.can_reconnect_modem():
                actions.append(self.reconnect_modem())

        if (
            self.config.mode == "auto"
            and tailscale_degraded
            and general
            and self.state["tailscale_failures"] >= self.config.tailscale_failure_threshold * 2
        ):
            if self.can_reconnect_modem():
                actions.append(self.reconnect_modem())

        result = {
            "mode": self.config.mode,
            "gateway": "healthy" if gateway else "degraded",
            "general_connectivity": "healthy" if general else "degraded",
            "exporter_health": "healthy" if exporter else "degraded",
            "tailscaled": "healthy" if tailscaled else "degraded",
            "tailscale_status": "healthy" if tailscale_status else "degraded",
            "tailscale_netcheck": "healthy" if tailscale_netcheck else "degraded",
            "tailscale_control_dns": "healthy" if tailscale_control_dns else "degraded",
            "tailscale_connectivity": "healthy" if not tailscale_degraded else "degraded",
            "network_failures": self.state["network_failures"],
            "tailscale_failures": self.state["tailscale_failures"],
            "throttled": throttled,
            "actions": actions,
        }
        self.save_state()
        return result

    def update_counter(self, key: str, degraded: bool) -> None:
        if degraded:
            self.state[key] = self.state.get(key, 0) + 1
        else:
            self.state[key] = 0

    def can_reconnect_modem(self) -> bool:
        if not self.config.networkmanager_connection:
            return False
        if not self.cooldown_elapsed("last_modem_reconnect", self.config.reconnect_cooldown_seconds):
            return False
        cutoff = self.now() - 3600
        recent = [ts for ts in self.state.get("modem_reconnects", []) if ts >= cutoff]
        self.state["modem_reconnects"] = recent
        return len(recent) < self.config.max_reconnects_per_hour

    def reconnect_modem(self) -> str:
        connection = self.config.networkmanager_connection
        assert connection is not None
        self.runner(["nmcli", "connection", "down", connection], 20)
        self.runner(["nmcli", "connection", "up", connection], 30)
        now = self.now()
        self.state["last_modem_reconnect"] = now
        self.state.setdefault("modem_reconnects", []).append(now)
        return f"reconnected NetworkManager connection {connection}"

    def restart_tailscale(self) -> str:
        self.runner(["systemctl", "restart", "tailscaled"], 20)
        self.state["last_tailscale_restart"] = self.now()
        return "restarted tailscaled"

    def cooldown_elapsed(self, key: str, seconds: int) -> bool:
        last = self.state.get(key)
        return not last or self.now() - float(last) >= seconds

    def check_command(self, command: List[str]) -> bool:
        try:
            return self.runner(command, 10).returncode == 0
        except Exception:
            return False

    def run_text(self, command: List[str], timeout: int) -> Optional[str]:
        try:
            result = self.runner(command, timeout)
        except Exception:
            return None
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None

    def load_state(self) -> Dict[str, object]:
        path = Path(self.config.state_path)
        if not path.exists():
            return {"network_failures": 0, "tailscale_failures": 0, "modem_reconnects": []}
        try:
            state = json.loads(path.read_text())
        except Exception:
            return {"network_failures": 0, "tailscale_failures": 0, "modem_reconnects": []}
        state.setdefault("network_failures", state.pop("general_failures", 0))
        state.setdefault("tailscale_failures", 0)
        state.setdefault("modem_reconnects", [])
        return state

    def save_state(self):
        path = Path(self.config.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.state, sort_keys=True))

    @staticmethod
    def default_runner(command: List[str], timeout: int):
        return subprocess.run(command, timeout=timeout, capture_output=True, text=True)


def parse_args():
    parser = argparse.ArgumentParser(description="Observe or cautiously recover DTU connectivity")
    parser.add_argument("--state-path", default="/var/lib/huawei-exporter/dtu-watchdog-state.json")
    parser.add_argument("--mode", choices=["observe", "auto"], default="observe")
    parser.add_argument("--networkmanager-connection")
    parser.add_argument("--gateway-ip", default="192.168.0.1")
    parser.add_argument("--exporter-health-url", default="http://127.0.0.1:8080/health")
    parser.add_argument("--tailscale-control-host", default="controlplane.tailscale.com")
    return parser.parse_args()


def main():
    args = parse_args()
    result = DtuWatchdog(
        WatchdogConfig(
            state_path=args.state_path,
            mode=args.mode,
            networkmanager_connection=args.networkmanager_connection,
            gateway_ip=args.gateway_ip,
            exporter_health_url=args.exporter_health_url,
            tailscale_control_host=args.tailscale_control_host,
        )
    ).evaluate()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
