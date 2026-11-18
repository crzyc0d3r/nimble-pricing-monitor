from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import app


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.before = app.load_snapshot(app.HERE/"fixtures/baseline.json")
        self.after = app.load_snapshot(app.HERE/"fixtures/current.json")

    def test_semantic_changes(self):
        r = app.compare(self.before, self.after)
        self.assertEqual(len(r["changes"]), 4)
        self.assertEqual([x["kind"] for x in r["changes"]], ["added", "amount", "features", "features"])
        self.assertFalse(any(x["plan"] == "Team" and x["kind"] == "amount" for x in r["changes"]))
        self.assertFalse(r["quality_evidence"])

    def test_plan_removal_and_period_change(self):
        after = deepcopy(self.after); after["plans"] = after["plans"][:1]
        after["plans"][0]["period"] = "year"
        r = app.compare(self.before, after)
        self.assertIn("removed", [x["kind"] for x in r["changes"]])
        self.assertIn("period", [x["kind"] for x in r["changes"]])

    def test_incompatible_dates_or_competitors(self):
        for field, value in (("date", "2026-09-08"), ("date", "2026-09-09"), ("competitor", "Other")):
            after = deepcopy(self.after); after[field] = value
            with self.assertRaises(ValueError): app.compare(self.before, after)

    def test_pricing_input_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"bad.json"
            for amount in (True, "NaN", "Infinity", "-1", "1e99", "0.0000001", "bad"):
                value = deepcopy(self.before); value["plans"][0]["amount"] = amount
                path.write_text(json.dumps(value))
                with self.subTest(amount=amount), self.assertRaises(ValueError): app.load_snapshot(path)

    def test_duplicate_plan_and_credentials_in_url_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"bad.json"; value = deepcopy(self.before)
            value["plans"].append(value["plans"][0]); path.write_text(json.dumps(value))
            with self.assertRaises(ValueError): app.load_snapshot(path)
        for url in ("http://example.com", "https://user:password@example.com", "file:///tmp/x"):
            with self.assertRaises(ValueError): app.native_commands(url)

    def test_local_artifacts_and_role_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/"run"; app.save_run(out, self.before, self.after, app.compare(self.before, self.after))
            self.assertTrue((out/"snapshots/2026-09-09.md").is_file())
            self.assertIn("Local alert draft", app.read_room(out, "alerts", "Ren"))
            for actor in ("Theo", "Mara", "unknown"):
                with self.assertRaises(PermissionError): app.read_room(out, "alerts", actor)
            self.assertIn("Local intel", app.read_room(out, "intel", "Theo"))
            with self.assertRaises(FileExistsError): app.save_run(out, self.before, self.after, {})

    def test_cli_commands_preserve_url_as_one_argument(self):
        url = "https://example.com/pricing?q=a;b"
        result = app.native_commands(url)
        self.assertEqual(result["extract"][result["extract"].index("--url")+1], url)
        self.assertEqual(result["map"][-4:], ["--sitemap", "only", "--limit", "200"])

    def test_cli_demo_json_and_permission_error(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): self.assertEqual(app.main(["demo"]), 0)
        self.assertEqual(json.loads(out.getvalue())["source_kind"], "synthetic")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["view", "--run-dir", "missing", "--room", "alerts", "--agent", "Theo"]), 1)


if __name__ == "__main__": unittest.main()
