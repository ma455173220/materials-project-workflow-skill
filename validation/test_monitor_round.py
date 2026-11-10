"""Pass/fail tests for monitor_round.py; the scheduler is faked and copies are local (needs rsync).

    python3 -m unittest -v test_monitor_round.py      (from this folder)
"""
import csv
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skills",
                                "materials-project-workflow", "scripts"))
import hpc_job_probe as P  # noqa: E402
import monitor_round as M  # noqa: E402

VASP_END = "General timing and accounting informations for this job:\n"
HAVE_RSYNC = shutil.which("rsync") is not None


def oszicar(steps, scf=5):
    out = []
    for i in range(1, steps + 1):
        out += ["DAV: %3d  -0.1E+03  -0.1E-01  -0.1E-01  100  0.1E+00" % n for n in range(1, scf + 1)]
        out.append("   %d F= %.8E E0= %.8E d E = -.1E-01" % (i, -100 - 0.01 * i, -100 - 0.01 * i))
    return "\n".join(out) + "\n"


VASP_RELAX_OK = {"INCAR": "IBRION = 3\nNSW = 200\nNELM = 60\n", "OSZICAR": oszicar(10),
                 "OUTCAR": "reached required accuracy - stopping structural energy minimisation\n" + VASP_END,
                 "WAVECAR": "big"}


@unittest.skipUnless(HAVE_RSYNC, "rsync is not installed")
class Project(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="monitor_round_")
        self.remote = os.path.join(self.base, "remote")
        self.root = os.path.join(self.base, "project")
        os.makedirs(self.root)
        self.groups = []
        self.queue, self.accounting, self.runs = [], [], {}
        self.records = os.path.join(self.root, "JOBS.csv")
        self.write_records([])

    def group(self, name="G", program="vasp", **extra):
        g = {"name": name, "scheduler": "slurm", "host": "local", "prefix": os.path.join(self.remote, name),
             "local": "calc/" + name, "program": program,
             "records": {"file": "JOBS.csv", "id_column": "job_id", "outcome_column": "outcome",
                         "state_column": "state"}}
        g.update(extra)
        self.groups.append(g)
        with open(os.path.join(self.root, "monitor.json"), "w") as handle:
            json.dump({"name": "t", "groups": self.groups}, handle)
        return g

    def config(self):
        return json.load(open(os.path.join(self.root, "monitor.json")))

    def write_records(self, ids, outcome="submitted"):
        with open(self.records, "w", newline="") as handle:
            handle.write("job_id,state,outcome\n")
            for i in ids:
                handle.write("%s,submitted,%s\n" % (i, outcome))

    def record(self, job_id):
        with open(self.records, newline="") as handle:
            return [r for r in csv.DictReader(handle) if r["job_id"] == job_id][0]

    def job_dir(self, group, name, files=None):
        path = os.path.join(self.remote, group, name)
        os.makedirs(path, exist_ok=True)
        self.write(path, files or {})
        return path

    @staticmethod
    def write(path, files, age=0):
        for fname, text in files.items():
            target = os.path.join(path, fname)
            with open(target, "w") as handle:
                handle.write(text)
            if age:
                t = time.time() - age
                os.utime(target, (t, t))

    def queued(self, job_id, path, state="RUNNING", reason="None", elapsed=5):
        self.queue = [j for j in self.queue if j["id"] != job_id]
        self.queue.append({"id": job_id, "state": state, "elapsed_min": elapsed, "left": "", "reason": reason,
                           "dir": path, "name": "job" + job_id})

    def ended(self, job_id, path, state="COMPLETED", start=None):
        self.queue = [j for j in self.queue if j["id"] != job_id]
        self.accounting.append({"id": job_id, "name": "job" + job_id, "state": state, "exit": "0:0",
                                "elapsed": "1:00:00", "start_epoch": start if start is not None else time.time() - 3600,
                                "end": "x", "dir": path})

    def run_round(self, *extra):
        def fake_probe(rnd, host, config):
            return {"ok": True, "now": time.time(), "queue": list(self.queue),
                    "accounting": list(self.accounting), "runs": self.runs}
        argv = ["monitor_round.py", "--config", os.path.join(self.root, "monitor.json")] + list(extra)
        buf = io.StringIO()
        with mock.patch.object(M.Round, "remote_probe", fake_probe), mock.patch.object(sys, "argv", argv), \
                redirect_stdout(buf):
            code = M.main()
        return code, buf.getvalue()

    def start(self, job_id="1", program="vasp", **extra):
        self.group(program=program, **extra)
        self.write_records([job_id])
        path = self.job_dir("G", "run" + job_id)
        self.queued(job_id, path)
        code, text = self.run_round()
        self.assertEqual(code, 0, text)
        return path

    def finish(self, job_id, path, files, state="COMPLETED"):
        self.write(path, files)
        self.ended(job_id, path, state)
        return self.run_round()

    @staticmethod
    def report(text):
        return text.split("report ")[1].split(";")[0]

    @staticmethod
    def items(text):
        return [l for l in text.splitlines() if l.startswith("- ")]


class Vasp(Project):
    def test_normal_relaxation_is_handled_by_the_script(self):
        path = self.start()
        code, text = self.finish("1", path, VASP_RELAX_OK)
        self.assertEqual(self.items(text), ["- [G] group G has ended (1 normal)"])
        local = os.path.join(self.root, "calc/G/run1")
        self.assertTrue(os.path.isfile(os.path.join(local, "OUTCAR")))
        self.assertFalse(os.path.exists(os.path.join(local, "WAVECAR")))
        self.assertEqual(json.load(open(os.path.join(local, "MONITOR_CHECK.json")))["outcome"], "completed")
        self.assertEqual(self.record("1")["outcome"], "completed")

    def test_record_start_and_line_endings_kept(self):
        self.start()
        self.assertEqual(self.record("1")["state"], "running")
        self.assertNotIn(b"\r\n", open(self.records, "rb").read())

    def test_timeout(self):
        path = self.start()
        code, text = self.finish("1", path, dict(VASP_RELAX_OK, OUTCAR=VASP_END), state="TIMEOUT")
        self.assertIn("TIMEOUT", text)
        self.assertIn("not converged", text)
        self.assertEqual(self.record("1")["outcome"], "timeout")

    def test_imaginary_modes_counted_once(self):
        path = self.start()
        mode = "   6 f/i=  1.2 THz   7.5 2PiTHz   40.0 cm-1\n"
        files = {"INCAR": "IBRION = 5\nNELM = 100\n", "OSZICAR": oszicar(13), "OUTCAR": mode + mode + VASP_END}
        text = self.finish("1", path, files)[1]
        self.assertIn("1 imaginary mode", text)
        self.assertEqual(self.record("1")["outcome"], "needs_review")

    def test_stale_outcar_is_not_a_result(self):  # review item 4
        path = self.start()
        self.write(path, VASP_RELAX_OK, age=3 * 86400)
        self.ended("1", path, start=time.time() - 3600)
        text = self.run_round()[1]
        self.assertIn("no OUTCAR written during this job", text)
        self.assertNotEqual(self.record("1")["outcome"], "completed")

    def test_forbid_searches_job_stdout(self):  # review item 5
        path = self.start(forbid=["srun: error"])
        text = self.finish("1", path, dict(VASP_RELAX_OK, **{"slurm-1.out": "srun: error: node1: Killed\n"}))[1]
        self.assertIn("forbidden pattern srun: error in slurm-1.out", text)

    def test_running_health_flag(self):
        self.start()
        self.runs = {"1": {"flags": [{"sig": "nan:OSZICAR", "msg": "NaN or overflow in OSZICAR"}]}}
        code, text = self.run_round()
        self.assertEqual(code, 1)
        self.assertIn("NaN", text)

    def test_probe_error_not_silenced_by_ack(self):  # review item 6b
        self.start()
        self.runs = {"1": {"flags": [{"sig": "probe_error", "msg": "probe failed"}]}}
        code, text = self.run_round()
        self.run_round("--ack", self.report(text))
        self.assertEqual(self.run_round()[0], 1)

    def test_requeue_is_reported(self):  # review item 6a
        path = self.start()
        self.queued("1", path, state="PENDING", reason="BeginTime")
        code, text = self.run_round()
        self.assertEqual(code, 1)
        self.assertIn("was RUNNING, now PENDING", text)

    def test_job_still_pending_in_accounting_is_not_ended(self):  # review item 1
        path = self.start()
        self.accounting.append({"id": "2", "name": "job2", "state": "PENDING", "exit": "0:0", "elapsed": "0:00",
                                "start_epoch": None, "end": "Unknown", "dir": os.path.join(self.remote, "G", "run2")})
        code, text = self.run_round()
        self.assertEqual(code, 0, text)
        self.accounting.pop()
        self.queued("2", self.job_dir("G", "run2"))
        self.run_round()
        self.ended("2", os.path.join(self.remote, "G", "run2"), state="FAILED")
        self.assertIn("FAILED", self.run_round()[1])

    def test_job_missing_from_record_table(self):
        path = self.start()
        self.write_records([])
        self.assertIn("not in the record table", self.finish("1", path, VASP_RELAX_OK)[1])

    def test_project_result_check_untouched(self):
        path = self.start()
        local = os.path.join(self.root, "calc/G/run1")
        os.makedirs(local, exist_ok=True)
        with open(os.path.join(local, "RESULT_CHECK.json"), "w") as handle:
            handle.write('{"outcome": "converged", "adsorbate_intact": true}')
        self.finish("1", path, VASP_RELAX_OK)
        self.assertEqual(json.load(open(os.path.join(local, "RESULT_CHECK.json")))["outcome"], "converged")
        self.assertTrue(os.path.isfile(os.path.join(local, "MONITOR_CHECK.json")))

    def test_check_every_end_sends_normal_end_to_claude(self):
        path = self.start(check_every_end=True)
        self.group(name="H")  # keep the round from ending the only group
        self.queued("9", self.job_dir("H", "run9"))
        self.write_records(["1", "9"])
        text = self.finish("1", path, VASP_RELAX_OK)[1]
        self.assertIn("project checks still due", text)
        self.assertEqual(self.record("1")["outcome"], "completed")

    def test_local_files_kept_before_copy(self):  # review item 11
        path = self.start()
        local = os.path.join(self.root, "calc/G/run1")
        os.makedirs(local, exist_ok=True)
        with open(os.path.join(local, "INCAR"), "w") as handle:
            handle.write("my next-stage INCAR\n")
        self.finish("1", path, VASP_RELAX_OK)
        self.assertEqual(open(os.path.join(local, "INCAR.before_copy")).read(), "my next-stage INCAR\n")

    def test_large_files_stay_on_cluster_except_checked_ones(self):
        path = self.start(max_copy_mb=0.01)  # about 10 KB
        files = dict(VASP_RELAX_OK, **{"vasprun.xml": "x" * 20000, "slurm-1.out": "y" * 20000, "vasp.err": "small\n"})
        files["OUTCAR"] = "z" * 20000 + "\n" + files["OUTCAR"]
        self.finish("1", path, files)
        self.assertEqual(self.record("1")["outcome"], "completed")
        local = os.path.join(self.root, "calc/G/run1")
        self.assertEqual(sorted(os.listdir(local)), ["INCAR", "MONITOR_CHECK.json", "OSZICAR", "OUTCAR", "vasp.err"])

    def test_size_limit_and_exemptions_set_by_project(self):
        path = self.start(max_copy_mb=0.01, copy_any_size=["OUTCAR", "OSZICAR", "slurm-*"])
        self.finish("1", path, dict(VASP_RELAX_OK, **{"slurm-1.out": "y" * 20000}))
        self.assertTrue(os.path.exists(os.path.join(self.root, "calc/G/run1/slurm-1.out")))
        path = self.job_dir("H", "run2")
        self.group(name="H", max_copy_mb=0, copy=["*"])
        self.write_records(["1", "2"], outcome="")
        self.queued("2", path)
        self.run_round()
        self.finish("2", path, dict(VASP_RELAX_OK, **{"slurm-2.out": "y" * 20000}))
        self.assertTrue(os.path.exists(os.path.join(self.root, "calc/H/run2/WAVECAR")))

    def test_large_text_outputs_arrive_compressed_and_are_checked(self):
        path = self.start(max_copy_mb=0.01, compress_mb=0.01)  # about 10 KB
        files = dict(VASP_RELAX_OK, **{"vasprun.xml": "<v>" * 7000, "slurm-1.out": "y" * 20000})
        files["OUTCAR"] = "z" * 20000 + "\n" + files["OUTCAR"]
        self.finish("1", path, files)
        self.assertEqual(self.record("1")["outcome"], "completed")
        local = os.path.join(self.root, "calc/G/run1")
        self.assertEqual(sorted(os.listdir(local)),
                         ["INCAR", "MONITOR_CHECK.json", "OSZICAR", "OUTCAR.gz", "vasprun.xml.gz"])
        with gzip.open(os.path.join(local, "vasprun.xml.gz"), "rt") as handle:
            self.assertEqual(handle.read(), files["vasprun.xml"])
        self.assertAlmostEqual(os.path.getmtime(os.path.join(local, "OUTCAR.gz")),
                               os.path.getmtime(os.path.join(path, "OUTCAR")), delta=1)
        check = json.load(open(os.path.join(local, "MONITOR_CHECK.json")))
        self.assertEqual(check["facts"]["left_on_cluster"], ["slurm-1.out (0 MB)"])
        self.assertFalse(os.path.exists(os.path.join(path, "OUTCAR.gz")))  # nothing written on the cluster

    def test_compressed_outcar_without_normal_end_is_attention(self):
        path = self.start(compress_mb=0.01)
        files = dict(VASP_RELAX_OK, OUTCAR="z" * 20000 + "\nreached required accuracy\n")
        text = self.finish("1", path, files)[1]
        self.assertIn("no normal-end timing section", text)

    def test_record_row_with_extra_column_refused(self):
        path = self.start()
        with open(self.records, "a") as handle:
            handle.write("7,submitted,submitted,note with, a comma\n")
        before = open(self.records).read()
        text = self.finish("1", path, VASP_RELAX_OK)[1]
        self.assertIn("different number of columns", text)
        self.assertEqual(open(self.records).read(), before)

    def test_completing_is_not_attention(self):
        path = self.start()
        self.queued("1", path, state="COMPLETING")
        self.assertEqual(self.run_round()[0], 0)

    def test_restart_seen_through_configuring(self):
        path = self.start()
        self.queued("1", path, elapsed=300)
        self.assertEqual(self.run_round()[0], 0)
        self.queued("1", path, state="CONFIGURING", elapsed=0)  # node failure, requeued
        self.assertEqual(self.run_round()[0], 0)
        self.queued("1", path, elapsed=10)
        code, text = self.run_round()
        self.assertEqual(code, 1)
        self.assertIn("run time", text)

    def test_record_table_changed_meanwhile_is_not_overwritten(self):  # review item 10
        path = self.start()
        original = M.Round.record_rows

        def appended(rnd, group):
            result = original(rnd, group)
            with open(rnd.path(group["records"]["file"]), "a") as handle:
                handle.write("99,submitted,submitted\n")
            return result
        with mock.patch.object(M.Round, "record_rows", appended):
            text = self.finish("1", path, VASP_RELAX_OK)[1]
        self.assertIn("changed while it was being updated", text)
        self.assertIn("99,submitted", open(self.records).read())


class OrcaCp2k(Project):
    ORCA_HEAD = "                                 * O   R   C   A *\n"

    def test_orca_converged_optimisation(self):
        path = self.start(program="orca")
        out = self.ORCA_HEAD + ("GEOMETRY OPTIMIZATION CYCLE   1\nTHE OPTIMIZATION HAS CONVERGED\n"
                                "FINAL SINGLE POINT ENERGY     -100.123\n****ORCA TERMINATED NORMALLY****\n")
        self.finish("1", path, {"opt.out": out})
        self.assertEqual(self.record("1")["outcome"], "completed")

    def test_orca_large_output_compressed(self):
        path = self.start(program="orca", compress_mb=0.01)
        out = self.ORCA_HEAD + "x" * 20000 + ("\nFINAL SINGLE POINT ENERGY     -100.123\n****ORCA TERMINATED NORMALLY****\n")
        self.finish("1", path, {"sp.out": out})
        self.assertEqual(self.record("1")["outcome"], "completed")
        self.assertTrue(os.path.exists(os.path.join(self.root, "calc/G/run1/sp.out.gz")))

    def test_orca_scf_not_converged_but_terminated_normally(self):  # review item 7
        path = self.start(program="orca")
        out = self.ORCA_HEAD + "SCF NOT CONVERGED AFTER 125 CYCLES\n****ORCA TERMINATED NORMALLY****\n"
        self.assertIn("SCF NOT CONVERGED", self.finish("1", path, {"sp.out": out})[1])

    def test_orca_not_fully_converged_any_case(self):
        path = self.start(program="orca")
        out = self.ORCA_HEAD + "Warning: the SCF is not fully converged\n****ORCA TERMINATED NORMALLY****\n"
        self.assertIn("not fully converged", self.finish("1", path, {"sp.out": out})[1])

    def test_orca_intermediate_cycles_are_not_a_problem(self):
        path = self.start(program="orca")
        out = self.ORCA_HEAD + ("GEOMETRY OPTIMIZATION CYCLE   1\n"
                                "The optimization has not yet converged - more geometry cycles are needed\n"
                                "THE OPTIMIZATION HAS CONVERGED\n****ORCA TERMINATED NORMALLY****\n")
        self.finish("1", path, {"opt.out": out})
        self.assertEqual(self.record("1")["outcome"], "completed")

    def test_orca_abnormal_end_and_imaginary_mode(self):
        path = self.start(program="orca")
        out = self.ORCA_HEAD + "VIBRATIONAL FREQUENCIES\n   6:    -35.12 cm**-1 ***imaginary mode***\n"
        text = self.finish("1", path, {"freq.out": out})[1]
        self.assertIn("ORCA TERMINATED NORMALLY", text)
        self.assertIn("imaginary frequencies -35.12", text)

    def test_orca_old_output_in_subfolder_ignored(self):  # review item 4
        path = self.start(program="orca")
        old = os.path.join(path, "previous")
        os.makedirs(old)
        self.write(old, {"x.out": self.ORCA_HEAD + "****ORCA TERMINATED NORMALLY****\n"})
        self.assertIn("no ORCA output written during this job", self.finish("1", path, {})[1])

    def test_cp2k_scf_not_converged_and_negative_frequency(self):  # review item 7
        path = self.start(program="cp2k")
        out = (" CP2K| version string\n *** SCF run NOT converged ***\n"
               " VIB|Frequency (cm^-1)     -20.5    100.0    200.0\n  PROGRAM ENDED AT 2026-10-21\n")
        text = self.finish("1", path, {"vib.out": out})[1]
        self.assertIn("SCF run NOT converged", text)
        self.assertIn("imaginary frequencies -20.5", text)


class Lifecycle(Project):
    def test_first_round_reports_failure_before_it_but_skips_recorded_results(self):  # review item 3
        self.group()
        with open(self.records, "w") as handle:
            handle.write("job_id,state,outcome\n1,done,completed\n2,submitted,submitted\n3,submitted,submitted\n")
        self.queued("3", self.job_dir("G", "run3"))
        self.ended("1", self.job_dir("G", "run1", VASP_RELAX_OK))
        self.ended("2", self.job_dir("G", "run2", {"INCAR": "IBRION = 3\nNSW = 5\n"}), state="FAILED")
        code, text = self.run_round()
        self.assertEqual(code, 1)
        self.assertIn("job2", text)
        self.assertNotIn("job1", text)
        self.assertEqual(self.record("2")["outcome"], "failed")

    def test_first_round_skips_job_with_existing_result_check(self):
        self.group()
        self.write_records(["1", "3"])
        self.queued("3", self.job_dir("G", "run3"))
        self.ended("1", self.job_dir("G", "run1", dict(VASP_RELAX_OK, OUTCAR=VASP_END)), state="TIMEOUT")
        local = os.path.join(self.root, "calc/G/run1")
        os.makedirs(local)
        with open(os.path.join(local, "RESULT_CHECK_freq.txt"), "w") as handle:
            handle.write("handled by the previous monitor\n")
        code, text = self.run_round()
        self.assertEqual(code, 0, text)

    def test_resubmission_after_ack_keeps_the_group(self):  # review item 2
        path = self.start()
        code, text = self.finish("1", path, dict(VASP_RELAX_OK, OUTCAR=VASP_END), state="TIMEOUT")
        self.assertIn("group G has ended", text)
        self.queued("3", self.job_dir("G", "run3"))  # Claude resubmits, then acknowledges
        self.write_records(["1", "3"])
        self.assertEqual(self.run_round("--ack", self.report(self.run_round()[1]))[0], 0)
        self.assertEqual([g["name"] for g in self.config()["groups"]], ["G"])
        self.ended("3", os.path.join(self.remote, "G", "run3"), state="FAILED")
        code, text = self.run_round()
        self.assertEqual(code, 1)
        self.assertIn("FAILED", text)

    def test_group_leaves_after_end_acknowledged_and_still_empty(self):
        path = self.start()
        code, text = self.finish("1", path, VASP_RELAX_OK)
        self.assertEqual(self.run_round("--ack", self.report(text))[0], 0)
        self.assertEqual([g["name"] for g in self.config()["groups"]], ["G"])
        code, text = self.run_round()
        self.assertEqual(code, 3)
        self.assertIn("NO_JOBS (first)", text)
        self.assertEqual(self.config()["finished_groups"][0]["name"], "G")
        code, text = self.run_round()
        self.assertEqual(code, 3)
        self.assertNotIn("first", text)

    def test_prefix_never_matched_is_attention(self):  # review item 8
        self.group()
        for _ in range(2):
            code, text = self.run_round()
            self.assertEqual(code, 1)
            self.assertIn("no job has ever matched prefix", text)
        self.assertEqual(len(self.config()["groups"]), 1)

    def test_other_group_keeps_running(self):
        path = self.start()
        self.group(name="H")
        self.queued("9", self.job_dir("H", "run9"))
        self.write_records(["1", "9"])
        code, text = self.finish("1", path, VASP_RELAX_OK)
        self.run_round("--ack", self.report(text))
        self.assertEqual(self.run_round()[0], 0)
        self.assertEqual([g["name"] for g in self.config()["groups"]], ["H"])

    def test_items_repeat_until_acknowledged_and_old_report_refused(self):
        path = self.start()
        code, text = self.finish("1", path, dict(VASP_RELAX_OK, OUTCAR=VASP_END), state="TIMEOUT")
        first = self.report(text)
        time.sleep(0.01)
        code, text = self.run_round()
        self.assertIn("TIMEOUT", text)
        latest = self.report(text)
        if first != latest:
            self.assertEqual(self.run_round("--ack", first)[0], 2)
        self.assertEqual(self.run_round("--ack", latest)[0], 0)

    def test_check_failure(self):
        self.start()

        def broken(rnd, host, config):
            raise RuntimeError("ssh exit 255")
        buf = io.StringIO()
        with mock.patch.object(M.Round, "remote_probe", broken), \
                mock.patch.object(sys, "argv", ["m", "--config", os.path.join(self.root, "monitor.json")]), \
                redirect_stdout(buf):
            self.assertEqual(M.main(), 2)
        self.assertIn("FAILED", buf.getvalue())


class RemoteListing(unittest.TestCase):
    def test_find_output_parsed(self):
        rnd = M.Round.__new__(M.Round)
        out = "1234\t1700000000.5\tOUTCAR\n99\t1700000001.0\tsub dir/vasprun.xml\n"
        with mock.patch.object(M.Round, "shell", return_value=mock.Mock(returncode=0, stdout=out, stderr="")) as sh:
            rows = rnd.remote_files({"host": "u@h"}, "/scratch/a b")
        self.assertEqual(rows, [("OUTCAR", 1234, 1700000000.5), ("sub dir/vasprun.xml", 99, 1700000001.0)])
        self.assertIn("find '/scratch/a b' -type f -printf '%s\\t%T@\\t%P\\n'", sh.call_args[0][1])


class PbsProbe(unittest.TestCase):
    def test_qstat_parsing(self):
        listing = "Job id  Name\n----\n101.pbs-server  a\n102.pbs-server  b\n103.pbs-server  c\n"
        jobs = {"Jobs": {
            "101.pbs-server": {"job_state": "R", "Job_Name": "a", "resources_used": {"walltime": "01:00:00"},
                             "Variable_List": {"PBS_O_WORKDIR": "/proj/a"}},
            "102.pbs-server": {"job_state": "F", "Job_Name": "b", "Exit_status": 271, "obittime": "Wed Oct 21 09:50:14 2026",
                             "stime": "Tue Oct 20 21:50:09 2026", "resources_used": {"walltime": "12:00:05"},
                             "Resource_List": {"walltime": "12:00:00"}, "Variable_List": {"PBS_O_WORKDIR": "/proj/b"}},
            "103.pbs-server": {"job_state": "Q", "Job_Name": "c", "Variable_List": {"PBS_O_WORKDIR": "/other/c"}}}}

        def fake_run(cmd, env=None):
            return listing if "-f" not in cmd else json.dumps(jobs)
        with mock.patch.object(P, "run", fake_run):
            now, queue, accounting = P.probe_pbs({"since_epoch": 0}, "u", ["/proj"])
        self.assertEqual([(j["id"], j["state"]) for j in queue], [("101", "RUNNING")])
        self.assertEqual([(j["id"], j["state"]) for j in accounting], [("102", "TIMEOUT")])
        self.assertIsNotNone(accounting[0]["start_epoch"])


if __name__ == "__main__":
    unittest.main()
