# Materials Project Workflow / 材料科研计算工作流

An agent Skill (for Codex or Claude Code) that starts from a computational-materials research goal, helps choose a project-specific ML/VASP/HPC route, then helps run, monitor, recover and organize the calculations. The researcher reviews the first actual HPC input package for a new route and site; routine work within that reviewed route then proceeds without repeated approval. AiiDA is optional. No other Skill or repository is required.

一个 Agent Skill（Codex 或 Claude Code 均可用）：从材料计算的科研目标出发，商定本课题的 ML/VASP/HPC 路线，再协助运行、监控、处理收敛问题和整理结果。新路线、新机器的第一份实际 HPC 输入由研究者审核；之后同一路线的日常任务不逐个确认。AiiDA 可选，无须安装其他 Skill 或仓库。

## Install / 安装

Copy [`skills/materials-project-workflow/`](skills/materials-project-workflow/) into your agent's skills folder, for example `~/.codex/skills/materials-project-workflow/` (Codex) or `~/.claude/skills/materials-project-workflow/` (Claude Code). Then describe a computational-materials task, or invoke the Skill by name. It follows the researcher's language.

把上述目录复制到 Agent 的 skills 目录，然后直接描述材料计算任务或按名称调用。Skill 按使用者的语言交流。

## Personal defaults and project records / 个人默认与课题记录

On a researcher's first substantial project, the Skill offers to save their usual route in `materials-project-workflow/DEFAULT.md` under the agent's configuration folder (`~/.codex` for Codex, `~/.claude` for Claude Code). [`example-default.md`](skills/materials-project-workflow/references/example-default.md) shows one researcher's 2D-adsorption route as an example; it applies only when selected. Each project keeps its chosen route in `WORKFLOW.md` and a one-screen `STATUS.md` covering local and HPC work.

首次做较大课题时，Skill 会建议把个人常用路线存进 `DEFAULT.md`。`example-default.md` 是一位研究者的二维吸附路线示例，只在被选用时生效。每个课题各自记录 `WORKFLOW.md` 和一屏读完的 `STATUS.md`。

## Design principles / 设计原则

- The Skill is a general framework. Project-specific requirements live in each project's own records; numbers in the Skill are overridable defaults. / Skill 只写通用框架；课题细节写在各课题自己的记录里；Skill 中的具体数值都是可覆盖的默认值。
- Scheduler completion, numerical convergence and scientific acceptance are kept distinct. / 调度器完成、数值收敛和科学接受是三回事。
- Each fact is kept in one place; status pages stay short and link to evidence. / 同一事实只记一处；状态页保持简短，链接到原始证据。

## Monitoring scripts / 监控脚本

For Slurm or PBS jobs, [`monitor_round.py`](skills/materials-project-workflow/scripts/monitor_round.py) does one monitoring round: it copies back ended jobs, checks VASP, ORCA or CP2K end and convergence markers, updates the record table and hands the agent only what it cannot confirm as normal. It never submits, cancels or changes a job. Requirements: macOS or Linux with Python 3, `ssh` and `rsync`; `python3` ≥ 3.6 on the cluster login node. Unattended use needs noninteractive SSH access. Tests: `python3 -m unittest discover -s validation` (needs `rsync`).

Slurm 或 PBS 作业可用 `monitor_round.py` 跑一轮监控：拷回已结束作业、检查 VASP/ORCA/CP2K 的正常结束和收敛标志、更新记录表，只把它不能确认正常的情况交给 Agent。脚本从不提交、取消或修改作业。无人值守运行需要免交互 SSH 登录。

## Status / 状态

Used in real projects by its original author. The Wannier and symmetry-eigenvalue guidance has not yet been tried on real calculations; running-job health checks exist only for VASP. Corrections are welcome.

已在原作者的真实课题中使用。Wannier 与对称性本征值部分尚未在真实计算中验证；运行中的健康检查目前只支持 VASP。欢迎指正。

## License

[MIT](LICENSE)
