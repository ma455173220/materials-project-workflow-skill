#!/usr/bin/env python3
"""One-command quiet check for a project's Slurm jobs.

Runs hpc_job_probe.py on the login node over one non-interactive ssh call,
compares the result with the project's saved baseline and prints either

    QUIET ...                          (exit 0) every check passed; nothing to do
    ATTENTION n item(s), report R ...  (exit 1) look at the listed items properly
    ATTENTION check failed ...         (exit 2) the check itself failed

Anything not positively confirmed healthy is ATTENTION. Reported items are
listed again by every later run until `--ack R` names the report that was
handled; a later run issues a new report number, so acknowledge only the
report whose output you have read. Exit 2 leaves nothing to acknowledge: fix
the cause (login, prefix, state file) or do the full check by hand. Problems
that disable the health check itself (probe error, no or unreadable OSZICAR,
no jobs under the prefix) cannot be acknowledged; they repeat until gone.
Read-only on HPC.

    check_hpc_jobs.py --init  --host USER@HOST --prefix /scratch/.../project --state STATE.json
    check_hpc_jobs.py         --host USER@HOST --prefix /scratch/.../project --state STATE.json
    check_hpc_jobs.py --ack R --state STATE.json
    check_hpc_jobs.py --replay RUN_DIR      # analyse a copied run directory

--init records the jobs already under the prefix as known and lists them;
health problems already present are reported as ATTENTION like any other run.
--record-dir DIR writes one new JSON file per check (time, verdict, ssh exit
code, script SHA256, jobs seen) for projects that log every remote access.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "hpc_job_probe.py")
ACTIVE = {"RUNNING", "PENDING", "CONFIGURING", "COMPLETING", "REQUEUED", "RESIZING", "SUSPENDED"}
NORMAL_PENDING = {
    "Priority", "Resources", "None", "Dependency", "BeginTime",
    "QOSMaxJobsPerUserLimit", "QOSMaxCpuPerUserLimit", "QOSMaxNodePerUserLimit",
    "AssocMaxJobsLimit", "QOSGrpCpuLimit", "QOSGrpNodeLimit", "AssocGrpCpuLimit", "AssocGrpNodeLimit",
}
JOB_EVENTS = {"started", "exit", "vanished", "pending", "state", "requeued", "restarted"}
NOT_ACKABLE = {"probe_error", "no_output", "unparsed", "empty"}


class CheckFailed(Exception):
    def __init__(self, message, ssh_exit=None):
        super().__init__(message)
        self.ssh_exit = ssh_exit


def load(path, default=None):
    try:
        with open(path) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise CheckFailed("cannot read %s: %s" % (path, exc))


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
    os.replace(tmp, path)


def ackable(sig):
    return not NOT_ACKABLE.intersection(sig.split(":"))


def fetch(args, since_epoch):
    """Return (ssh exit code, probe result); raise CheckFailed on any failure."""
    config = {"prefixes": args.prefix, "since_epoch": since_epoch, "stall_min": args.stall_min}
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ControlMaster=no",
           "-o", "ControlPath=none", args.host, "python3", "-", encoded]
    with open(PROBE) as source:
        done = subprocess.run(cmd, stdin=source, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise CheckFailed("ssh exit %d: %s" % (done.returncode, done.stderr.strip()[-300:]), done.returncode)
    try:
        result = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise CheckFailed("probe output is not JSON", 0)
    if not result.get("ok"):
        raise CheckFailed(result.get("error", "probe returned no result"), 0)
    return done.returncode, result


def compare(baseline, result):
    """Return (events, new_state). Events are (signature, message)."""
    events = []
    now = result["now"]
    old_jobs = baseline.get("jobs", {})
    reported = set(baseline.get("reported_terminal", []))
    queue = {job["id"]: job for job in result["queue"]}
    finished = {}
    for job in result["accounting"]:
        if job["state"].split()[0] not in ACTIVE and job["id"] not in queue:
            finished[job["id"]] = job
    for job_id, job in finished.items():
        if job_id not in reported:
            events.append(("exit:%s" % job_id, "%s %s: ended %s (exit %s, %s)" % (
                job_id, job["name"], job["state"], job["exit"], job["elapsed"])))
    lost = {}
    for job_id, old in old_jobs.items():
        if job_id not in queue and job_id not in finished and job_id not in reported:
            # Keep it in the baseline until accounting shows how it ended.
            lost[job_id] = dict(old, state="VANISHED", seen=old.get("seen", now))
            events.append(("vanished:%s" % job_id,
                           "%s %s: left the queue but has no accounting record yet" % (job_id, old.get("name"))))
    for job_id, job in queue.items():
        old = old_jobs.get(job_id, {})
        label = "%s %s" % (job_id, job["name"])
        was_running = old.get("state") == "RUNNING"
        if job["state"] == "RUNNING" and not was_running:
            events.append(("started:%s" % job_id, "%s: started running (startup check due)" % label))
        if was_running and job["state"] != "RUNNING":
            events.append(("requeued:%s:%d" % (job_id, now),
                           "%s: was RUNNING, now %s (%s)" % (label, job["state"], job["reason"])))
        elif was_running and old.get("elapsed_min") is not None and baseline.get("checked_at"):
            expected = old["elapsed_min"] + (now - baseline["checked_at"]) / 60.0
            if (job.get("elapsed_min") or 0) < expected - 2:
                events.append(("restarted:%s:%d" % (job_id, now), "%s: run time %.0f min, expected about %.0f (restarted?)"
                               % (label, job.get("elapsed_min") or 0, expected)))
        if job["state"] == "PENDING" and job["reason"] not in NORMAL_PENDING:
            events.append(("pending:%s:%s" % (job_id, job["reason"]), "%s: pending, reason %s" % (label, job["reason"])))
        if job["state"] not in ("RUNNING", "PENDING"):
            events.append(("state:%s:%s" % (job_id, job["state"]), "%s: state %s" % (label, job["state"])))
        for flag in result["runs"].get(job_id, {}).get("flags", []):
            events.append(("%s:%s" % (job_id, flag["sig"]), "%s: %s" % (label, flag["msg"])))
    if not queue:
        events.append(("empty", "no queued or running jobs under the prefix; retire the monitor if this work is finished"))
    acked = set(baseline.get("acked", []))
    events = [(sig, msg) for sig, msg in events if sig not in acked]
    jobs = {job_id: {"state": job["state"], "name": job["name"], "dir": job["dir"],
                     "elapsed_min": job.get("elapsed_min")} for job_id, job in queue.items()}
    jobs.update(lost)
    new_state = {
        "checked_at": now,
        "jobs": jobs,
        "reported_terminal": sorted(reported | set(finished))[-2000:],
        "acked": sorted(acked)[-2000:],
    }
    return events, new_state


def quiet_line(result):
    running = [j for j in result["queue"] if j["state"] == "RUNNING"]
    pending = [j for j in result["queue"] if j["state"] == "PENDING"]
    ages = [r.get("summary", {}).get("output_age_min") for r in result["runs"].values()]
    ages = [a for a in ages if a is not None]
    newest = " every running job wrote output within %.0f min;" % max(ages) if ages else ""
    stamp = datetime.datetime.fromtimestamp(result["now"]).strftime("%Y-%m-%d %H:%M")
    return "QUIET %d running, %d pending;%s checked %s" % (len(running), len(pending), newest, stamp)


def check(args, out, meta, saves):
    """Run one check, appending output lines to out and (path, data) writes to saves
    (data None removes the file); return the exit code. meta collects ssh exit and result."""
    pending_path = args.state + ".pending"
    baseline = load(args.state)
    pending = load(pending_path)
    if args.init and pending:
        raise CheckFailed("report %s is not acknowledged; handle it and --ack it before --init" % pending.get("report"))
    if baseline is None and not args.init:
        raise CheckFailed("no baseline at %s; run once with --init" % args.state)
    baseline = baseline or {}
    starts = [baseline.get("checked_at") or (time.time() - args.lookback_hours * 3600)]
    starts += [j["seen"] for j in baseline.get("jobs", {}).values() if j.get("state") == "VANISHED"]
    meta["ssh_exit"], result = fetch(args, min(starts) - 1800)
    meta["result"] = result

    events, new_state = compare(baseline, result)
    if args.init:
        if not result["queue"]:
            raise CheckFailed("no queued or running jobs under %s; check --prefix" % ", ".join(args.prefix))
        known = [(sig, msg) for sig, msg in events if sig.split(":")[0] in JOB_EVENTS]
        events = [(sig, msg) for sig, msg in events if sig.split(":")[0] not in JOB_EVENTS]
        new_state["acked"] = sorted(set(new_state["acked"]) | {sig for sig, _ in known})
        saves.append((args.state, new_state))
        out.append("INIT baseline saved; " + quiet_line(result)[6:])
        out.extend("- already present: " + msg for _, msg in known)
        if not events:
            return 0
    elif pending:
        # Items reported earlier but not yet acknowledged are never dropped, even if
        # the condition has passed; problems that cannot be acknowledged are re-detected.
        current = {sig for sig, _ in events}
        note = " [earlier report, not acknowledged]"
        events += [(sig, msg if msg.endswith(note) else msg + note) for sig, msg in pending["events"]
                   if sig not in current and ackable(sig)]

    if not events:
        saves.append((args.state, new_state))
        if pending:
            saves.append((pending_path, None))
        out.append(quiet_line(result))
        return 0
    stamp = datetime.datetime.fromtimestamp(result["now"], datetime.timezone.utc).strftime("%Y%m%dT%H%MZ")
    digest = hashlib.sha256(json.dumps([result["now"], events]).encode()).hexdigest()[:6]
    report = "%s-%s" % (stamp, digest)
    new_state["acked"] = sorted(set(new_state["acked"]) | {sig for sig, _ in events if ackable(sig)})
    saves.append((pending_path, {"report": report, "events": events, "state": new_state}))
    out.append("ATTENTION %d item(s), report %s; after handling them run --ack %s" % (len(events), report, report))
    out.extend("- " + msg for _, msg in events[:30])
    if len(events) > 30:
        out.append("- ... %d more" % (len(events) - 30))
    return 1


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def write_record(directory, init, code, ssh_exit, result, out):
    """Write one new JSON file describing this check; never overwrite an earlier one."""
    os.makedirs(directory, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    queue = (result or {}).get("queue", [])
    jobs = [{"id": j["id"], "state": j["state"]} for j in queue]
    jobs += [{"id": j["id"], "state": j["state"]} for j in (result or {}).get("accounting", [])
             if j["id"] not in {q["id"] for q in queue}]
    record = {
        "checked_utc": now.isoformat(timespec="seconds"),
        "verdict": {0: "QUIET", 1: "ATTENTION", 2: "CHECK_FAILED"}[code],
        "init": init,
        "exit_code": code,
        "ssh_exit": ssh_exit,
        "output": out,
        "scripts": {os.path.basename(p): sha256(p) for p in (os.path.abspath(__file__), PROBE)},
        "jobs": jobs,
    }
    base = os.path.join(directory, "quiet_check_%s" % now.strftime("%Y%m%dT%H%M%SZ"))
    for n in range(100):
        path = base + (".json" if n == 0 else "_%d.json" % n)
        try:
            with open(path, "x") as handle:
                json.dump(record, handle, indent=1)
            return
        except FileExistsError:
            continue
    raise OSError("no free record name at %s" % base)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host")
    parser.add_argument("--prefix", action="append", default=[], help="remote directory prefix of this project's jobs")
    parser.add_argument("--state", help="local JSON baseline for this project")
    parser.add_argument("--stall-min", type=float, default=45.0)
    parser.add_argument("--lookback-hours", type=float, default=48.0)
    parser.add_argument("--init", action="store_true", help="record the current jobs as the baseline")
    parser.add_argument("--ack", metavar="REPORT", help="accept the named report's items as handled")
    parser.add_argument("--record-dir", help="write one JSON record per check into this directory")
    parser.add_argument("--replay", help="analyse a local copy of a run directory and exit")
    args = parser.parse_args()

    if args.replay:
        sys.path.insert(0, HERE)
        import hpc_job_probe
        # File times of a copied directory are meaningless, so skip the stall test.
        print(json.dumps(hpc_job_probe.health(args.replay, stall_min=float("inf")), indent=1))
        return 0
    if not args.state:
        parser.error("--state is required")
    pending_path = args.state + ".pending"
    if args.ack:
        try:
            pending = load(pending_path)
        except CheckFailed as exc:
            print("ack refused: %s" % exc)
            return 2
        if pending is None:
            print("nothing to acknowledge")
            return 0
        if args.ack != pending.get("report"):
            print("ack refused: the latest report is %s, not %s; read its output first" % (pending.get("report"), args.ack))
            return 2
        save(args.state, pending["state"])
        os.remove(pending_path)
        print("acknowledged report %s; baseline updated" % args.ack)
        return 0
    if not args.host or not args.prefix:
        parser.error("--host and --prefix are required")

    out, meta, saves = [], {}, []
    try:
        code = check(args, out, meta, saves)
    except Exception as exc:
        code, out, saves = 2, ["ATTENTION check failed: %s" % exc], []
        meta.setdefault("ssh_exit", getattr(exc, "ssh_exit", None))
    try:
        if args.record_dir:
            write_record(args.record_dir, args.init, code, meta.get("ssh_exit"), meta.get("result"), out)
        for path, data in saves:
            if data is None:
                os.remove(path)
            else:
                save(path, data)
    except Exception as exc:
        # Nothing was acknowledged or saved, so the next run repeats this check.
        code, out = 2, ["ATTENTION check failed: record or state not written: %s" % exc]
    print("\n".join(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
