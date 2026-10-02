# Repository scope

- Put reusable acquisition/analysis APIs in `src/smi_plans/`, their tests in `tests/`,
  and API/design/workflow documentation in `docs/`.
- When a user asks for an ad hoc experiment, beamtime, setup, or numbered recovery
  script, create it in the **SWAXS_user_scripts** repository, normally the sibling
  checkout `../SWAXS_user_scripts` (`~/SWAXS_user_scripts` on this workstation).
  Use that repository's user-group/date conventions. Do not place executable
  one-off scripts or their generated data in `smi-plans/docs/`.
- Prefer calling the public `smi_plans` APIs from user scripts. Promote shared
  behavior into this library when it becomes reusable.
- If the scripts checkout is missing, locate/confirm its destination instead of
  silently placing scripts here. See `docs/SCRIPT_PLACEMENT.md` for the decision
  and the historical archive location.
