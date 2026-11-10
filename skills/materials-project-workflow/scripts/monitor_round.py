#!/usr/bin/env python3
"""One monitoring round for a project's HPC jobs, run first by the scheduled monitor.

The script does every routine step itself and hands the agent only what it cannot
confirm as normal. It never submits, cancels or changes a job.

    monitor_round.py --config monitor.json             # one round
    monitor_round.py --config monitor.json --ack R     # after the agent has handled report R

Exit codes:
    0  QUIET      everything was normal and is already handled: end the round.
    1  ATTENTION  report R lists items with their files: analyse, report, then --ack R.
                  Items are listed again every round until acknowledged.
    2  FAILED     the check itself failed (login, network, config); not a job failure.
    3  NO_JOBS    no active group is left. The first NO_JOBS says so ("first"): tell the
                  main session once that the monitor can be retired; later ones just end.

Routine, done by the script: a job queued or starting (the start goes into the record
table's state column if one is named); an ended job: its listed files are copied back
(local files that would be replaced are kept as *.before_copy), MONITOR_CHECK.json is
written beside them (a project's own RESULT_CHECK files are never touched) and the record
table's outcome is set. An ended job is normal only when the
scheduler reports success, its main output was written after the job started, and the
program checks pass: VASP normal end, convergence of a relaxation, no electronic step at
NELM where it matters, no imaginary mode; ORCA and CP2K normal end, converged SCF and
optimisation, no imaginary frequency; plus the group's own `require` and `forbid`
regular expressions, searched in every copied file written during the job.
Everything else is ATTENTION, including the end of a whole group. A group with
`"check_every_end": true` sends every ended job to the agent after the routine steps, for
project checks a script cannot make (adsorbate integrity, project result files). A group leaves the
active list only after its end was acknowledged and its prefix still has no job; a
group that has never matched a job is ATTENTION (check its prefix). Running jobs get
health checks for VASP only; a stuck ORCA or CP2K job shows up when it ends. On the
first round, an ended job is taken as handled when its record row already has a final
outcome or its local folder already holds a RESULT_CHECK* or MONITOR_CHECK.json file;
without either, earlier COMPLETED jobs are skipped and others reported.

monitor.json (paths relative to its folder, the project root; state and log go to
.monitor/<name>/ beside it):

    {"name": "myproject", "groups": [
      {"name": "C", "scheduler": "slurm", "host": "user@login.example.org",
       "prefix": "/scratch/PROJECT/USER/myproject/calculations/dft",
       "local": "calculations/dft", "program": "vasp",
       "records": {"file": "calculations/dft/JOBS.csv", "id_column": "job_id",
                   "outcome_column": "outcome", "state_column": ""},
       "copy": [], "compress": [], "compress_mb": 5, "max_copy_mb": 20, "copy_any_size": [],
       "require": [], "forbid": [], "check_every_end": false}]}

`scheduler` is slurm or pbs; `program` is vasp, orca or cp2k; an empty `copy` uses the
program's default list (VASP's leaves out WAVECAR and CHGCAR); a job's remote folder below
`prefix` maps to the same folder below `local`. A listed file above `compress_mb` (default
5; 0 never compresses) that matches `compress` (default: the program's large text outputs,
VASP OUTCAR, vasprun.xml, XDATCAR, PROCAR, DOSCAR and EIGENVAL) arrives as <name>.gz,
compressed while it is copied, so nothing is written on the cluster; the checks read it.
Any other listed file above `max_copy_mb` (default 20; 0 means no limit) stays on the
cluster and is named in MONITOR_CHECK.json, unless it matches `copy_any_size`, which
defaults to the files the checks read (VASP OUTCAR and OSZICAR, ORCA *.out, CP2K *.out
and *.log).
"""
import argparse
import base64
import csv
import datetime
import fcntl
import fnmatch
import glob
import gzip
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "hpc_job_probe.py")
sys.path.insert(0, HERE)
import check_hpc_jobs as Q  # noqa: E402  the queue comparison shared with the quiet check
import hpc_job_probe as P  # noqa: E402

DEFAULT_COPY = {
    "vasp": ["INCAR", "KPOINTS", "POSCAR", "CONTCAR", "OSZICAR", "OUTCAR", "vasprun.xml", "*.out", "*.err",
             "slurm-*", "*.o[0-9]*", "*.e[0-9]*"],
    "orca": ["*.inp", "*.out", "*.xyz", "*.hess", "*.o[0-9]*", "*.e[0-9]*", "slurm-*"],
    "cp2k": ["*.inp", "*.out", "*.log", "*-pos-*.xyz", "*.o[0-9]*", "*.e[0-9]*", "slurm-*"],
}
CHECK_FILES = {"vasp": ["OUTCAR", "OSZICAR"], "orca": ["*.out"], "cp2k": ["*.out", "*.log"]}
COMPRESS = {"vasp": ["OUTCAR", "vasprun.xml", "XDATCAR", "PROCAR", "DOSCAR", "EIGENVAL"],
            "orca": ["*.out", "*.xyz", "*.hess"], "cp2k": ["*.out", "*.log", "*.xyz"]}
MAX_COPY_MB, COMPRESS_MB = 20, 5
VASP_END = "General timing and accounting informations for this job"
VASP_RELAXED = "reached required accuracy"
ORCA_END = "****ORCA TERMINATED NORMALLY****"
# Any case. Not "not yet converged": every unfinished optimisation cycle prints that line.
ORCA_BAD = [r"SCF NOT CONVERGED", r"NOT FULLY CONVERGED", r"finished by error termination"]
CP2K_END = "PROGRAM ENDED AT"
CP2K_BAD = ["SCF run NOT converged"]
FINAL_OUTCOMES_SKIP = {"", "submitted", "running", "pending", "queued"}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ControlMaster=no", "-o", "ControlPath=none"]
MAX_TEXT = 200_000_000


def load_json(path, default=None):
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def read(path):
    """Text of a file, a .gz one decompressed; empty for an unreadable or oversized file."""
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", errors="replace") as handle:
                return handle.read(MAX_TEXT)
        if os.path.getsize(path) > MAX_TEXT:
            return ""
        with open(path, errors="replace") as handle:
            return handle.read()
    except (OSError, EOFError):
        return ""


def matches(name, patterns):
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def stamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


class Round:
    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        self.root = os.path.dirname(self.config_path)
        self.cfg = load_json(self.config_path)
        if not self.cfg or not self.cfg.get("name"):
            raise RuntimeError("no monitor config with a name at %s" % config_path)
        self.dir = os.path.join(self.root, ".monitor", self.cfg["name"])
        os.makedirs(self.dir, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.pending_path = os.path.join(self.dir, "attention.json")
        self.state = load_json(self.state_path, {"groups": {}, "handled": {}})

    def path(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.root, rel)

    def rel(self, path):
        return os.path.relpath(path, self.root)

    def log(self, text):
        with open(os.path.join(self.dir, "monitor.log"), "a") as handle:
            handle.write("%s %s\n" % (stamp(), text))

    # ------------------------------------------------------------ probing and copying

    def probe(self, groups):
        """Return {group name: probe result}; one ssh call per (host, scheduler)."""
        by_host = {}
        for group in groups:
            by_host.setdefault((group["host"], group.get("scheduler", "slurm")), []).append(group)
        times = [g.get("checked_at") for g in self.state["groups"].values() if g.get("checked_at")]
        times += [j["seen"] for g in self.state["groups"].values() for j in g.get("jobs", {}).values() if j.get("seen")]
        since = (min(times) if times else time.time() - 48 * 3600) - 1800
        results = {}
        for (host, scheduler), members in by_host.items():
            config = {"prefixes": [g["prefix"] for g in members], "since_epoch": since, "scheduler": scheduler,
                      "health_prefixes": [g["prefix"] for g in members if g.get("program") == "vasp"]}
            result = self.remote_probe(host, config)
            for group in members:
                def inside(job, g=group):
                    return P.in_prefixes(job["dir"], [g["prefix"]])
                queue = [j for j in result["queue"] if inside(j)]
                results[group["name"]] = {"now": result["now"], "queue": queue,
                                          "accounting": [j for j in result["accounting"] if inside(j)],
                                          "runs": {k: v for k, v in result["runs"].items()
                                                   if k in {j["id"] for j in queue}}}
        return results

    def remote_probe(self, host, config):
        encoded = base64.b64encode(json.dumps(config).encode()).decode()
        if host == "local":
            done = subprocess.run([sys.executable, PROBE, encoded], capture_output=True, text=True, timeout=300)
        else:
            with open(PROBE) as source:
                done = subprocess.run(SSH + [host, "python3", "-", encoded], stdin=source,
                                      capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise RuntimeError("ssh %s exit %d: %s" % (host, done.returncode, done.stderr.strip()[-300:]))
        try:
            result = json.loads(done.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise RuntimeError("probe output from %s is not JSON" % host)
        if not result.get("ok"):
            raise RuntimeError("probe on %s: %s" % (host, result.get("error")))
        return result

    def local_dir(self, group, remote_dir):
        rel = os.path.relpath(remote_dir, group["prefix"])
        if rel.startswith(".."):
            raise ValueError("%s is outside %s" % (remote_dir, group["prefix"]))
        return os.path.normpath(os.path.join(self.path(group["local"]), rel))

    def shell(self, group, command, **kw):
        cmd = ["sh", "-c", command] if group["host"] == "local" else SSH + [group["host"], command]
        return subprocess.run(cmd, timeout=1800, **kw)

    def remote_files(self, group, remote_dir):
        """(relative path, bytes, mtime) of every file below the job folder."""
        if group["host"] == "local":
            return [(os.path.relpath(os.path.join(d, n), remote_dir), st.st_size, st.st_mtime)
                    for d, _, names in os.walk(remote_dir) for n in names
                    for st in [os.stat(os.path.join(d, n))]]
        done = self.shell(group, "find %s -type f -printf '%%s\\t%%T@\\t%%P\\n'" % shlex.quote(remote_dir),
                          capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError("listing failed (exit %d): %s" % (done.returncode, done.stderr.strip()[-300:]))
        rows = [line.split("\t", 2) for line in done.stdout.splitlines() if line.count("\t") >= 2]
        return [(rel, int(size), float(mtime)) for size, mtime, rel in rows]

    def copy_back(self, group, remote_dir, local_dir):
        """Copy an ended job's listed files; large text files arrive gzipped. Return the files left behind."""
        program = group.get("program")
        patterns = group.get("copy") or DEFAULT_COPY.get(program, ["*"])
        any_size = group.get("copy_any_size") or CHECK_FILES.get(program, [])
        squeeze = group.get("compress") or COMPRESS.get(program, [])
        limit, small = group.get("max_copy_mb", MAX_COPY_MB), group.get("compress_mb", COMPRESS_MB)
        packed, left = [], []
        for rel, size, mtime in self.remote_files(group, remote_dir):
            name, mb = os.path.basename(rel), size / 1048576.0
            if not (matches(name, patterns) or matches(name, any_size)):
                continue
            if small and mb > small and matches(name, squeeze):
                packed.append((rel, mtime))
            elif limit and mb > limit and not matches(name, any_size):
                left.append("%s (%.0f MB)" % (rel, mb))
        os.makedirs(local_dir, exist_ok=True)
        source = remote_dir + "/" if group["host"] == "local" else "%s:%s/" % (group["host"], remote_dir)
        skip = [rel for rel, _ in packed] + [item.rsplit(" (", 1)[0] for item in left]
        cmd = (["rsync", "-a", "--prune-empty-dirs", "--backup", "--suffix=.before_copy", "-e", " ".join(SSH)]
               + ["--exclude=/%s" % rel for rel in skip] + ["--include=*/"]
               + ["--include=%s" % p for p in patterns + any_size] + ["--exclude=*", source, local_dir + "/"])
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if done.returncode != 0:
            raise RuntimeError("rsync exit %d: %s" % (done.returncode, done.stderr.strip()[-300:]))
        for rel, mtime in packed:  # compressed on the fly; nothing is written on the cluster
            target = os.path.join(local_dir, rel + ".gz")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target + ".part", "wb") as out:
                done = self.shell(group, "gzip -c -- %s" % shlex.quote(os.path.join(remote_dir, rel)),
                                  stdout=out, stderr=subprocess.PIPE)
            try:
                if done.returncode != 0:
                    raise RuntimeError("gzip of %s failed: %s" % (rel, done.stderr.decode(errors="replace")[-300:]))
                with gzip.open(target + ".part") as check:  # reading to the end verifies the CRC
                    while check.read(1 << 22):
                        pass
            except Exception:
                os.remove(target + ".part")
                raise
            if os.path.exists(target):
                os.replace(target, target + ".before_copy")
            os.replace(target + ".part", target)
            os.utime(target, (mtime, mtime))
        return left

    def check_outputs(self, group, local_dir, start):
        """Return (problems, facts, files) for one ended job's copied outputs."""
        if start is None:
            return ["job start time unknown, so outputs cannot be told apart from older ones"], {}, []
        fresh = [f for f in glob.glob(os.path.join(local_dir, "**", "*"), recursive=True)
                 if os.path.isfile(f) and not f.endswith(".before_copy") and os.path.getmtime(f) >= start - 60]
        checker = {"vasp": check_vasp, "orca": check_orca, "cp2k": check_cp2k}.get(group.get("program"))
        if checker is None:
            return ["unknown program %r" % group.get("program")], {}, []
        problems, facts, files = checker(local_dir, set(fresh))
        require, forbid = group.get("require", []), group.get("forbid", [])
        found, hits_by = set(), {pattern: [] for pattern in forbid}
        for f in fresh if require or forbid else []:  # one file in memory at a time
            text = read(f)
            found.update(pattern for pattern in require if re.search(pattern, text))
            for pattern in forbid:
                if re.search(pattern, text):
                    hits_by[pattern].append(os.path.basename(f))
        problems += ["required pattern not found: %s" % pattern for pattern in require if pattern not in found]
        for pattern in forbid:
            hits = sorted(hits_by[pattern])
            if hits:
                problems.append("forbidden pattern %s in %s" % (pattern, ", ".join(hits)))
                files += [f for f in fresh if os.path.basename(f) in hits and f not in files]
        return problems, facts, files

    # ------------------------------------------------------------ record table

    def record_rows(self, group):
        rec = group["records"]
        path = self.path(rec["file"])
        with open(path, newline="") as handle:
            raw = handle.read()
        rows = list(csv.DictReader(raw.splitlines()))
        return path, raw, rows

    def record_value(self, group, job_id, column=None):
        rec = group.get("records")
        if not rec:
            return None
        _, _, rows = self.record_rows(group)
        col = column or rec.get("outcome_column", "outcome")
        for row in rows:
            if (row.get(rec.get("id_column", "job_id")) or "").split(".")[0].strip() == job_id:
                return row.get(col) or ""
        return None

    def update_record(self, group, job_id, value, column=None):
        """Set one cell for job_id; False if the job is not listed. Refuses if the file changed meanwhile."""
        rec = group.get("records")
        if not rec:
            return True
        path, raw, rows = self.record_rows(group)
        widths = {len(r) for r in csv.reader(raw.splitlines()) if r}
        if len(widths) > 1:
            raise RuntimeError("%s has rows with a different number of columns than its header "
                               "(an unquoted comma?); not written" % rec["file"])
        id_col = rec.get("id_column", "job_id")
        col = column or rec.get("outcome_column", "outcome")
        fields = list(rows[0].keys()) if rows else next(csv.reader(raw.splitlines()), [])
        if col not in fields:
            raise RuntimeError("column %s not in %s" % (col, rec["file"]))
        hits = [r for r in rows if (r.get(id_col) or "").split(".")[0].strip() == job_id]
        if not hits:
            return False
        for row in hits:
            row[col] = value
        ending = "\r\n" if "\r\n" in raw else "\n"
        tmp = path + ".tmp"
        with open(tmp, "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator=ending)
            writer.writeheader()
            writer.writerows(rows)
        with open(path, newline="") as handle:
            if handle.read() != raw:
                os.remove(tmp)
                raise RuntimeError("%s changed while it was being updated; not written" % rec["file"])
        os.replace(tmp, path)
        return True

    # ------------------------------------------------------------ job handling

    def finish(self, group, job, first_round):
        """Routine steps for one ended job; return (normal, problems, files)."""
        if first_round:
            try:
                done = self.record_value(group, job["id"]) if group.get("records") else None
            except Exception:
                done = None  # processed below, where the record problem is reported
            try:
                checked = glob.glob(os.path.join(self.local_dir(group, job["dir"]), "RESULT_CHECK*")) + \
                    glob.glob(os.path.join(self.local_dir(group, job["dir"]), "MONITOR_CHECK.json"))
            except ValueError:
                checked = []
            if (done is not None and done.strip().lower() not in FINAL_OUTCOMES_SKIP) or checked:
                self.log("%s %s ended before the first round and was already handled (%s); not processed again"
                         % (job["id"], job["name"], done if done else os.path.basename(checked[0])))
                return True, [], []
            if not group.get("records") and job["state"].split()[0] == "COMPLETED":
                self.log("%s %s completed before the first round; not processed (no record table)" % (job["id"], job["name"]))
                return True, [], []
        files, problems, facts, local = [], [], {}, None
        try:
            local = self.local_dir(group, job["dir"])
            left = self.copy_back(group, job["dir"], local)
            problems, facts, files = self.check_outputs(group, local, job.get("start_epoch"))
            if left:
                facts["left_on_cluster"] = left
        except Exception as exc:
            problems = ["copy or check failed: %s" % exc]
        sched = job["state"].split()[0]
        if sched != "COMPLETED":
            problems.insert(0, "scheduler state %s (exit %s, %s)" % (job["state"], job.get("exit"), job.get("elapsed")))
        outcome = "completed" if not problems else {"TIMEOUT": "timeout", "CANCELLED": "cancelled",
                                                    "COMPLETED": "needs_review"}.get(sched, "failed")
        if local:
            target = os.path.join(local, "MONITOR_CHECK.json")
            if os.path.exists(target):
                os.replace(target, target + ".before_copy")
            save_json(target, {"job_id": job["id"], "name": job["name"], "remote_dir": job["dir"],
                               "scheduler_state": job["state"], "exit": job.get("exit"), "elapsed": job.get("elapsed"),
                               "program": group.get("program"), "outcome": outcome, "problems": problems,
                               "facts": facts, "checked": stamp(), "checked_by": "monitor_round.py"})
            files = [target] + files
        try:
            if not self.update_record(group, job["id"], outcome):
                problems.append("job not in the record table")
        except Exception as exc:
            problems.append("record table not updated: %s" % exc)
        if not problems:
            self.log("%s %s completed; outputs checked and recorded" % (job["id"], job["name"]))
            if group.get("check_every_end"):
                problems = ["passed the script's checks; project checks still due (check_every_end)"]
        return not problems, problems, [self.rel(f) for f in files]

    def handle_group(self, group, result, items):
        name = group["name"]
        gstate = self.state["groups"].get(name)
        first_round = gstate is None
        baseline = gstate or {}
        events, new_state = Q.compare(baseline, result)
        handled = self.state["handled"]
        routine = []
        ended = {j["id"]: j for j in result["accounting"]}
        passing = {j["id"] for j in result["queue"] if j["state"] in ("COMPLETING", "CONFIGURING")}
        for sig, msg in events:
            kind = sig.split(":")[0]
            if sig == "empty" or (kind in ("state", "requeued") and sig.split(":")[1] in passing):
                continue  # handled below / a passing scheduler state
            if kind == "started":
                routine.append(sig)
                column = (group.get("records") or {}).get("state_column")
                job_id = sig.split(":")[1]
                if column:
                    try:
                        if not self.update_record(group, job_id, "running", column):
                            items.append((name, "%s:unlisted" % job_id, "%s: not in the record table" % msg.split(":")[0], []))
                    except Exception as exc:
                        items.append((name, "%s:record" % job_id, "%s: record table not updated: %s" % (job_id, exc), []))
                continue
            if kind == "exit":
                job_id = sig.split(":")[1]
                key = "%s:%s" % (name, job_id)
                if key not in handled:
                    normal, problems, files = self.finish(group, ended[job_id], first_round)
                    handled[key] = {"normal": normal, "problems": problems, "files": files,
                                    "outcome": "ok" if normal else "attention"}
                h = handled[key]
                if h["normal"]:
                    routine.append(sig)
                else:
                    items.append((name, sig, "%s: %s" % (msg, "; ".join(h["problems"])), h["files"]))
                continue
            items.append((name, sig, msg, []))
        for job_id in passing & set(baseline.get("jobs", {})):
            # A passing state says nothing new: keep the last real one, so a restart through it is still seen.
            new_state["jobs"][job_id] = baseline["jobs"][job_id]
        new_state["acked"] = sorted(set(new_state["acked"]) | set(routine))
        new_state["ever_matched"] = baseline.get("ever_matched") or bool(result["queue"] or result["accounting"])
        self.state["groups"][name] = new_state
        if result["queue"]:
            # Work continues (perhaps resubmitted after an acknowledged end): report the next end again.
            new_state["acked"] = [s for s in new_state["acked"] if s != "group_end:%s" % name]
            return None
        mine = [k for k in handled if k.startswith(name + ":")]
        if not new_state["ever_matched"]:
            items.append((name, "empty", "group %s: no job has ever matched prefix %s; check the prefix and scheduler"
                          % (name, group["prefix"]), []))
            return None
        if any(j.get("state") == "VANISHED" for j in new_state["jobs"].values()):
            return None
        if "group_end:%s" % name in new_state["acked"]:
            return name  # end acknowledged and still no job: leave the active list
        counts = {}
        for k in mine:
            o = "normal" if handled[k]["normal"] else "attention"
            counts[o] = counts.get(o, 0) + 1
        items.append((name, "group_end:%s" % name, "group %s has ended (%s)" % (
            name, ", ".join("%d %s" % (n, o) for o, n in sorted(counts.items())) or "no ended job seen"), []))
        return None

    # ------------------------------------------------------------ the round

    def run(self, out):
        groups = self.cfg.get("groups", [])
        if not groups:
            first = not self.state.get("no_jobs_reported")
            self.state["no_jobs_reported"] = True
            save_json(self.state_path, self.state)
            out.append("NO_JOBS%s no active group in %s; the monitor can be retired"
                       % (" (first)" if first else "", self.rel(self.config_path)))
            return 3
        self.state["no_jobs_reported"] = False
        results = self.probe(groups)
        items, retire = [], []
        for group in groups:
            done = self.handle_group(group, results[group["name"]], items)
            if done:
                retire.append(done)
        pending = load_json(self.pending_path, {"items": []})
        seen = {(i["group"], i["key"]) for i in pending["items"]}
        merged = [i for i in pending["items"]] + [
            {"group": g, "key": k, "text": t, "files": f} for g, k, t, f in items if (g, k) not in seen]
        # Refresh text and files of items found again this round.
        latest = {(g, k): (t, f) for g, k, t, f in items}
        for item in merged:
            if (item["group"], item["key"]) in latest:
                item["text"], item["files"] = latest[(item["group"], item["key"])]
        if merged:
            digest = hashlib.sha256(json.dumps([time.time(), merged]).encode()).hexdigest()[:6]
            report = "%s-%s" % (datetime.datetime.now().strftime("%Y%m%dT%H%M"), digest)
            save_json(self.pending_path, {"report": report, "items": merged})  # before state, so nothing is lost
        save_json(self.state_path, self.state)
        if retire:
            self.retire(retire)
        if not merged:
            if not self.cfg.get("groups"):
                return self.run(out)
            running = sum(len(results[g["name"]]["queue"]) for g in groups)
            out.append("QUIET %d job(s) queued or running in %d group(s); routine steps done; %s"
                       % (running, len(self.cfg["groups"]), stamp()))
            return 0
        out.append("ATTENTION %d item(s), report %s; after handling them run --ack %s" % (len(merged), report, report))
        for item in merged:
            out.append("- [%s] %s" % (item["group"], item["text"]))
            out.extend("    file: " + f for f in item["files"])
        return 1

    def ack(self, report, out):
        pending = load_json(self.pending_path)
        if pending is None:
            out.append("nothing to acknowledge")
            return 0
        if pending["report"] != report:
            out.append("ack refused: the latest report is %s, not %s; read its output first" % (pending["report"], report))
            return 2
        for item in pending["items"]:
            g = self.state["groups"].setdefault(item["group"], {})
            if Q.ackable(item["key"]):
                g["acked"] = sorted(set(g.get("acked", [])) | {item["key"]})[-2000:]
        save_json(self.state_path, self.state)
        os.remove(self.pending_path)
        out.append("acknowledged report %s" % report)
        return 0

    def retire(self, names):
        cfg = load_json(self.config_path)
        names = set(names)
        done = cfg.setdefault("finished_groups", [])
        done += [dict(g, finished=stamp()) for g in cfg.get("groups", []) if g["name"] in names]
        cfg["groups"] = [g for g in cfg.get("groups", []) if g["name"] not in names]
        save_json(self.config_path, cfg)
        self.cfg = cfg
        self.log("groups %s left the active list (ended, acknowledged, no job left)" % ", ".join(sorted(names)))


# ---------------------------------------------------------------- program checks

def main_outputs(local_dir, fresh, pattern):
    """Top-level files of the job folder matching pattern, split into (fresh, stale)."""
    found = sorted(f for p in (pattern, pattern + ".gz") for f in glob.glob(os.path.join(local_dir, p))
                   if os.path.isfile(f))
    return [f for f in found if f in fresh], [f for f in found if f not in fresh]


def check_vasp(local_dir, fresh):
    problems, facts = [], {}
    outcar, oszicar = os.path.join(local_dir, "OUTCAR"), os.path.join(local_dir, "OSZICAR")
    if outcar + ".gz" in fresh:
        outcar += ".gz"
    files = [f for f in (outcar, oszicar) if os.path.isfile(f)]
    if outcar not in fresh:
        return ["no OUTCAR written during this job" + (" (an older one is there)" if os.path.isfile(outcar) else "")], facts, files
    text = read(outcar)
    if VASP_END not in text:
        problems.append("OUTCAR has no normal-end timing section")
    tags = P.read_incar(os.path.join(local_dir, "INCAR"))
    ibrion, nsw, nelm = P.incar_int(tags, "IBRION", -1), P.incar_int(tags, "NSW", 0), P.incar_int(tags, "NELM", 60)
    facts.update(ibrion=ibrion, nelm=nelm)
    if oszicar in fresh:
        info = P.analyse_oszicar(oszicar)
        counts = info["scf_counts"]
        facts.update(ionic_steps=len(counts), max_scf=max(counts) if counts else None,
                     last_F=info["energies"][-1] if info["energies"] else None)
        if info["nan"]:
            problems.append("NaN in OSZICAR")
        if counts and not info["scf_lines"]:
            problems.append("electronic-step lines in OSZICAR not recognised")
        at_nelm = [i + 1 for i, n in enumerate(counts) if n >= nelm]
        if ibrion in P.FREQUENCY_IBRION and at_nelm:
            problems.append("displacement(s) %s reached NELM=%d" % (at_nelm, nelm))
        elif counts and counts[-1] >= nelm:
            problems.append("last ionic step reached NELM=%d" % nelm)
    else:
        problems.append("no OSZICAR written during this job")
    if ibrion in P.RELAX_IBRION and nsw > 0 and VASP_RELAXED not in text:
        problems.append("geometry not converged (no '%s')" % VASP_RELAXED)
    if ibrion in P.FREQUENCY_IBRION:
        modes = sorted(set(int(m) for m in re.findall(r"^\s*(\d+)\s+f/i=", text, re.M)))
        facts["imaginary_modes"] = len(modes)
        if modes:
            problems.append("%d imaginary mode(s)" % len(modes))
    return problems, facts, files


def check_orca(local_dir, fresh):
    problems, facts = [], {}
    new, old = main_outputs(local_dir, fresh, "*.out")
    files = [f for f in new if "O   R   C   A" in read(f)[:20000]]
    if not files:
        return ["no ORCA output written during this job"], facts, new + old
    for path in files:
        text, name = read(path), os.path.basename(path)
        if ORCA_END not in text:
            problems.append("%s: no '%s'" % (name, ORCA_END.strip("*")))
        problems += ["%s: '%s'" % (name, m.group(0)) for bad in ORCA_BAD
                     for m in [re.search(bad, text, re.I)] if m]
        if "GEOMETRY OPTIMIZATION CYCLE" in text and "THE OPTIMIZATION HAS CONVERGED" not in text:
            problems.append("%s: optimisation not converged" % name)
        imaginary = re.findall(r"^\s*\d+:\s+(-?\d+\.\d+) cm\*\*-1\s+\*\*\*imaginary mode\*\*\*", text, re.M)
        if imaginary:
            problems.append("%s: imaginary frequencies %s cm-1" % (name, ", ".join(imaginary)))
        energies = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", text)
        if energies:
            facts[name] = {"final_energy_Eh": float(energies[-1])}
    return problems, facts, files


def check_cp2k(local_dir, fresh):
    problems, facts = [], {}
    new, old = main_outputs(local_dir, fresh, "*.out")
    new2, old2 = main_outputs(local_dir, fresh, "*.log")
    files = [f for f in new + new2 if "CP2K|" in read(f)[:200000]]
    if not files:
        return ["no CP2K output written during this job"], facts, new + old + new2 + old2
    for path in files:
        text, name = read(path), os.path.basename(path)
        if CP2K_END not in text:
            problems.append("%s: no '%s'" % (name, CP2K_END))
        problems += ["%s: '%s'" % (name, bad) for bad in CP2K_BAD if bad in text]
        if re.search(r"\bABORT\b", text):
            problems.append("%s: ABORT" % name)
        geo = re.search(r"Run type\s+GEO_OPT", text) or "GEOMETRY OPTIMIZATION" in text
        if geo and "GEOMETRY OPTIMIZATION COMPLETED" not in text:
            problems.append("%s: geometry optimisation not completed" % name)
        negative = [v for line in re.findall(r"VIB\|Frequency \(cm\^-1\)((?:\s+-?\d+\.\d+)+)", text)
                    for v in line.split() if float(v) < 0]
        if negative:
            problems.append("%s: imaginary frequencies %s cm-1" % (name, ", ".join(negative)))
        energies = re.findall(r"ENERGY\| Total FORCE_EVAL \( QS \) energy \[a\.u\.\]:\s+(-?\d+\.\d+)", text)
        if energies:
            facts[name] = {"final_energy_Eh": float(energies[-1])}
    return problems, facts, files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    parser.add_argument("--ack", metavar="REPORT")
    args = parser.parse_args()
    out = []
    try:
        rnd = Round(args.config)
        lock = open(os.path.join(rnd.dir, "lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("FAILED another round of this monitor is still running")
            return 2
        code = rnd.ack(args.ack, out) if args.ack else rnd.run(out)
    except Exception as exc:
        out, code = ["FAILED check did not complete: %s" % exc], 2
    print("\n".join(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
