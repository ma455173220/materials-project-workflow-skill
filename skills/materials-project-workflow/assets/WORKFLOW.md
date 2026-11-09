# Project workflow

Adapt this outline to the project's language and keep only useful fields.

## Goal and current evidence

What question should the calculations answer? Which local/HPC files and results already exist, and where are their authoritative copies?

## Chosen route

| Stage | Purpose and relevant settings | DFT walltime / job or ML steps / structure | Maximum DFT restarts / structure | What advances / handoff condition |
| --- | --- | --- | --- | --- |
| | | | | |

Record the number/diversity of candidates that advance where relevant. For ML, record the force criterion and step limit (the Skill default unless another is chosen) and set them in the actual runner from the start. Genuine interruption recovery within the remaining allowance needs no separate approval. For DFT, confirm walltime and restart limits with the representative inputs; the first job is excluded from the restart count. Link current usage to existing run records. Any additional agreed project budget remains in force. A proposed route is not a completed result or a submission grant.

## Tools and first-input review

Record only relevant ML software/model, HPC site/access, POTCAR method, site script source, reviewed actual input package, executable, resources and bounded work scope. Keep related input files consistently formatted for comparison. For local ML, record the effective worker/thread/batch profile and account for other work on the same computer.

## Monitoring and backup

Name the current `STATUS.md`, active monitor cadence when work is running, chosen backup destination and retention scope. No monitor is needed while nothing runs.

## Current state and decisions

Distinguish researcher-confirmed choices from proposals. Record the representative input package and series scope actually reviewed, or the pending review. Keep the current choices, validated results, uncertainties and next step concise; update changed fields in place. Link to relevant calculation notes for meaningful decision history and adopted results instead of copying full run records or accumulating complete previous plans here.
