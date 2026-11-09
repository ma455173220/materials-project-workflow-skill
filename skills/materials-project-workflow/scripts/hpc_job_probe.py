#!/usr/bin/env python3
"""Read-only probe of a user's Slurm or PBS jobs and their VASP outputs.

check_hpc_jobs.py sends this file over ssh and runs it on the HPC login node;
it can also be imported to analyse a copied run directory (replay tests).
It never writes, cancels or submits anything. Standard library only.
"""
import base64
import glob
import json
import math
import os
import re
import subprocess
import sys
import time

SCF_LINE = re.compile(r"^\s*(?:[A-Z]{2,4}:|trl)\s+(\d+)\s")
IONIC_LINE = re.compile(r"^\s*(\d+)\s+F=\s*(\S+)")
FREQUENCY_IBRION = {5, 6, 7, 8}
RELAX_IBRION = {1, 2, 3}
FLAT_STEPS, FLAT_EV = 40, 0.002  # energy flat within 2 meV over 40 ionic steps
ERROR_PATTERNS = [
    "VERY BAD NEWS",
    "ZBRENT: fatal",
    "BRMIX: very serious",
    "ZHEGV failed",
    "Error EDDDAV",
    "I REFUSE TO CONTINUE",
    "internal error",
    "ERROR",
    "Segmentation fault",
    "SIGSEGV",
    "forrtl",
    "MPI_ABORT",
    "MPI_Abort",
    "oom-kill",
    "Out Of Memory",
    "out of memory",
    "Bus error",
]
STDOUT_NAMES = ["vasp_log", "vasp.log", "vasp.out", "std.out", "stdout"]
MAX_STDOUT_BYTES = 50_000_000


def read_incar(path):
    tags = {}
    try:
        with open(path, errors="replace") as handle:
            for raw in handle:
                line = re.split(r"[#!]", raw, maxsplit=1)[0]
                for part in line.split(";"):
                    if "=" in part:
                        key, value = part.split("=", 1)
                        tags[key.strip().upper()] = value.strip()
    except OSError:
        pass
    return tags


def incar_int(tags, key, default):
    try:
        return int(float(tags.get(key, default)))
    except (TypeError, ValueError):
        return default


def analyse_oszicar(path):
    """Return per-ionic-step SCF counts, free energies and NaN evidence."""
    scf_counts, energies = [], []
    current = scf_lines = 0
    nan = False
    lines = 0
    with open(path, errors="replace") as handle:
        for line in handle:
            lines += 1
            scf = SCF_LINE.match(line)
            if scf:
                scf_lines += 1
                current = int(scf.group(1))
                if "nan" in line.lower() or "*****" in line:
                    nan = True
                continue
            ionic = IONIC_LINE.match(line)
            if ionic:
                text = ionic.group(2)
                try:
                    value = float(text)
                    if not math.isfinite(value):
                        nan = True
                except ValueError:
                    value = None
                    nan = True
                scf_counts.append(current)
                energies.append(value)
                current = 0
    return {"lines": lines, "scf_counts": scf_counts, "in_progress_scf": current,
            "energies": energies, "nan": nan, "scf_lines": scf_lines}


def count_errors(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > MAX_STDOUT_BYTES:
                handle.seek(size - MAX_STDOUT_BYTES)
            text = handle.read().decode(errors="replace")
    except OSError:
        return {}
    return {p: text.count(p) for p in ERROR_PATTERNS if p in text}


def stdout_files(run_dir, job_id):
    names = set()
    if job_id:
        names.update(glob.glob(os.path.join(run_dir, "*%s*" % job_id)))
    for name in STDOUT_NAMES:
        path = os.path.join(run_dir, name)
        if os.path.isfile(path):
            names.add(path)
    return sorted(p for p in names if os.path.isfile(p))


def oszicar_paths(run_dir):
    top = os.path.join(run_dir, "OSZICAR")
    if os.path.isfile(top):
        return [top]
    return sorted(glob.glob(os.path.join(run_dir, "[0-9][0-9]", "OSZICAR")))


def oszicar_flags(info, label, nelm, ibrion):
    """Health flags for one OSZICAR analysis; returns (signature, message) pairs."""
    flags = []
    steps = len(info["scf_counts"])
    if info["nan"]:
        flags.append(("nan:%s" % label, "NaN or overflow in %s" % label))
    if steps and not info.get("scf_lines", 1):
        # Ionic steps but no electronic-step line recognised: the SCF checks cannot work.
        flags.append(("unparsed:%s" % label, "no electronic-step lines recognised in %s" % label))
    hits = [i + 1 for i, n in enumerate(info["scf_counts"]) if n >= nelm]
    if info["in_progress_scf"] >= nelm:
        hits.append(steps + 1)
    if ibrion in FREQUENCY_IBRION:
        if hits:
            flags.append(("nelm:%s" % label,
                          "displacement(s) %s reached NELM=%d (%s)" % (",".join(map(str, hits)), nelm, label)))
    else:
        late = [s for s in hits if s > 2]
        latest = steps + (1 if info["in_progress_scf"] >= nelm else 0)
        if len(late) >= 2 and late[-1] == latest and late[-2] == latest - 1:
            flags.append(("nelm2:%s:%d" % (label, latest // 5),
                          "ionic steps %d-%d both reached NELM=%d (%s)" % (latest - 1, latest, nelm, label)))
    energies = [e for e in info["energies"] if e is not None]
    if ibrion in RELAX_IBRION and len(energies) >= 3:
        rise = energies[-1] - min(energies)
        recent = energies[2:][-6:]
        jumps = [b - a for a, b in zip(recent, recent[1:])]
        if rise > 1.0 or (jumps and max(jumps) > 1.0):
            worst = max([rise] + jumps)
            # Report again only when the rise reaches a higher band: 1, 2, 4, 8 ... eV.
            flags.append(("energy_up:%s:%d" % (label, int(math.log2(worst))),
                          "energy now %.2f eV above its minimum, or jumped up >1 eV in the last steps, at step %d (%s)"
                          % (rise, steps, label)))
        if len(energies) > FLAT_STEPS and abs(energies[-1] - energies[-1 - FLAT_STEPS]) < FLAT_EV:
            flags.append(("flat:%s:%d" % (label, len(energies) // 50),
                          "energy changed only %.1f meV over the last %d ionic steps (step %d, %s)"
                          % (1000 * (energies[-1] - energies[-1 - FLAT_STEPS]), FLAT_STEPS, len(energies), label)))
    return flags


def health(run_dir, job_id=None, elapsed_min=None, now=None, stall_min=45.0):
    """Analyse one run directory; return a summary and a list of flags.

    Each flag has a stable signature so a caller can suppress repeats that a
    person or agent has already examined.
    """
    now = time.time() if now is None else now
    tags = read_incar(os.path.join(run_dir, "INCAR"))
    nelm = incar_int(tags, "NELM", 60)
    ibrion = incar_int(tags, "IBRION", -1)
    flags = []
    summary = {"dir": run_dir, "nelm": nelm, "ibrion": ibrion}

    paths = oszicar_paths(run_dir)
    if not paths:
        if elapsed_min is None or elapsed_min > 20:
            flags.append(("no_output", "no OSZICAR in the job directory"))
        summary["oszicar"] = None
    newest = None
    for path in paths:
        label = os.path.relpath(path, run_dir)
        mtime = os.path.getmtime(path)
        newest = mtime if newest is None else max(newest, mtime)
        info = analyse_oszicar(path)
        steps = len(info["scf_counts"])
        summary[label] = {"ionic_steps": steps, "in_progress_scf": info["in_progress_scf"],
                          "last_F": info["energies"][-1] if info["energies"] else None}
        flags.extend(oszicar_flags(info, label, nelm, ibrion))
    for path in [os.path.join(run_dir, "OUTCAR")]:
        if os.path.isfile(path):
            mtime = os.path.getmtime(path)
            newest = mtime if newest is None else max(newest, mtime)
    if newest is not None:
        age = (now - newest) / 60.0
        summary["output_age_min"] = round(age, 1)
        if age > stall_min:
            flags.append(("stall:%d" % int(newest),
                          "no output written for %.0f min" % age))
    for path in stdout_files(run_dir, job_id):
        name = os.path.basename(path)
        for pattern, count in count_errors(path).items():
            flags.append(("err:%s:%s:%d" % (name, pattern, count),
                          "'%s' x%d in %s" % (pattern, count, name)))
    return {"summary": summary, "flags": [{"sig": s, "msg": m} for s, m in flags]}


def minutes(text):
    """Convert Slurm D-HH:MM:SS / HH:MM:SS / MM:SS to minutes."""
    try:
        days = 0
        if "-" in text:
            day_text, text = text.split("-", 1)
            days = int(day_text)
        parts = [int(p) for p in text.split(":")]
        while len(parts) < 3:
            parts.insert(0, 0)
        return days * 1440 + parts[0] * 60 + parts[1] + parts[2] / 60.0
    except ValueError:
        return None


def in_prefixes(path, prefixes):
    real = os.path.realpath(path) if path else ""
    return any(path.startswith(p) or real.startswith(os.path.realpath(p)) for p in prefixes)


def run(cmd, env=None):
    # Python 3.6 on some login nodes: no capture_output/text arguments.
    done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=120,
                          env=dict(os.environ, **env) if env else None)
    if done.returncode != 0:
        raise RuntimeError("%s exit %d: %s" % (cmd[0], done.returncode, done.stderr.strip()[-300:]))
    return done.stdout


def epoch(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def ctime_epoch(text):
    try:
        return time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y"))
    except (TypeError, ValueError):
        return None


PBS_STATES = {"Q": "PENDING", "W": "PENDING", "R": "RUNNING", "B": "RUNNING", "E": "COMPLETING",
              "H": "HELD", "S": "SUSPENDED", "T": "MOVING", "X": "COMPLETED"}


def probe_pbs(config, user, prefixes):
    """PBS: read queued, running and recently finished jobs via qstat -x."""
    ids = [line.split()[0] for line in run(["qstat", "-x", "-u", user]).splitlines()
           if re.match(r"^\d+\.", line)]
    now = time.time()
    queue, accounting = [], []
    for start in range(0, len(ids), 200):
        data = json.loads(run(["qstat", "-x", "-f", "-F", "json"] + ids[start:start + 200]) or "{}")
        for full_id, job in data.get("Jobs", {}).items():
            work_dir = job.get("Variable_List", {}).get("PBS_O_WORKDIR", "")
            if not in_prefixes(work_dir, prefixes):
                continue
            job_id, name = full_id.split(".")[0], job.get("Job_Name", "")
            used = job.get("resources_used", {}).get("walltime", "0:00:00")
            if job.get("job_state") == "F":
                end = job.get("obittime") or job.get("mtime") or ""
                if (ctime_epoch(end) or float("inf")) < config["since_epoch"]:
                    continue
                code = job.get("Exit_status")
                limit = job.get("Resource_List", {}).get("walltime")
                if code == 0:
                    state = "COMPLETED"
                elif limit and (minutes(used) or 0) >= (minutes(limit) or float("inf")):
                    state = "TIMEOUT"
                else:
                    state = "FAILED"
                accounting.append({"id": job_id, "name": name, "state": state, "exit": str(code), "elapsed": used,
                                   "start_epoch": ctime_epoch(job.get("stime")), "end": end, "dir": work_dir})
            else:
                state = PBS_STATES.get(job.get("job_state"), job.get("job_state", "UNKNOWN"))
                queue.append({"id": job_id, "state": state, "elapsed_min": minutes(used), "left": "",
                              "reason": "None", "dir": work_dir, "name": name})
    return now, queue, accounting


def probe(config):
    user = os.environ.get("USER") or run(["whoami"]).strip()
    prefixes = config["prefixes"]
    if config.get("scheduler", "slurm") == "pbs":
        now, queue, accounting = probe_pbs(config, user, prefixes)
        return {"ok": True, "now": now, "queue": queue, "accounting": accounting,
                "runs": running_health(queue, config)}
    queue = []
    squeue = run(["squeue", "-h", "-u", user, "-o", "%i|%T|%M|%L|%r|%Z|%j"])
    # Stamp the time with the queue snapshot: run times are compared against it,
    # and the later sacct and output checks can take minutes.
    now = time.time()
    for line in squeue.splitlines():
        parts = line.split("|", 6)
        if len(parts) == 7 and in_prefixes(parts[5], prefixes):
            job_id, state, elapsed, left, reason, work_dir, name = parts
            queue.append({"id": job_id, "state": state, "elapsed_min": minutes(elapsed),
                          "left": left, "reason": reason, "dir": work_dir, "name": name})
    accounting = []
    # sacct reads times in the cluster's own time zone, so convert here.
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(config["since_epoch"]))
    sacct = run(["sacct", "-X", "-n", "-P", "-u", user, "-S", since,
                 "-o", "JobID,JobName,State,ExitCode,Elapsed,Start,End,WorkDir"], env={"SLURM_TIME_FORMAT": "%s"})
    for line in sacct.splitlines():
        parts = line.split("|", 7)
        if len(parts) == 8 and in_prefixes(parts[7], prefixes):
            job_id, name, state, exit_code, elapsed, start, end, work_dir = parts
            accounting.append({"id": job_id, "name": name, "state": state, "exit": exit_code, "elapsed": elapsed,
                               "start_epoch": epoch(start), "end": end, "dir": work_dir})
    return {"ok": True, "now": now, "queue": queue, "accounting": accounting, "runs": running_health(queue, config)}


def running_health(queue, config):
    """VASP health of running jobs; only under health_prefixes when the caller gives them."""
    runs = {}
    for job in queue:
        if job["state"] == "RUNNING" and in_prefixes(job["dir"], config.get("health_prefixes", config["prefixes"])):
            try:
                runs[job["id"]] = health(job["dir"], job["id"], job["elapsed_min"],
                                         stall_min=config.get("stall_min", 45.0))
            except Exception as exc:  # report, never hide, a probe failure
                runs[job["id"]] = {"summary": {"dir": job["dir"]},
                                   "flags": [{"sig": "probe_error", "msg": "probe failed: %s" % exc}]}
    return runs


def main():
    try:
        config = json.loads(base64.b64decode(sys.argv[1]).decode())
        print(json.dumps(probe(config)))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))


if __name__ == "__main__":
    main()
