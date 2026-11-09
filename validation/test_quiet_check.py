"""Pass/fail tests for check_hpc_jobs.py and hpc_job_probe.py; no HPC or project data needed.

    python3 -m unittest -v test_quiet_check.py      (from this folder)
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills",
                                "materials-project-workflow", "scripts"))
import check_hpc_jobs as C  # noqa: E402
import hpc_job_probe as P  # noqa: E402


def oszicar(steps, scf=5, e0=-100.0, de=-0.1, nan_at=None, tail_scf=0, prefix="DAV:"):
    out = []
    for i in range(1, steps + 1):
        out += ["%s %3d  -0.1E+03  -0.1E-01  -0.1E-01  100  0.1E+00" % (prefix, n) for n in range(1, scf + 1)]
        e = "*****" if nan_at == i else "%.8E" % (e0 + de * i)
        out.append("   %d F= %s E0= %s d E = -.1E-01" % (i, e, e))
    out += ["%s %3d  -0.1E+03  -0.1E-01  -0.1E-01  100  0.1E+00" % (prefix, n) for n in range(1, tail_scf + 1)]
    return "\n".join(out) + "\n"


def run_dir(root, name, osz=None, incar="IBRION = 2\nNELM = 60\n", extra=None, age_min=0):
    path = os.path.join(root, name)
    os.makedirs(path)
    with open(os.path.join(path, "INCAR"), "w") as handle:
        handle.write(incar)
    if osz is not None:
        with open(os.path.join(path, "OSZICAR"), "w") as handle:
            handle.write(osz)
        t = time.time() - age_min * 60
        os.utime(os.path.join(path, "OSZICAR"), (t, t))
    for name, text in (extra or {}).items():
        with open(os.path.join(path, name), "w") as handle:
            handle.write(text)
    return path


def sigs(result):
    return [f["sig"] for f in result["flags"]]


class Health(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="quiet_check_")

    def health(self, name, **kw):
        return sigs(P.health(run_dir(self.root, name, **kw), job_id="123", elapsed_min=60))

    def test_healthy_run_has_no_flag(self):
        self.assertEqual(self.health("ok", osz=oszicar(10)), [])

    def test_nan(self):
        self.assertIn("nan:OSZICAR", self.health("nan", osz=oszicar(5, nan_at=5)))

    def test_geometry_two_steps_at_nelm(self):
        self.assertIn("nelm2:OSZICAR:1", self.health("g", osz=oszicar(6, scf=60)))

    def test_geometry_nelm_hidden_by_next_scf_in_progress(self):
        # Steps 5 and 6 hit NELM; step 7's electronic loop has just started.
        self.assertIn("nelm2:OSZICAR:1", self.health("g7", osz=oszicar(6, scf=60, tail_scf=3)))

    def test_nelm_in_first_two_steps_is_ignored(self):
        self.assertEqual(self.health("first2", osz=oszicar(2, scf=60)), [])

    def test_frequency_nelm(self):
        self.assertIn("nelm:OSZICAR", self.health("f", osz=oszicar(3, scf=100), incar="IBRION = 5\nNELM = 100\n"))

    def test_stall(self):
        self.assertTrue(any(s.startswith("stall:") for s in self.health("stall", osz=oszicar(5), age_min=90)))

    def test_no_output(self):
        self.assertEqual(self.health("none"), ["no_output"])

    def test_fatal_text(self):
        found = self.health("err", osz=oszicar(4), extra={"slurm-123.out": "VERY BAD NEWS! internal error\n"})
        self.assertIn("err:slurm-123.out:VERY BAD NEWS:1", found)

    def test_harmless_stderr_text(self):
        self.assertEqual(self.health("fonts", osz=oszicar(4),
                                     extra={"error_123.txt": "findfont: Font family 'arial' not found.\n"}), [])

    def test_energy_up_reported_again_only_in_a_higher_band(self):
        a = [s for s in self.health("up8", osz=oszicar(8, de=0.3)) if s.startswith("energy_up")]
        b = [s for s in self.health("up9", osz=oszicar(9, de=0.3)) if s.startswith("energy_up")]
        c = [s for s in self.health("up20", osz=oszicar(20, de=0.3)) if s.startswith("energy_up")]
        self.assertEqual(a, ["energy_up:OSZICAR:1"])
        self.assertEqual(a, b)
        self.assertEqual(c, ["energy_up:OSZICAR:2"])

    def test_unrecognised_scf_lines(self):
        self.assertIn("unparsed:OSZICAR", self.health("odd", osz=oszicar(4, prefix="XYZ12:")))

    def test_all_scf_line_formats(self):
        for prefix in ("DAV:", "RMM:", "CGA:", "SDA:", "trl"):
            self.assertEqual(self.health("fmt_" + prefix.strip(":"), osz=oszicar(4, prefix=prefix)), [], prefix)


def q(i, state="RUNNING", reason="None", elapsed=30.0):
    return {"id": i, "state": state, "elapsed_min": elapsed, "left": "1:00:00", "reason": reason,
            "dir": "/x/" + i, "name": "job" + i}


def acct(i, state="COMPLETED"):
    return {"id": i, "name": "job" + i, "state": state, "exit": "0:0", "elapsed": "1:00:00", "end": "x", "dir": "/x/" + i}


def result(queue, accounting=(), runs=None, now=None):
    return {"ok": True, "now": now or time.time(), "queue": queue, "accounting": list(accounting), "runs": runs or {}}


class Compare(unittest.TestCase):
    def events(self, baseline, res):
        return [s for s, _ in C.compare(baseline, res)[0]]

    def test_end_and_start_between_checks_both_reported(self):
        _, base = C.compare({}, result([q("1")]))
        base["acked"] = ["started:1"]
        ev = self.events(base, result([q("2")], [acct("1")]))
        self.assertIn("exit:1", ev)
        self.assertIn("started:2", ev)

    def test_requeue_to_pending(self):
        _, base = C.compare({}, result([q("1")]))
        ev = self.events(base, result([q("1", "PENDING", "BeginTime")]))
        self.assertTrue(any(s.startswith("requeued:1:") for s in ev))

    def test_requeue_and_restart_between_checks(self):
        _, base = C.compare({}, result([q("1", elapsed=300)], now=1000.0))
        ev = self.events(base, result([q("1", elapsed=10)], now=2800.0))
        self.assertTrue(any(s.startswith("restarted:1:") for s in ev))

    def test_restart_soon_after_start(self):
        # 10 min in at the first check; 30 min later it shows 20 min, not about 40.
        _, base = C.compare({}, result([q("1", elapsed=10)], now=1000.0))
        ev = self.events(base, result([q("1", elapsed=20)], now=2800.0))
        self.assertTrue(any(s.startswith("restarted:1:") for s in ev))

    def test_normal_progress_is_not_a_restart(self):
        _, base = C.compare({}, result([q("1", elapsed=10)], now=1000.0))
        ev = self.events(base, result([q("1", elapsed=40)], now=2800.0))
        self.assertFalse(any(s.startswith("restarted") for s in ev))

    def test_vanished_job_reports_its_end_once_accounting_appears(self):
        _, base = C.compare({}, result([q("1"), q("2")]))
        ev, base = C.compare(base, result([q("2")]))
        self.assertIn("vanished:1", [s for s, _ in ev])
        base["acked"] = ["vanished:1", "started:1", "started:2"]
        self.assertIn("1", base["jobs"])
        self.assertIn("exit:1", self.events(base, result([q("2")], [acct("1", "TIMEOUT")])))

    def test_no_jobs_is_attention(self):
        self.assertIn("empty", self.events({}, result([])))

    def test_unusual_pending_reason(self):
        self.assertIn("pending:3:AssocGrpBillingMinutes", self.events({}, result([q("3", "PENDING", "AssocGrpBillingMinutes")])))


class Main(unittest.TestCase):
    """End-to-end runs of main() with the ssh call replaced."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="quiet_check_main_")
        self.state = os.path.join(self.dir, "state.json")
        self.records = os.path.join(self.dir, "records")

    def run_main(self, *extra, res=None, fail=None):
        argv = ["check_hpc_jobs.py", "--state", self.state]
        if not extra or extra[0] != "--ack":
            argv += ["--host", "h", "--prefix", "/x", "--record-dir", self.records]
        argv += list(extra)

        def fake_fetch(args, since):
            if fail:
                raise C.CheckFailed("ssh exit 255: no route", 255)
            return 0, res

        buf = io.StringIO()
        with mock.patch.object(C, "fetch", fake_fetch), mock.patch.object(sys, "argv", argv), redirect_stdout(buf):
            code = C.main()
        return code, buf.getvalue()

    @staticmethod
    def report_id(text):
        return text.split("report ")[1].split(";")[0]

    def test_init_lists_known_jobs_and_reports_existing_problem(self):
        res = result([q("1"), q("2")], runs={"2": {"flags": [{"sig": "nan:OSZICAR", "msg": "NaN"}]}})
        code, text = self.run_main("--init", res=res)
        self.assertEqual(code, 1)
        self.assertIn("already present", text)
        self.assertIn("NaN", text)
        code, text = self.run_main(res=res)
        self.assertEqual(code, 1, "existing NaN must stay reported until acknowledged")

    def test_init_without_jobs_fails(self):
        code, text = self.run_main("--init", res=result([]))
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(self.state))

    def test_ack_needs_the_latest_report(self):
        self.run_main("--init", res=result([q("1")]))
        code, text = self.run_main(res=result([q("1"), q("2")]))
        self.assertEqual(code, 1)
        first = self.report_id(text)
        code, text = self.run_main(res=result([q("1"), q("2"), q("3")]))
        latest = self.report_id(text)
        self.assertNotEqual(first, latest)
        self.assertEqual(self.run_main("--ack", first)[0], 2)
        self.assertEqual(self.run_main("--ack", latest)[0], 0)
        self.assertEqual(self.run_main(res=result([q("1"), q("2"), q("3")]))[0], 0)

    def test_failed_check_leaves_nothing_to_ack(self):
        self.run_main("--init", res=result([q("1")]))
        code, text = self.run_main(res=result([q("1"), q("2")]))
        report = self.report_id(text)
        code, text = self.run_main(fail=True)
        self.assertEqual(code, 2)
        self.assertNotIn("report", text)
        code, text = self.run_main(res=result([q("1"), q("2")]))
        self.assertEqual(code, 1, "unacknowledged start must still be reported after a failed check")
        self.assertNotEqual(self.report_id(text), report)

    def test_transient_problem_kept_until_acknowledged(self):
        self.run_main("--init", res=result([q("1")]))
        flagged = result([q("1")], runs={"1": {"flags": [{"sig": "nelm2:OSZICAR:2", "msg": "two NELM steps"}]}})
        self.assertEqual(self.run_main(res=flagged)[0], 1)
        code, text = self.run_main(res=result([q("1")]))
        self.assertEqual(code, 1)
        self.assertIn("two NELM steps", text)
        self.assertEqual(self.run_main("--ack", self.report_id(text))[0], 0)
        self.assertEqual(self.run_main(res=result([q("1")]))[0], 0)

    def test_probe_error_cannot_be_acknowledged(self):
        self.run_main("--init", res=result([q("1")]))
        broken = result([q("1")], runs={"1": {"flags": [{"sig": "probe_error", "msg": "probe failed"}]}})
        code, text = self.run_main(res=broken)
        self.run_main("--ack", self.report_id(text))
        self.assertEqual(self.run_main(res=broken)[0], 1)

    def test_corrupt_state_is_a_failed_check(self):
        with open(self.state, "w") as handle:
            handle.write("{not json")
        code, text = self.run_main(res=result([q("1")]))
        self.assertEqual(code, 2)
        self.assertIn("check failed", text)

    def test_init_refused_while_a_report_is_unacknowledged(self):
        self.run_main("--init", res=result([q("1")]))
        code, text = self.run_main(res=result([q("1"), q("2")]))
        report = self.report_id(text)
        self.assertEqual(self.run_main("--init", res=result([q("1"), q("2")]))[0], 2)
        self.assertEqual(self.run_main("--ack", report)[0], 0)

    def test_record_failure_saves_nothing_and_offers_no_ack(self):
        self.run_main("--init", res=result([q("1")]))
        with open(self.records + "_blocker", "w"):
            pass
        self.records = self.records + "_blocker"  # a file, so the record directory cannot be created
        code, text = self.run_main(res=result([q("1"), q("2")]))
        self.assertEqual(code, 2)
        self.assertNotIn("--ack", text)
        self.assertFalse(os.path.exists(self.state + ".pending"))

    def test_ssh_exit_recorded_when_a_later_step_fails(self):
        self.run_main("--init", res=result([q("1")]))
        bad = result([q("1")])
        del bad["accounting"]
        self.assertEqual(self.run_main(res=bad)[0], 2)
        failed = [json.load(open(os.path.join(self.records, f))) for f in os.listdir(self.records)]
        failed = [r for r in failed if r["verdict"] == "CHECK_FAILED"]
        self.assertEqual(failed[0]["ssh_exit"], 0)

    def test_one_record_per_check(self):
        self.run_main("--init", res=result([q("1")]))
        self.run_main(fail=True)
        files = sorted(os.listdir(self.records))
        self.assertEqual(len(files), 2)
        records = [json.load(open(os.path.join(self.records, f))) for f in files]
        self.assertEqual({r["verdict"] for r in records}, {"QUIET", "CHECK_FAILED"})
        failed = [r for r in records if r["verdict"] == "CHECK_FAILED"][0]
        self.assertEqual(failed["ssh_exit"], 255)
        ok = [r for r in records if r["verdict"] == "QUIET"][0]
        self.assertEqual(ok["jobs"], [{"id": "1", "state": "RUNNING"}])
        self.assertEqual(set(ok["scripts"]), {"check_hpc_jobs.py", "hpc_job_probe.py"})


class Probe(unittest.TestCase):
    def test_parses_queue_and_accounting_and_converts_time_zone(self):
        calls = []

        marks = {}

        def fake_run(cmd, env=None):
            calls.append(cmd)
            if cmd[0] == "sacct":
                marks.setdefault("after_squeue", time.time())
                time.sleep(0.05)
            if cmd[0] == "squeue":
                return ("101|RUNNING|1-02:03:04|10:00|None|/proj/a|jobA\n"
                        "102|PENDING|0:00|1-00:00:00|Priority|/other/b|jobB\n")
            if cmd[0] == "sacct":
                return ("99|jobZ|TIMEOUT|0:0|1-00:00:16|1792100000|1792186416|/proj/z\n"
                        "98|jobY|COMPLETED|0:0|01:00:00|1792100000|1792103600|/other/y\n")
            return "user\n"

        old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Australia/Perth"
        time.tzset()
        try:
            with mock.patch.object(P, "run", fake_run), mock.patch.object(P, "health", lambda *a, **k: {"flags": []}):
                out = P.probe({"prefixes": ["/proj"], "since_epoch": 1792166400})  # 2026-10-17 00:00 Perth
        finally:
            if old_tz is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = old_tz
            time.tzset()
        self.assertEqual([j["id"] for j in out["queue"]], ["101"])
        self.assertLess(out["now"], marks["after_squeue"] + 1e-6)
        self.assertAlmostEqual(out["queue"][0]["elapsed_min"], 1563 + 4 / 60.0)
        self.assertEqual([j["id"] for j in out["accounting"]], ["99"])
        sacct = [c for c in calls if c[0] == "sacct"][0]
        self.assertEqual(sacct[sacct.index("-S") + 1], "2026-10-17T00:00:00")


if __name__ == "__main__":
    unittest.main()
