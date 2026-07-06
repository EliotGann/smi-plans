# Beamline Question Log

This file records user questions from the 2026-07-06 SMI beamline support session.  It is intended
as source material for a future strict skill that recognizes when the user is asking an operational
question and answers with the beamline-approved pattern.

For each question, fill in the approved response after review by the lead beamline scientist.

## Draft Core Principles For Beamline-User Answers

```text
1. Never suggest something untested. Suggest only developed strategies using vetted
   profile-collection or smi-plans code. Code snippets for user scripts must follow good Bluesky
   plan practice.
2. Always consult the tails of:
   /home/xf12id/.cache/bluesky/log/bluesky.log
   /home/xf12id/.cache/bluesky/log/bluesky_ipython.log
   before diagnosing errors or recent command state.
3. Explain errors in plain language. Do not change codebases during live user support; only user
   scripts/commands. Record codebase fixes for later development with a beamline scientist.
4. Beamtime is precious, but safety is highest priority even at the cost of time or data. No
   cutting corners.
5. If anything is not straightforward, direct users to call Eliot Gann, x4225 from the beamline
   phone.
```

## PF And Live Analysis

### Negative Derivative Peaks

User question or request:

```text
in the pf function when we are taking the derivative, we want to allow for negative peaks to be fit
```

Context:

```text
pf(..., der=True) should fit falling-edge derivative troughs, not only positive peaks.
```

Approved answer pattern:

```text
TODO
```

### Missing Motor Column In pf

User question or report:

```text
SMI 320090 bdm1 2026-07-04 10:53:02 [96]: pf(der=True)
An exception has occurred, use '%tb verbose' to see the full traceback.
KeyError: "motor column 'bdm_y' not in the scan table. Available numeric columns: [..., 'bdm_y_done', 'bdm_y_setpoint', 'bdm_y_readback', 'bdm_y_stop_signal']"
```

Context:

```text
Scan metadata named motor bdm_y, but the table stored bdm_y_readback and bdm_y_setpoint.
```

Approved answer pattern:

```text
TODO
```

### Live pf Callback

User question:

```text
would it be possible to have a sort of live version of pf that can be subscribed to a scan, and it updates a plot and fit live as each point comes in.  this wouldn't be going through tiled, but looking at the documents live as they come out, like bec a bit, but doing the pf type of output through redis?
```

Approved answer pattern:

```text
TODO
```

### Callback Without Subscribing

User question:

```text
can we write that callback, but not subscribe it?  i think we can subscribe by just putting RE(plan(),subforthisplan) right?  this would be great for testing
```

Approved answer pattern:

```text
TODO
```

### Get Derivative Instead Of Raw Intensity

User question:

```text
how do i get ps with the derivative instead of raw intensity
```

Context:

```text
User likely means pf/ps-style analysis of derivative signal for edge alignment.
```

Approved answer pattern:

```text
TODO
```

## Pixi And Deployment

### Confirm Push

User question:

```text
are you sure this is pushed?
```

Approved answer pattern:

```text
TODO
```

### Check pixi Update Pickup

User question:

```text
in profile-collection, I need to pixi update, how do I check that it's picking this up?
```

Approved answer pattern:

```text
TODO
```

### Check Version In Lock File

User question:

```text
those commands don't work, the update does, is there a way to check what the version is in the lock file?
```

Approved answer pattern:

```text
TODO
```

### pixi Environment Has No Default Python

User clarification:

```text
sorry, it does not have a python that I can run SMI [xf12id@xf12id2-ws1 profile_collection]$ pixi run python -c "import smi_plans.analysis as a; print(a.__file__); print(hasattr(a, 'LivePF'))"
python: command not found

Available tasks:
	beam-down
	beam-status
	beam-up
	pvs
	qs-backend
	qs-list
	qs-server
	qserver-gui
	start
	start-beamdown
	test
	test-hardware
	test-iocs
	test-sim
	test-unit
SMI [xf12id@xf12id2-ws1 profile_collection]$ pixi list
Error:   × No packages found in 'default' environment for 'linux-64' platform.
```

Approved answer pattern:

```text
TODO
```

### Force Latest smi-plans In pixi

User question:

```text
what is the pixi update command to force the latest smi-plans?
```

Approved answer pattern:

```text
TODO
```

## Ramp, Fly, And Counting Plans

### Ramp Plan Existence

User question:

```text
is there a ramp plan?  exposing while moving a motor at a constant speed?
```

Approved answer pattern:

```text
TODO
```

### Build Ramp Plan

User question:

```text
yeah, can we build that?
```

Approved answer pattern:

```text
TODO
```

### Single Exposure During stage.phi Ramp

User question:

```text
so if we want to move stage.phi from -10 to 10 at constant velocity, and expose the pilatus 2M for the whole movement in one exposure, what will be the command for that?
```

Approved answer pattern:

```text
TODO
```

### Pseudo Motor Has No Velocity Signal

User question or report:

```text
ValueError: velocity was supplied, but motor 'stage_phi' has no .velocity signal  can we just use the current velocity?  phi is a pseudo motor, so velocities don't really matter. can we move the bare motor?
```

Approved answer pattern:

```text
TODO
```

### Count Number Argument

User question:

```text
RE(bp.count([pil2M]), )
 how do i put number=1000
```

Approved answer pattern:

```text
TODO
```

### Stop Experiment

User question:

```text
stopping an experiment ctrl c and then?
```

Approved answer pattern:

```text
TODO
```

## Stage And Geometry

### stage.phi Center Of Rotation

User question:

```text
stage phi center of rotation is not perfectly aligned on the sample. how to change?
```

Approved answer pattern:

```text
TODO
```

### Detector Vertical Position

User questions:

```text
how to change the pil2m position vertically?
```

```text
detector position y
```

Approved answer pattern:

```text
TODO
```

## Beamstop, Absorbers, And Attenuation

### Measure With Absorber Without Beamstop

User question:

```text
i want to measure with absorber but without beam stop how to move out the beam stop and then back in?
```

Approved answer pattern:

```text
TODO
```

### GISAXS Beamstop Context

User clarification:

```text
but we have gisaxs rightnow
```

Approved answer pattern:

```text
TODO
```

### Absorber Not Inserted

User clarification:

```text
but it did not put the absorber now
```

Approved answer pattern:

```text
TODO
```

### Absorber Status

User question:

```text
how to get the status of all the absorbers?
```

User follow-up output:

```text
att2_9 Not Open
att2_10 Not Open
att2_11 Not Open
att2_12 Not Open
```

Approved answer pattern:

```text
TODO
```

### Move Attenuation 100

User question:

```text
move attenuation 100

how to do that
```

Approved answer pattern:

```text
TODO
```

## Error Diagnosis

### FailedStatus On piezo_ch

User question or report:

```text
[... skipping similar frames: plan_mutator at line 213 (4 times)]

File ~/.cache/rattler/cache/envs/smi-profile-collection-12026695555121049507/envs/terminal/lib/python3.12/site-packages/bluesky/preprocessors.py:213, in plan_mutator(...)
...
FailedStatus: MoveStatus(done=True, pos=piezo_ch, elapsed=1.8, success=False, settle_time=0.0)

SMI 316021 explore_CDGISAXS 2026-07-05 17:34:03 [102]: %tb verbose  what is happening
```

Approved answer pattern:

```text
TODO
```

## Skill Design Notes

Questions in this session were often short operational commands or fragments rather than complete
sentences.  The future skill should treat these as questions when they mention live beamline
objects, Bluesky commands, profile-collection/Pixi operations, errors, or immediate procedural
needs.

Candidate intent categories:

```text
Operational command syntax
Beamline safety and restoration sequence
Live error diagnosis
Deployment/version verification
Plan/callback design and testing
Device position/status lookup
```

Candidate answer requirements:

```text
Give exact bsui/IPython commands first.
Use the live profile object names when known.
State safety/restoration steps explicitly when moving beamstop, attenuation, or motors.
Distinguish software-timed plans from hardware-synchronized fly scans.
When diagnosing tracebacks, identify the failing device/status line before suggesting commands.
Avoid speculative device names unless verified from profile-collection.
```

## Beamline-User Detection Notes

The initial triage procedure is documented in:

```text
skills/beamline-question-triage.md
```

Verified during this session:

```text
hostname: xf12id2-ws1.nsls2.bnl.local
Redis db=0 proposal key was readable from this workstation.
Current proposal.type: Beamline Commissioning (beamline staff only)
Current proposal_id: 321164
Current data_session: pass-321164
```

Rule under review:

```text
xf12id2-ws1 hostname is a live beamline-workstation signal.
Redis db=0 proposal.type not containing "Commissioning" is a user-proposal signal.
Redis db=0 must only be read by the assistant, never written.
```
