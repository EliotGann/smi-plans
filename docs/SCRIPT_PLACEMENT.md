# Decision: ad hoc scripts live in SWAXS_user_scripts

Recorded 2026-10-02.

**smi-plans is the reusable library. SWAXS_user_scripts is the home of custom
experiment scripts.** This applies to staff- and agent-generated scripts requested
by users as well as manually written ones.

| Content | Home |
|---|---|
| Reusable acquisition, analysis, sampling, recovery primitives | `smi-plans/src/smi_plans/` |
| Library tests, API documentation, design decisions and field lessons | `smi-plans/tests/`, `docs/`, `skills/` |
| Beamtime-specific plans, setup/list builders, numbered restarts | `SWAXS_user_scripts/<user-group>/<date-or-purpose>/` |
| Script-specific tests, recovery snapshots and generated reports | Alongside the owning script in `SWAXS_user_scripts` |
| Maintained common-measurement examples | `SWAXS_user_scripts/templates/` |

Repository: https://github.com/NSLS-II-SMI/SWAXS_user_scripts

Local checkout: `~/SWAXS_user_scripts` (normally `../SWAXS_user_scripts` from this
repository). User-group folders follow its README; do not guess a PI or proposal
association. Historical material spanning groups can use a dated `historical/`
directory. Reusable snippets in Markdown remain appropriate library documentation.

## September/October 2026 migration

The ad hoc files previously accumulated in `smi-plans/docs/` are archived at
[`SWAXS_user_scripts/historical/2026-09_10_alignment_energy/`](https://github.com/NSLS-II-SMI/SWAXS_user_scripts/tree/main/historical/2026-09_10_alignment_energy).
They are **historical beamtime recipes**, with fixed sample IDs, holder names,
scan IDs, positions and setup assumptions; they are not the supported generic
grazing/recovery interface. Their historical defaults are preserved.

Moved together:

- `hard_grazing_damage_check.py`, `holder2_multi_edge_grid.py`, `saxs_nexafs_snake.py`;
- `tender_resume_1170553.py`, `tender_resume_1170565.py`, `prepare_hard_resume.py`
  and `hard_resume_1170456.json`;
- `add_l3_energy_lists.py`, `add_transition_metal_energy_lists.py`;
- `examine_scan_timing.py`, `HOLDER2_TIMING_EXAMINATION.md` and the three
  `holder2_timing_*` CSV/JSON outputs;
- the loose `voltage_calibration.svg` artifact;
- the three tests specific to those scripts, under the archive's `tests/` directory.

The scripts were untracked here, so their first committed versions are in the
destination repository. Script tests use the smi-plans simulation fixture via an
explicit checkout path; library tests do not require the scripts repository.

For new grazing work use [GRAZING_WORKFLOWS.md](GRAZING_WORKFLOWS.md) and the public
`GrazingScan`/`GrazingContext`/`grazing_scan` API. The reusable implementation still
needs beamline commissioning; archiving the field scripts is an ownership decision,
not a claim that the new library has already replaced them operationally.
