# Status, monitoring and readable files

Use the researcher's language and existing project convention. Keep project roots small; put detailed evidence beside the calculation, not on the first screen of the status page.

Keep each fact at one current source: project choices in `WORKFLOW.md`, execution scope, job identity and input/script digests in existing scope/run records, and current actions/results in `STATUS.md`. Notes and monitors link to these records rather than copying them wholesale. Update affected fields and summaries; retain original inputs and meaningful change history. Existing project contracts still apply; the Skill itself requires no additional authorization JSON or bespoke ledger.

## One `STATUS.md` for local and remote work

Keep the current page readable in one screen; put detailed evidence and dated history behind links. Use the project's language and these sections:

1. **Needs your action or decision** — only concrete pending actions/choices and a short recommendation. When empty, write only “none”; do not put normal progress, parameters or authorization history under it.
2. **Anomalies I am handling** — only unresolved issues with a next action. State the case, problem, next step and whether user action is needed in one or two lines. A failed/nonselected result already closed by the researcher belongs in history unless it still blocks current work.
3. **Current work and next milestone** — summarize simultaneous jobs by group/stage and counts, with one link to the full job list. State the next milestone or review; mention monitor cadence when useful. Long job-ID lists and routine resource/input details stay in the job list and run records; include the current job ID when it helps act on an issue.
4. **Recent milestones** — usually the latest three important results/decisions, one or two lines each with a source link. Link additional dated events from existing notes/history rather than appending complete previous-page snapshots. Keep meaningful history accessible for at least seven days; preserve earlier records and mark superseded advice as historical.

For example, adapt this shape rather than copying its illustrative counts or paths:

```markdown
# Project status
Last checked: YYYY-MM-DD HH:MM TZ

## Needs your action or decision
None.

## Anomalies I am handling
None.

## Current work and next milestone
Adsorption coarse DFT: 12 running; 30-minute monitor active. [Jobs](calculations/jobs.csv)
Next: after convergence, researcher selects structures for fine relaxation.

## Recent milestones
- MM-DD: ML candidates selected. [Results](results/ml/NOTE.md)
- MM-DD: coarse DFT started after input review. [Run notes](calculations/NOTE.md)

[Earlier events](calculations/STATUS_HISTORY.md)
```

Do not fill STATUS with SCF/force trajectories, hashes, generation/audit/backup checklists, long resource/authorization explanations or routine “no temporary cache” receipts. Include only the decisive evidence for a current issue; link the full record. `WORKFLOW.md` holds the current route and detailed decision history stays beside its source calculation.

At each meaningful step, reconcile current actions, anomalies, result and next milestone before reporting completion or handing off. Update changed conclusions and their evidence timestamp; unchanged normal queueing/running needs no rewrite. For local ML use current process/log/checkpoint/output; for HPC use current run identity, queue **and accounting**, and recent outputs. Preserve unique attempt identity, input/script digests and unknown-submission fencing in the run records: queue absence is not failure or permission to resubmit. Scheduler completion, numerical convergence and scientific acceptance remain distinct. Use this project's scientific criteria and job identities, never another project's thresholds or jobs.

If an existing STATUS is long, retain useful history in linked project records and replace its current summary; do not delete original scientific evidence or create a second competing action page. Preserve existing `HPC_STATUS.md` history when establishing one current STATUS entry. Link STATUS from the project README when one exists.

During active work, create one project-scoped scheduled agent follow-up: check every 30 minutes at first, change to every 2 hours after the calculation is demonstrably stable, and use 15-minute checks while a short frequency job warrants them (or follow the project's chosen cadence). Adjust the existing follow-up rather than accumulating duplicate monitors. Keep its prompt short: name the monitored scope and link current project/run records and rules, instead of pasting all parameter and approval histories. For HPC jobs, each round first runs [monitor_round.py](../scripts/monitor_round.py) with the project's `monitor.json`, which lists only the active job groups (format and exit codes in the script header); write it after the jobs are submitted. The script, not the follow-up, copies back ended jobs, checks the program's end and convergence markers, writes `MONITOR_CHECK.json`, updates the record table and absorbs normal queue changes. On `QUIET` or `NO_JOBS` the round ends with no further reads; only the first `NO_JOBS` is reported to the main session. On `ATTENTION`, analyse only the listed items and files, report, then `--ack` that report's number. At a group end the follow-up only summarises the results and marks anomalies; anything needing scientific judgement or a decision on the next step goes to the main session as a report, and the follow-up decides nothing itself. Where the scheduler lets you choose the model (for a Claude desktop scheduled task, the `model:` line in its SKILL.md frontmatter), a smaller model is usually enough for these rounds. Keep project-specific handling in project files, read only on `ATTENTION`. Recheck the current scope before acting. Notify only on an actionable anomaly, changed recovery plan, required decision or milestone; unchanged progress stays silent. Do not create it before there is active work. The project's main session changes its cadence and retires it after completion and reconciliation. Unattended HPC checks require noninteractive login and network access in that follow-up's actual runtime. A failed connection is an access issue, not evidence that a VASP job failed. Update `STATUS.md` before sending a short notification.

## ML runs and local resource use

Apply the ML force criterion and step limit from `SKILL.md` (or the project's choice), whether ML runs locally or on HPC. Set both values in the actual runner at launch, ending as soon as the force criterion is met; do not plan short-step runs or artificial restart checkpoints. After a genuine interruption or walltime timeout, recover as `SKILL.md` describes, preserving the completed-step count. Do not merely write the limit in a note, reset the count on recovery or apply DFT restart-review gates to ML. Preserve the model and convergence target; genuine divergence, invalid numerical output or structural ambiguity still needs diagnosis. At the selected ceiling, report an unconverged result and next option in STATUS; extending beyond that ceiling needs a researcher decision.

On HPC, the job request sets ML resources and the scheduled follow-up confirms that the job started with the intended settings and is progressing; the rest of this section concerns local runs. Inspect actual CPU capacity, RAM, available accelerators, current load and existing ML workers before choosing local resources. Keep an everyday computer responsive; on a dedicated compute host, use more of its available or allocated capacity. Use the known project/machine context rather than requiring another setup interview; when unclear, begin conservatively. **One worker, 1–2 CPU threads and a small supported batch** are a fallback for an unprofiled interactive computer, not universal settings or a cap for a dedicated machine. Account for all projects on the same host so they do not each claim its full capacity. Queue remaining structures rather than loading the whole candidate set or a model copy per candidate.

Apply limits through the actual runner and its numerical libraries, including any nested worker/loader pools; verify the effective settings rather than just writing a limit in a note. For a PyTorch runner, [set CPU threads before eager/JIT/autograd work](https://docs.pytorch.org/docs/stable/generated/torch.set_num_threads.html). A CPU-thread limit does not bound model/batch memory or accelerator use. On macOS, inspect [memory pressure and swap activity](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac), not just free RAM.

For a local run, observe the first normal calculation through model loading and initial force evaluation, checking CPU, process memory, system pressure and responsiveness within the first minute and during startup; do not wait for the scheduled project follow-up. Reuse existing runner/system tools without adding a monitoring framework or extra scientific benchmark. Increase one resource setting at a time while retaining headroom appropriate to interactive or dedicated use and other work. Reuse a verified machine/tool/model profile, rechecking current load rather than reprofiling every job. Save a concise profile in existing user defaults when available, and record the effective project settings or differences in `WORKFLOW.md`/run settings. A different machine, backend, model or materially larger structure needs a fresh resource check; hardware detection alone does not authorize changing the scientific route or numerical precision.

On sustained high memory pressure, rapid swap growth or loss of responsiveness, stop launching more work and reduce batch/concurrency/threads as appropriate. If ongoing work must be stopped, checkpoint where supported, preserve completed results and interrupt only the identified project worker; confirm it has exited before restarting at lower resource use. Do not kill unrelated processes, disable allocator safeguards, or change the model, numerical precision or convergence target merely to make it fit. If even one small-batch worker cannot fit, report that specific limitation and suggest an available suitable compute host. Ordinary resource reductions within the approved route do not need a new scientific approval; update STATUS only for a meaningful issue or change.

## Human-readable calculation directories

Use short scientific names and group multi-candidate runs by a stable, human-readable structure or route name. Keep that name across ML, coarse DFT and fine DFT when it is the same candidate. Within a group, `run01`, `run02` mark meaningful calculations or restarts along that route; routine parameter changes do not create another group. A genuine branch from an older result may get a separate, clearly named route, with its source recorded in `NOTE.md`. Put parameter differences, run purpose, job identity, source structure and outcome in a short `NOTE.md`, not a huge folder name. Do not create a run directory for edits made before execution or precreate unused future stages. For example, the same logical naming may live under different local and HPC storage roots:

```text
calculations/NO3/01_ml/selected/        # real local folder; several ML candidates
calculations/NO3/02_dft_coarse/runs/top_site/run01/
calculations/NO3/02_dft_coarse/runs/top_site/run02/   # continuation of run01
calculations/NO3/02_dft_coarse/runs/bridge_site/run01/
calculations/NO3/02_dft_coarse/final_choice
calculations/NO3/02_dft_coarse/FINAL_CHOICE.md
calculations/paths/01_NO3_to_NO2/ci_neb/run01/
calculations/paths/01_NO3_to_NO2/idm/run01/direction/
calculations/paths/01_NO3_to_NO2/idm/run01/fine/
results/NO3/
results/paths/01_NO3_to_NO2/
```

Create `idm/` only if used; create `coarse/` inside an IDM attempt only when needed. Keep each CI-NEB run's whole image set together. The reaction-step `final_choice` points to the adopted CI-NEB run or IDM fine result; `FINAL_CHOICE.md` names the real path, reason and whether the full path or only a transition state is validated.

Do not equate a scheduler submission with a new `runNN`. An obvious early mis-submit may be corrected in the same run after the old job is conclusively stopped and any original submitted inputs, script, job identity and error evidence are retained; never let two jobs write that directory or overwrite useful outputs. A restart from a useful structure/checkpoint, a distinct calculation, or a branch that needs its own raw output gets a new `runNN`. Keep nonselected attempts in their route under `runs/`, without separate success/failure/test trees. `final_choice` means an adopted result, not the newest run: create or change it only after selection, and leave it pointing to the previous valid choice while a successor is still running. When a stage advances several structures, use `selected/` for that set rather than falsely declaring one final choice.

On HPC, `final_choice` may be a symlink to the real adopted run, with neighboring `FINAL_CHOICE.md` so the choice remains readable if the link breaks. **Never create any filesystem link inside a cloud-synced folder such as OneDrive.** There, use real `selected/` or `final_choice/` folders containing only the adopted small outputs and source/reason note. `results/` is a curated view of adopted structures, energies/barriers and source paths, not a raw-output mirror.

Choose the backup destination and retention scope once per project. Preserve formal results, input/script/job provenance and raw evidence required for reuse; verify authoritative copies before removing any exact disposable duplicate. Organize or compress failed/nonselected scientific attempts when useful, but do not classify unique VASP outputs, structures or trajectories as cache. At normal task completion, remove exact, unused, reproducible run/development/preview/inspection/transfer temporary files created by that task; do not merely move disposable files into an archive. On an explicit project-cleanup request, also inspect older generated caches and transfer packages, deleting only exact verified unused targets. Never sweep broadly or remove original scientific data, provenance or needed scripts.
