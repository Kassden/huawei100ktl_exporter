import unittest
from datetime import datetime, timezone

from iot_driver_copilot.huawei_sun_2000_solar_inverter.driver import cloud_delivery_status


class CloudDeliveryHealthTests(unittest.TestCase):
    def test_upload_failure_with_pending_spool_is_degraded(self):
        status = cloud_delivery_status({"spool_pending": 10, "consecutive_upload_failures": 3,
                                        "spool_oldest_at": "2026-10-30T00:00:00Z",
                                        "spool_last_drain_error": "HTTP 400"},
                                       datetime(2026, 10, 30, 1, tzinfo=timezone.utc))
        self.assertEqual(status["status"], "degraded")
        self.assertEqual(status["pending"], 10)
        self.assertEqual(status["oldest_age_seconds"], 3600)

    def test_recent_pending_batch_is_healthy(self):
        status = cloud_delivery_status({"spool_pending": 2, "consecutive_upload_failures": 0,
                                        "spool_oldest_at": "2026-10-30T00:00:00Z"},
                                       datetime(2026, 10, 30, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(status["status"], "healthy")

    def test_empty_spool_after_recovery_is_healthy(self):
        status = cloud_delivery_status({"spool_pending": 0, "consecutive_upload_failures": 0,
                                        "spool_oldest_at": None},
                                       datetime(2026, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(status["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
