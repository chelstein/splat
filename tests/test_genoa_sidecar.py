"""Tests for genoa_sidecar.

Run:    python -m unittest discover -s tests -v
or:     python -m unittest tests.test_genoa_sidecar -v

No extra deps — stdlib unittest + Flask test client (Flask is already a
production dependency).
"""
from __future__ import annotations

import unittest
from unittest import mock

from genoa_sidecar import _build_command, app, RunStats


class BuildCommandTests(unittest.TestCase):
    def test_requires_tx_qth(self):
        self.assertEqual(_build_command({}), "tx_qth is required")

    def test_minimal_payload_produces_t_flag(self):
        cmd = _build_command({"tx_qth": "site.qth"})
        # cmd[0] is the SPLAT binary path; assert on the rest.
        self.assertEqual(cmd[1:], ["-t", "site.qth"])

    def test_full_payload(self):
        cmd = _build_command({
            "tx_qth":      "tx.qth",
            "rx_qth":      "rx.qth",
            "output_base": "out/coverage",
            "flags":       ["-metric", "-dbm"],
        })
        self.assertEqual(
            cmd[1:],
            ["-t", "tx.qth", "-r", "rx.qth", "-o", "out/coverage", "-metric", "-dbm"],
        )

    def test_flag_values_are_coerced_to_str(self):
        cmd = _build_command({"tx_qth": "x", "flags": [123]})
        self.assertEqual(cmd[-1], "123")


class RunStatsTests(unittest.TestCase):
    def test_initial_snapshot_is_zero(self):
        s = RunStats().snapshot()
        self.assertEqual(s["total_runs"], 0)
        self.assertEqual(s["success_count"], 0)
        self.assertEqual(s["failure_count"], 0)
        self.assertEqual(s["success_rate"], 0.0)
        self.assertEqual(s["avg_runtime_seconds"], 0.0)
        self.assertIsNone(s["last_run"])

    def test_records_success(self):
        stats = RunStats()
        stats.record(returncode=0, runtime_seconds=1.5, command_string="splat -t a.qth")
        s = stats.snapshot()
        self.assertEqual(s["total_runs"], 1)
        self.assertEqual(s["success_count"], 1)
        self.assertEqual(s["failure_count"], 0)
        self.assertEqual(s["success_rate"], 1.0)
        self.assertEqual(s["avg_runtime_seconds"], 1.5)
        self.assertEqual(s["last_run"]["returncode"], 0)
        self.assertEqual(s["last_run"]["command_string"], "splat -t a.qth")

    def test_records_failure(self):
        stats = RunStats()
        stats.record(returncode=2, runtime_seconds=0.5, command_string="splat -t bad.qth")
        s = stats.snapshot()
        self.assertEqual(s["total_runs"], 1)
        self.assertEqual(s["failure_count"], 1)
        self.assertEqual(s["success_count"], 0)
        self.assertEqual(s["success_rate"], 0.0)

    def test_avg_runtime_across_runs(self):
        stats = RunStats()
        stats.record(returncode=0, runtime_seconds=2.0, command_string="splat 1")
        stats.record(returncode=0, runtime_seconds=4.0, command_string="splat 2")
        self.assertEqual(stats.snapshot()["avg_runtime_seconds"], 3.0)

    def test_success_rate_mixed(self):
        stats = RunStats()
        stats.record(returncode=0, runtime_seconds=1, command_string="ok")
        stats.record(returncode=1, runtime_seconds=1, command_string="fail")
        stats.record(returncode=0, runtime_seconds=1, command_string="ok")
        s = stats.snapshot()
        self.assertEqual(s["total_runs"], 3)
        self.assertEqual(s["success_count"], 2)
        self.assertEqual(s["success_rate"], round(2 / 3, 4))


class HealthAndVersionRoutesTests(unittest.TestCase):
    """Pin the existing /healthz and /version contract so this PR can't drift it."""

    def setUp(self):
        self.client = app.test_client()

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})

    def test_version_shape(self):
        resp = self.client.get("/version")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["sidecar"], "genoa-splat-sidecar")
        self.assertIn("splat_bin", body)
        self.assertIn("workdir", body)


class StatsRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_stats_route_returns_expected_keys(self):
        # Don't assert exact values — the module-level _stats is shared
        # state across tests in the same process. Shape is what we pin.
        resp = self.client.get("/api/v1/stats")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        for key in ("total_runs", "success_count", "failure_count",
                    "success_rate", "avg_runtime_seconds", "last_run"):
            self.assertIn(key, body)
        self.assertIsInstance(body["total_runs"], int)
        self.assertIsInstance(body["success_rate"], (int, float))


class SplatRunRouteTests(unittest.TestCase):
    """subprocess.run is patched so we don't need a real splat binary."""

    def setUp(self):
        self.client = app.test_client()

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            "/api/v1/splat/run",
            data="{not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_tx_qth_returns_400(self):
        resp = self.client.post("/api/v1/splat/run", json={})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tx_qth", resp.get_json()["error"])

    @mock.patch("genoa_sidecar.subprocess.run")
    def test_successful_run_returns_subprocess_output_and_preserves_contract(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
        resp = self.client.post("/api/v1/splat/run", json={"tx_qth": "x.qth"})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        # Existing fields callers depend on:
        self.assertEqual(body["returncode"], 0)
        self.assertEqual(body["stdout"], "ok")
        self.assertEqual(body["stderr"], "")
        self.assertIn("-t", body["command"])
        self.assertIn("command_string", body)

    @mock.patch("genoa_sidecar.subprocess.run")
    def test_run_increments_stats(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        before = self.client.get("/api/v1/stats").get_json()["total_runs"]
        self.client.post("/api/v1/splat/run", json={"tx_qth": "x.qth"})
        after = self.client.get("/api/v1/stats").get_json()["total_runs"]
        self.assertEqual(after, before + 1)

    @mock.patch("genoa_sidecar.subprocess.run")
    def test_failed_run_records_failure(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=2, stdout="", stderr="boom")
        before = self.client.get("/api/v1/stats").get_json()["failure_count"]
        self.client.post("/api/v1/splat/run", json={"tx_qth": "x.qth"})
        after = self.client.get("/api/v1/stats").get_json()["failure_count"]
        self.assertEqual(after, before + 1)


if __name__ == "__main__":
    unittest.main()
