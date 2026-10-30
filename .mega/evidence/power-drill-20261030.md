# Supervised 4G hub-port drill, Oct 30 2026 HKT

- Operator onsite and explicitly authorized one supervised USB power-off/on drill. A transient systemd timer was armed to run `/usr/sbin/uhubctl -e -l 1-1 -p 1 -a on` after 30 seconds. The selected hub was the Pi 4B VIA 2109:3431 and the modem was its only attached device.
- At 12:25:33 the off command removed `eth1`; kernel logged `usb 1-1.1: USB disconnect, device number 9` and RNDIS unregister. The remote Tailscale/SSH link dropped.
- At 12:25:44-45, the timer executed successfully. `uhubctl` reported port 1 changing from `0000 off` to `0101 power connect []`. No USB re-enumeration or `eth1` recovery followed in the previous-boot kernel/NetworkManager journal.
- The onsite user reported the card had power but the Pi remained unreachable after a physical card switch off/on. A Pi power cycle restored connectivity. New boot time was 12:33:09; `eth1`, Tailscale, exporter, and SQLite integrity were healthy after boot.
- At 12:30:29-33, the previous boot kernel logged undervoltage detected then voltage normalized. This was during the recovery period, after the remote timer. It does not establish the cause of the earlier 4G wedge or the failed re-enumeration.
- First-drill conclusion: disabling only port 1 broke USB data but did not switch the Pi 4B's ganged VBUS supply. The modem stayed powered. The installed watchdog's USB enumeration reset also had failed to restore the Oct 29-30 outage.

## Correct full-hub drill

- The `uhubctl` Pi 4B documentation specifies switching all ports on hub `1-1`; the installed VL805 firmware `000138c0` exceeds its minimum `00137ad`. The modem was the only external USB device. The onsite user separately approved a second supervised drill.
- Two independent transient `systemd-run` power-on timers were armed for 45 and 110 seconds. The command `uhubctl -l 1-1 -a off` ran at 12:51:29. The user observed the modem's lights go fully off, confirming actual electrical power removal.
- At 12:52:26, the first timer powered on both USB3 hub `2` and USB2 hub `1-1`; all ports had been reported off before this. Kernel logs show a new Marvell 1286:4e3d device at 12:52:32 and `eth1` re-registration at 12:52:37. Tailscale/SSH recovered by 12:52:53 without a Pi reboot; boot time remained 12:34:29. Exporter remained active, collection fresh, and SQLite integrity `ok`.
- Kernel logged undervoltage at 12:52:27 and normalization at 12:52:31 during modem power-up. This suggests a supply or USB inrush margin issue, but no voltage measurement was available. Automatic cycles should be bounded, skipped while current undervoltage is present, and paired with an independent power-on timer. A dedicated modem supply or switched powered hub remains recommended to remove modem surge load from the Pi.
