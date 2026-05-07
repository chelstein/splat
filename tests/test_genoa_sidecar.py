"""Tests for genoa_sidecar.

Run:    python -m unittest discover -s tests -v
or:     python -m unittest tests.test_genoa_sidecar -v

No extra deps — stdlib unittest + Flask test client (Flask is already a
production dependency).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from genoa_sidecar import (
    _build_command,
    _flag_has_traversal,
    _is_authorized,
    _path_inside_workdir,
    app,
    RunStats,
    sweep_workdir,
)


class BuildCommandTests(unittest.TestCase):
    def test_requires_tx_qth(self):
        self.assertEqual(_build_command({}), "tx_qth is required")

    def test_minimal_payload_produces_t_flag(self):
        cmd = _build_command({"tx_qth": "site.qth"})
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

    def test_tx_qth_traversal_rejected(self):
        err = _build_command({"tx_qth": "../etc/passwd"})
        self.assertIsInstance(err, str)
        self.assertIn("tx_qth", err)
        self.assertIn("WORKDIR", err)

    def test_absolute_tx_qth_rejected(self):
        err = _build_command({"tx_qth": "/etc/passwd"})
        self.assertIsInstance(err, str)
        self.assertIn("tx_qth", err)

    def test_rx_qth_traversal_rejected(self):
        err = _build_command({"tx_qth": "site.qth", "rx_qth": "../../etc/host"})
        self.assertIsInstance(err, str)
        self.assertIn("rx_qth", err)

    def test_output_base_traversal_rejected(self):
        err = _build_command({"tx_qth": "site.qth", "output_base": "../foo.ppm"})
        self.assertIsInstance(err, str)
        self.assertIn("output_base", err)

    def test_relative_paths_inside_workdir_pass(self):
        cmd = _build_command({
            "tx_qth":      "sample_data/wnju-dt.qth",
            "output_base": "out/region/coverage",
        })
        self.assertIsInstance(cmd, list)

    def test_flag_dotdot_rejected(self):
        err = _build_command({"tx_qth": "site.qth", "flags": ["-o", "../sneaky"]})
        self.assertIsInstance(err, str)
        self.assertIn("path traversal", err)

    def test_flag_absolute_rejected(self):
        err = _build_command({"tx_qth": "site.qth", "flags": ["-o", "/etc/foo"]})
        self.assertIsInstance(err, str)
        self.assertIn("path traversal", err)

    def test_benign_flags_still_accepted(self):
        cmd = _build_command({
            "tx_qth": "site.qth",
            "flags":  ["-metric", "-dbm", "-d", "sample_data"]
        })
        self.assertIsInstance(cmd, list)
        self.assertIn("-metric", cmd)
        self.assertIn("-d", cmd)


class PathConfinementTests(unittest.TestCase):
    def test_none_and_empty_pass(self):
        self.assertTrue(_path_inside_workdir(None))
        self.assertTrue(_path_inside_workdir(""))

    def test_simple_relative_passes(self):
        self.assertTrue(_path_inside_workdir("foo.qth"))
        self.assertTrue(_path_inside_workdir("sub/foo.qth"))

    def test_dotdot_rejected(self):
        self.assertFalse(_path_inside_workdir("../escape"))
        self.assertFalse(_path_inside_workdir("sub/../../escape"))

    def test_absolute_outside_workdir_rejected(self):
        self.assertFalse(_path_inside_workdir("/etc/passwd"))
        self.assertFalse(_path_inside_workdir("/tmp/foo"))

    def test_absolute_inside_workdir_passes(self):
        from genoa_sidecar import WORKDIR
        self.assertTrue(_path_inside_workdir(str(WORKDIR / "foo.qth")))


class FlagTraversalTests(unittest.TestCase):
    def test_dotdot(self):
        self.assertTrue(_flag_has_traversal("../foo"))
        self.assertTrue(_flag_has_traversal("a/../b"))

    def test_absolute(self):
        self.assertTrue(_flag_has_traversal("/etc/foo"))

    def test_benign_flag(self):
        self.assertFalse(_flag_has_traversal("-metric"))
        self.assertFalse(_flag_has_traversal("-dbm"))
        self.assertFalse(_flag_has_traversal("out/foo"))

    def test_non_string_coerced(self):
        self.assertFalse(_flag_has_traversal(123))


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
    def setUp(self):
        self.client = app.test_client()

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"status": "ok"})

    def test_version_includes_legacy_keys(self):
        resp = self.client.get("/version")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["sidecar"], "genoa-splat-sidecar")
        self.assertIn("splat_bin", body)
        self.assertIn("workdir", body)

    def test_version_includes_new_metadata(self):
        resp = self.client.get("/version")
        body = resp.get_json()
        for key in ("git_commit_sha", "build_time", "splat_version", "auth_required"):
            self.assertIn(key, body)

    def test_version_auth_required_false_when_token_unset(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GENOA_API_TOKEN", None)
            resp = self.client.get("/version")
            self.assertFalse(resp.get_json()["auth_required"])

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_version_auth_required_true_when_token_set(self):
        resp = self.client.get("/version")
        self.assertTrue(resp.get_json()["auth_required"])

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_healthz_unauthenticated_with_token_set(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_version_unauthenticated_with_token_set(self):
        resp = self.client.get("/version")
        self.assertEqual(resp.status_code, 200)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_stats_unauthenticated_with_token_set(self):
        resp = self.client.get("/api/v1/stats")
        self.assertEqual(resp.status_code, 200)


class StatsRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_stats_route_returns_expected_keys(self):
        resp = self.client.get("/api/v1/stats")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        for key in ("total_runs", "success_count", "failure_count",
                    "success_rate", "avg_runtime_seconds", "last_run"):
            self.assertIn(key, body)
        self.assertIsInstance(body["total_runs"], int)
        self.assertIsInstance(body["success_rate"], (int, float))


class IsAuthorizedTests(unittest.TestCase):
    def _req(self, headers=None):
        class Req:
            pass
        r = Req()
        r.headers = headers or {}
        return r

    def test_no_token_configured_passes(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GENOA_API_TOKEN", None)
            self.assertTrue(_is_authorized(self._req()))
            self.assertTrue(_is_authorized(self._req({"Authorization": "Bearer anything"})))

    def test_empty_token_configured_passes(self):
        with mock.patch.dict(os.environ, {"GENOA_API_TOKEN": ""}):
            self.assertTrue(_is_authorized(self._req()))

    def test_correct_token_passes(self):
        with mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"}):
            self.assertTrue(_is_authorized(self._req({"Authorization": "Bearer sekret"})))

    def test_wrong_token_fails(self):
        with mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"}):
            self.assertFalse(_is_authorized(self._req({"Authorization": "Bearer wrong"})))

    def test_missing_header_fails(self):
        with mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"}):
            self.assertFalse(_is_authorized(self._req()))

    def test_wrong_scheme_fails(self):
        with mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"}):
            self.assertFalse(_is_authorized(self._req({"Authorization": "Basic sekret"})))
            self.assertFalse(_is_authorized(self._req({"Authorization": "sekret"})))


class SplatRunRouteTests(unittest.TestCase):
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

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_run_without_auth_header_returns_401(self):
        resp = self.client.post("/api/v1/splat/run", json={"tx_qth": "x.qth"})
        self.assertEqual(resp.status_code, 401)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_run_with_wrong_token_returns_401(self):
        resp = self.client.post(
            "/api/v1/splat/run",
            json={"tx_qth": "x.qth"},
            headers={"Authorization": "Bearer wrong"},
        )
        self.assertEqual(resp.status_code, 401)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    @mock.patch("genoa_sidecar.subprocess.run")
    def test_run_with_correct_token_returns_200(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
        resp = self.client.post(
            "/api/v1/splat/run",
            json={"tx_qth": "x.qth"},
            headers={"Authorization": "Bearer sekret"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_run_with_traversal_in_tx_qth_returns_400(self):
        resp = self.client.post(
            "/api/v1/splat/run",
            json={"tx_qth": "../etc/passwd"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("WORKDIR", resp.get_json()["error"])

    def test_run_with_absolute_output_base_returns_400(self):
        resp = self.client.post(
            "/api/v1/splat/run",
            json={"tx_qth": "x.qth", "output_base": "/etc/foo"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("output_base", resp.get_json()["error"])

    def test_run_with_traversal_flag_returns_400(self):
        resp = self.client.post(
            "/api/v1/splat/run",
            json={"tx_qth": "x.qth", "flags": ["-o", "../sneaky"]},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("path traversal", resp.get_json()["error"])


class ArtifactsRouteTests(unittest.TestCase):
    """Artifact list + fetch routes.

    Each test gets a unique sub-directory under WORKDIR so file seeds
    don't collide across tests, and the directory is removed in tearDown.
    """

    @classmethod
    def setUpClass(cls):
        from genoa_sidecar import WORKDIR
        cls.WORKDIR = WORKDIR

    def setUp(self):
        self.client = app.test_client()
        self.test_dir = self.WORKDIR / f"test_artifacts_{os.getpid()}_{time.time_ns()}"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _seed(self, rel: str, content: bytes = b"x") -> Path:
        p = self.test_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.WORKDIR).as_posix()

    # ---- list ----

    def test_list_returns_expected_top_level_keys(self):
        resp = self.client.get("/api/v1/artifacts")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        for key in ("workdir", "count", "artifacts"):
            self.assertIn(key, body)
        self.assertIsInstance(body["artifacts"], list)
        self.assertEqual(body["count"], len(body["artifacts"]))

    def test_list_returns_seeded_files(self):
        a = self._seed("a.ppm", b"PPM A")
        b = self._seed("nested/b.kml", b"<kml/>")
        body = self.client.get("/api/v1/artifacts").get_json()
        paths = {item["path"] for item in body["artifacts"]}
        self.assertIn(self._rel(a), paths)
        self.assertIn(self._rel(b), paths)

    def test_list_item_has_expected_per_artifact_keys(self):
        seeded = self._seed("item.ppm", b"abcde")
        body = self.client.get("/api/v1/artifacts").get_json()
        rel = self._rel(seeded)
        item = next((i for i in body["artifacts"] if i["path"] == rel), None)
        self.assertIsNotNone(item, f"seeded file {rel} not in listing")
        self.assertEqual(item["size_bytes"], 5)
        self.assertEqual(item["url"], f"/api/v1/artifacts/{rel}")
        self.assertIn("modified_at", item)

    def test_list_sorts_newest_first(self):
        old = self._seed("old.ppm", b"old")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        new = self._seed("new.ppm", b"new")
        items = self.client.get("/api/v1/artifacts").get_json()["artifacts"]
        rel_old = self._rel(old)
        rel_new = self._rel(new)
        positions = {item["path"]: idx for idx, item in enumerate(items)}
        self.assertIn(rel_old, positions)
        self.assertIn(rel_new, positions)
        self.assertLess(positions[rel_new], positions[rel_old])

    # ---- get ----

    def test_get_returns_file_content(self):
        seeded = self._seed("file.txt", b"hello world")
        resp = self.client.get(f"/api/v1/artifacts/{self._rel(seeded)}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"hello world")

    def test_get_missing_returns_404(self):
        resp = self.client.get("/api/v1/artifacts/this/does/not/exist.foo")
        self.assertEqual(resp.status_code, 404)

    def test_get_traversal_blocked(self):
        # Encoded traversal in the URL path.  Either 400 (caught by
        # _path_inside_workdir) or 404 (caught by send_from_directory)
        # is acceptable — both refuse the escape.
        resp = self.client.get("/api/v1/artifacts/..%2Fetc%2Fpasswd")
        self.assertIn(resp.status_code, (400, 404))

    def test_get_nested_path(self):
        seeded = self._seed("deep/nested/coverage.ppm", b"deep")
        resp = self.client.get(f"/api/v1/artifacts/{self._rel(seeded)}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"deep")

    # ---- auth ----

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_list_requires_auth_when_token_set(self):
        resp = self.client.get("/api/v1/artifacts")
        self.assertEqual(resp.status_code, 401)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_list_with_correct_token(self):
        resp = self.client.get(
            "/api/v1/artifacts",
            headers={"Authorization": "Bearer sekret"},
        )
        self.assertEqual(resp.status_code, 200)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_get_requires_auth_when_token_set(self):
        seeded = self._seed("auth_test.txt", b"data")
        resp = self.client.get(f"/api/v1/artifacts/{self._rel(seeded)}")
        self.assertEqual(resp.status_code, 401)

    @mock.patch.dict(os.environ, {"GENOA_API_TOKEN": "sekret"})
    def test_get_with_correct_token(self):
        seeded = self._seed("auth_pass.txt", b"data")
        resp = self.client.get(
            f"/api/v1/artifacts/{self._rel(seeded)}",
            headers={"Authorization": "Bearer sekret"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, b"data")


class SweepWorkdirTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _touch(self, rel: str, age_seconds: float) -> Path:
        path = self.workdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        old = time.time() - age_seconds
        os.utime(path, (old, old))
        return path

    def test_retention_zero_disables_sweep(self):
        old = self._touch("a.ppm", age_seconds=10 * 3600)
        self.assertEqual(sweep_workdir(self.workdir, retention_hours=0), 0)
        self.assertTrue(old.exists())

    def test_deletes_files_older_than_retention(self):
        old = self._touch("old.ppm", age_seconds=48 * 3600)
        fresh = self._touch("fresh.ppm", age_seconds=1 * 3600)
        deleted = sweep_workdir(self.workdir, retention_hours=24)
        self.assertEqual(deleted, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_skips_sample_data_tree(self):
        old_sample = self._touch("sample_data/wnju-dt.qth", age_seconds=10000 * 3600)
        old_run = self._touch("out/coverage.ppm", age_seconds=10000 * 3600)
        deleted = sweep_workdir(self.workdir, retention_hours=24)
        self.assertEqual(deleted, 1)
        self.assertTrue(old_sample.exists())
        self.assertFalse(old_run.exists())

    def test_recurses_into_subdirectories(self):
        old_deep = self._touch("out/region/coverage.ppm", age_seconds=48 * 3600)
        sweep_workdir(self.workdir, retention_hours=24)
        self.assertFalse(old_deep.exists())

    def test_now_argument_is_deterministic(self):
        path = self._touch("x.ppm", age_seconds=10 * 3600)
        deleted = sweep_workdir(self.workdir, retention_hours=24, now=time.time())
        self.assertEqual(deleted, 0)
        deleted = sweep_workdir(self.workdir, retention_hours=24, now=time.time() + 100 * 3600)
        self.assertEqual(deleted, 1)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
