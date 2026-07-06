# Beamline Question Triage

Draft skill notes for deciding whether the current user is asking an SMI beamline operational
question.  This is not yet a strict opencode skill; it records the detection procedure and should
be promoted once the approved answer patterns are reviewed.

## Core Operating Principles

When talking to a beamline user, follow these principles strictly:

1. Never suggest something untested.  Suggest only a developed strategy based on vetted
   profile-collection or `smi-plans` code.  When suggesting code snippets for user scripts, adhere
   to good Bluesky-plan practice: generator plans, message-pure `bps.*`/`bp.*` operations, no raw
   `.put()`/`.get()` control flow inside plans unless a vetted profile helper already does it.
2. Always consult the tails of the latest logs before diagnosing errors or recent command state:
   `/home/xf12id/.cache/bluesky/log/bluesky.log` and
   `/home/xf12id/.cache/bluesky/log/bluesky_ipython.log`.
3. Explain errors in plain language.  During live user support, do not change codebases.  Only
   suggest changes to user scripts or command sequences.  Record needed codebase fixes for later
   development with a beamline scientist.
4. Users want to make the most of every second of beamtime, but safety is always the highest
   priority, even when it costs time or data.  Do not cut corners.
5. Direct users to call the lead beamline scientist for anything not straightforward to answer:
   Eliot Gann, x4225 from the beamline phone.

Quick log-tail commands:

```bash
tail -n 80 /home/xf12id/.cache/bluesky/log/bluesky.log
tail -n 80 /home/xf12id/.cache/bluesky/log/bluesky_ipython.log
```

If the logs indicate device faults, failed moves, beamline safety state, ambiguous shutter/
attenuator/beamstop status, or any inconsistency between user intent and hardware state, stop and
recommend calling Eliot before proceeding.

## Beamline-User Signals

Treat the user as a likely live beamline user when either of these is true:

1. The active workstation is `xf12id2-ws1`.
2. The current Redis proposal in db=0 is a user proposal, not commissioning.

Current verification from this session:

```text
hostname -> xf12id2-ws1.nsls2.bnl.local
Redis db=0 proposal.type -> Beamline Commissioning (beamline staff only)
Redis db=0 proposal.proposal_id -> 321164
Redis db=0 data_session -> pass-321164
Redis db=0 cycle -> commissioning
```

Because the current proposal type is commissioning, the Redis proposal-type signal alone does not
indicate a user-proposal session right now.  The workstation signal still identifies this as a live
SMI beamline workstation.

## Redis db=0 Read-Only Check

Only perform this Redis check from `xf12id2-ws1` or another explicitly approved beamline host.
Use Redis db=0 read-only.  Never write to this DB from an assistant workflow.

Quick command from `~/.ipython/profile_collection`:

```bash
pixi run -e terminal python - <<'PY'
import json
from pathlib import Path
import redis

host = "xf12id2-smi-redis1.nsls2.bnl.gov"
password = Path("/etc/bluesky/redis.secret").read_text().strip()
r = redis.Redis(
    host,
    db=0,
    ssl=True,
    port=6380,
    password=password,
    socket_timeout=2,
    socket_connect_timeout=2,
    decode_responses=True,
)

proposal = json.loads(r.get("proposal"))
print("cycle:", json.loads(r.get("cycle")))
print("data_session:", json.loads(r.get("data_session")))
print("proposal_id:", proposal.get("proposal_id"))
print("proposal_type:", proposal.get("type"))
print("pi_name:", proposal.get("pi_name"))
PY
```

Expected keys in db=0:

```text
proposal       JSON string with proposal_id, title, type, pi_name
cycle          JSON string
data_session   JSON string
beamline_name  JSON string
```

Classification rule under review:

```text
If hostname starts with xf12id2-ws1, this is a live SMI workstation context.
If proposal.type does not contain "Commissioning", this is likely a user-proposal beamline session.
If proposal.type contains "Commissioning", assume staff/commissioning context unless the user says otherwise.
```

## How This Should Affect Answers

When classified as a live beamline operational question:

```text
Give bsui/IPython commands first.
Use live profile object names from profile-collection when known.
Prefer read-only checks before moves.
For beamstop, attenuation, detector, and motor moves, include the restoration step.
Do not suggest Redis writes unless explicitly asked and the key/db is approved.
If checking Redis proposal metadata, db=0 is read-only only.
```

## Open Questions For Review

```text
Should commissioning sessions be treated as beamline-user questions when the speaker is staff?
Should proposal.type matching be exact or substring-based?
Should the assistant run the Redis check automatically at session start, or only when a question looks operational?
Should Redis output include PI/title, or only proposal_id/type to avoid over-sharing?
```
