"""Reusable grazing energy scans and explicit, recorded-point continuation.

Specifications and coverage are pure Python. Hardware is supplied explicitly in
``GrazingContext``; alignment and harmonic policy remain profile responsibilities.
See docs/GRAZING_WORKFLOWS.md for the public contract and runnable examples.
"""

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field

from ._holder import select_samples
from ._lists import resolve_list


def _numbers(values, label, *, positive=False):
    result = tuple(float(v) for v in values)
    if not result or not all(math.isfinite(v) and (not positive or v > 0) for v in result):
        raise ValueError(f"{label} must be a nonempty finite sequence")
    if len(set(result)) != len(result):
        raise ValueError(f"{label} contains duplicate values")
    return result


@dataclass(frozen=True)
class GrazingScan:
    """Materialized experiment intent; no database/device access on construction.

    ``edges`` is a sequence of (label, energies_eV) pairs. ``fast_axis='energy'``
    holds theta for an entire energy list; ``'incidence'`` visits all angles at
    each energy. Each outer-axis value opens a separate run. Checks (when enabled)
    visit all angles at the first edge's first energy, before/after all edges at
    each arc. Samples are always outermost, then arcs, then edges.
    """

    edges: tuple
    angles: tuple
    arcs: tuple = (20.0,)
    fast_axis: str = "energy"
    theta_axis: str = "stage_theta"
    exposure: float = 1.0
    settle: float = 2.0
    attenuation_factor: float = 10.0
    damage_checks: bool = True
    theta_tolerance: float = 0.002
    harmonic: object = None
    name_tokens: tuple = ("energy{energy_set}", "ai{incident_angle}", "wa{scan_arc}")

    def __post_init__(self):
        edges = tuple((str(label), _numbers(values, "energies", positive=True)) for label, values in self.edges)
        if not edges or any(not label for label, _ in edges) or len({e[0] for e in edges}) != len(edges):
            raise ValueError("Edges need unique nonempty labels")
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "angles", _numbers(self.angles, "angles"))
        object.__setattr__(self, "arcs", _numbers(self.arcs, "arcs"))
        object.__setattr__(self, "name_tokens", tuple(self.name_tokens))
        if self.fast_axis not in ("energy", "incidence"):
            raise ValueError("fast_axis must be 'energy' or 'incidence'")
        for key in ("exposure", "settle", "attenuation_factor", "theta_tolerance"):
            value = float(getattr(self, key))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
            object.__setattr__(self, key, value)
        if self.exposure == 0 or self.attenuation_factor < 1 or self.theta_tolerance == 0:
            raise ValueError("Need exposure/tolerance > 0 and attenuation_factor >= 1")
        if self.harmonic is not None:
            h = int(self.harmonic)
            if h != self.harmonic or h < 1 or h % 2 != 1:
                raise ValueError("harmonic must be a positive odd integer")
        if not self.theta_axis:
            raise ValueError("theta_axis must name a Position field")

    @classmethod
    def from_lists(cls, edges, angles, *, store=None, **kwargs):
        """Resolve {edge_label: named_list_or_values} once, freezing list contents."""
        return cls(tuple((label, resolve_list(values, kind="energy", store=store))
                         for label, values in edges.items()), tuple(angles), **kwargs)

    def to_dict(self):
        return json.loads(json.dumps(self.__dict__))

    @classmethod
    def from_dict(cls, value):
        return cls(**value)

    @property
    def fingerprint(self):
        # Loop order is intentionally changeable at recovery. All acquisition
        # settings and original ordered lists remain part of the identity.
        data = self.to_dict()
        data.pop("fast_axis")
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    @property
    def energy_range(self):
        values = [v for _, energies in self.edges for v in energies]
        return min(values), max(values)

    def points(self):
        """All stable point identities in acquisition order (independent of samples)."""
        for arc_index, arc in enumerate(self.arcs):
            for phase in (("before", "main", "after") if self.damage_checks else ("main",)):
                if phase != "main":
                    indices = range(len(self.angles)) if phase == "before" else reversed(range(len(self.angles)))
                    for ai in indices:
                        yield ScanPoint(arc_index, arc, phase, -1, "check", ai, self.angles[ai],
                                        0, self.edges[0][1][0])
                else:
                    for ei, (label, energies) in enumerate(self.edges):
                        pairs = ((ai, vi) for ai in range(len(self.angles)) for vi in range(len(energies)))
                        if self.fast_axis == "incidence":
                            pairs = ((ai, vi) for vi in range(len(energies)) for ai in range(len(self.angles)))
                        for ai, vi in pairs:
                            yield ScanPoint(arc_index, arc, phase, ei, label, ai, self.angles[ai], vi, energies[vi])

    def preview(self, samples):
        samples = select_samples(samples)
        points = list(self.points())
        return {"sample_names": [s.name for s in samples], "sample_ids": [s.id for s in samples],
                "events_per_sample": len(points), "events": len(points) * len(samples),
                "runs_per_sample": len(_groups(points, self.fast_axis)),
                "energy_range_eV": list(self.energy_range), "fast_axis": self.fast_axis,
                "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class ScanPoint:
    arc_index: int
    arc: float
    phase: str
    edge_index: int
    edge: str
    angle_index: int
    angle: float
    energy_index: int
    energy: float

    @property
    def id(self):
        return f"{self.arc_index}:{self.phase}:{self.edge_index}:{self.angle_index}:{self.energy_index}"


def _groups(points, fast_axis):
    groups = []
    previous = None
    for p in points:
        outer = p.angle_index if fast_axis == "energy" else p.energy_index
        key = (p.arc_index, p.phase, p.edge_index, outer if p.phase == "main" else None)
        if key != previous:
            groups.append([])
            previous = key
        groups[-1].append(p)
    return groups


@dataclass
class AlignmentState:
    """Recorded aligned position; theta_zero is absolute, angles are relative.

    Only coordinates present in ``position`` are restored. No nominal-position
    move or re-alignment occurs when explicitly reusing this state.
    """

    sample_id: str
    theta_axis: str
    theta_zero: float
    position: dict = field(default_factory=dict)

    def __post_init__(self):
        self.theta_zero = float(self.theta_zero)
        self.position = {k: float(v) for k, v in self.position.items()}
        if not all(math.isfinite(v) for v in [self.theta_zero] + list(self.position.values())):
            raise ValueError("Alignment contains nonfinite coordinates")
        self.position[self.theta_axis] = self.theta_zero

    def to_dict(self):
        return {"sample_id": self.sample_id, "theta_axis": self.theta_axis,
                "theta_zero": self.theta_zero, "position": dict(self.position)}


@dataclass
class ScanCoverage:
    """Explicit evidence of saved events, not a motor-position-based restart guess.

    Use only runs from one batch and specification. Flagged events still count
    as exposed and are skipped by default. JSON round-trippable via to_dict.
    """

    fingerprint: str
    batch_id: str
    completed: dict = field(default_factory=dict)
    alignments: dict = field(default_factory=dict)
    source_uids: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    harmonic: object = None
    foil_states: dict = field(default_factory=dict)

    def add_run(self, start, events, *, stop):
        # Validate against a copy so malformed/incomplete data cannot leave partly
        # accepted coverage behind when an exception is raised.
        candidate = self.from_dict(self.to_dict())
        candidate._add_run(start, events, stop=stop)
        self.__dict__.update(candidate.__dict__)

    def _add_run(self, start, events, *, stop):
        if not stop:
            raise ValueError("Cannot recover from an open run; stop it and refresh the data")
        info = start["grazing_scan"]
        if info.get("schema_version") != 1:
            raise ValueError("Unsupported grazing_scan metadata version")
        if GrazingScan.from_dict(info["spec"]).fingerprint != info["fingerprint"]:
            raise ValueError("Recorded specification does not match its fingerprint")
        if stop.get("run_start") != start["uid"]:
            raise ValueError("Stop document belongs to a different run")
        if info["fingerprint"] != self.fingerprint or info["batch_id"] != self.batch_id:
            raise ValueError("Mixed scan specifications or batches in recovery input")
        sid = start["sample_id"]
        state = info["alignment"]
        if state["sample_id"] != sid:
            raise ValueError("Alignment belongs to a different sample")
        if sid in self.alignments and self.alignments[sid] != state:
            raise ValueError(f"Conflicting alignments for sample {sid}")
        self.alignments[sid] = state
        foils = info.get("attenuation_inserted")
        if foils is not None:
            if sid in self.foil_states and self.foil_states[sid] != foils:
                raise ValueError("Foils changed between runs of the same sample")
            self.foil_states[sid] = foils
        h = start.get("harmonic_lock", {}).get("harmonic")
        if h is not None:
            if self.harmonic is not None and h != self.harmonic:
                raise ValueError("Mixed harmonics in recovery input")
            self.harmonic = h
        known = self.completed.setdefault(sid, [])
        expected = set(info["point_ids"])
        count = 0
        for event in events:
            data = event.get("data", event)
            pid = str(data["scan_point_id"])
            if pid not in expected:
                raise ValueError("Recorded point not in the run's planned point list")
            if pid not in known:
                known.append(pid)
            count += 1
            theta_key = info["theta_readback_key"]
            error = float(data[theta_key]) - (state["theta_zero"] + float(data["incident_angle"]))
            if not math.isfinite(error) or abs(error) > info["spec"]["theta_tolerance"]:
                self.flags.append({"uid": start["uid"], "point_id": pid,
                                   "theta_error": error if math.isfinite(error) else None})
        saved = stop.get("num_events", {}).get("primary", 0)
        if count != saved:
            raise ValueError("Primary event count differs from stop document; refresh incomplete ingestion")
        if start["uid"] not in self.source_uids:
            self.source_uids.append(start["uid"])

    def pending(self, spec, sample_id):
        if spec.fingerprint != self.fingerprint:
            raise ValueError("Scan settings changed; only fast_axis may change during continuation")
        known = set(self.completed.get(sample_id, []))
        valid = {p.id for p in spec.points()}
        if not known <= valid:
            raise ValueError("Coverage contains unknown point IDs")
        return [p for p in spec.points() if p.id not in known]

    def to_dict(self):
        return json.loads(json.dumps(self.__dict__, allow_nan=False))

    @classmethod
    def from_dict(cls, value):
        return cls(**json.loads(json.dumps(value)))


def coverage_from_tiled(runs):
    """Read only scalar event columns from explicitly selected closed BlueskyRuns.

    No implicit latest-run search, client construction, authentication or writes.
    Accepts Tiled BlueskyRun objects, not legacy databroker Headers. Older ad hoc
    runs without grazing_scan metadata require a reviewed one-off conversion.
    """
    coverage = None
    for run in runs:
        start = dict(run.metadata["start"])
        if "grazing_scan" not in start:
            continue  # alignment runs have their own metadata/streams
        info = start["grazing_scan"]
        if coverage is None:
            coverage = ScanCoverage(info["fingerprint"], info["batch_id"])
        stop = run.metadata.get("stop")
        if not stop:
            raise ValueError("Cannot recover from an open run")
        events = []
        if "primary" in run:
            fields = ["scan_point_id", "incident_angle", info["theta_readback_key"]]
            arrays = {key: run["primary"]["data"][key].read() for key in fields}
            lengths = {len(v) for v in arrays.values()}
            if len(lengths) != 1:
                raise ValueError("Inconsistent scalar array lengths; refresh Tiled")
            events = [{key: values[i] for key, values in arrays.items()}
                      for i in range(len(arrays["scan_point_id"]))]
        coverage.add_run(start, events, stop=stop)
    if coverage is None:
        raise ValueError("No grazing_scan measurement runs supplied")
    return coverage


@dataclass
class GrazingContext:
    """Explicit live-device adapter, shared by users, GUI workers, and simulations.

    motors maps Position field names to motors; include all coordinates to restore
    after an interruption. align(sample) and exposure(seconds, seconds) are plans.
    harmonic_lock(plan, start=..., stop=..., harmonic=...) is the profile wrapper.
    detectors() is called only AFTER an arc move. No direct hardware imports.
    """

    energy: object
    arc: object
    motors: dict
    detectors: object
    exposure: object
    harmonic_lock: object
    align: object = None
    attenuation: object = None
    reads: tuple = ()

    @classmethod
    def from_namespace(cls, namespace, *, align):
        """Bind a configured SMI session explicitly, without importing live hardware.

        ``align`` is still a caller-supplied callable(sample)->plan. The profile's
        convenience with_harmonic_lock is used (it already supplies energy).
        """
        from ._core import saxs_waxs_dets

        p, st = namespace["piezo"], namespace["stage"]
        motors = {"piezo_" + key: getattr(p, key) for key in ("x", "y", "z", "th")}
        motors.update({"stage_" + key: getattr(st, key) for key in ("x", "y", "z", "theta", "chi", "phi")})

        def detectors():
            return saxs_waxs_dets(saxs_det=namespace["pil2M"], waxs_det=namespace["pil900KW"],
                                  waxs_arc_dev=namespace["waxs"])

        return cls(namespace["energy"], namespace["waxs"].arc, motors, detectors,
                   namespace["det_exposure_time"], namespace["with_harmonic_lock"],
                   align=align, attenuation=namespace["attenuation"],
                   reads=tuple(namespace[k] for k in ("xbpm2", "xbpm3", "pin_diode") if k in namespace))


def capture_alignment(sample, context, theta_axis, *, tolerance=0.002):
    """Plan: verify final theta target, then capture aligned coordinate setpoints."""
    import bluesky.plan_stubs as bps

    motor = context.motors[theta_axis]
    actual = float((yield from bps.rd(motor)))
    target_signal = getattr(motor, "setpoint", None)
    if target_signal is None:
        target_signal = getattr(motor, "user_setpoint", None)
    target = actual if target_signal is None else float((yield from bps.rd(target_signal)))
    if not math.isfinite(actual) or not math.isfinite(target) or abs(actual - target) > tolerance:
        raise RuntimeError(f"Alignment theta did not reach target: {actual} versus {target}")
    position = {}
    for key, device in context.motors.items():
        signal = getattr(device, "setpoint", None)
        if signal is None:
            signal = getattr(device, "user_setpoint", device)
        position[key] = float((yield from bps.rd(signal)))
    return AlignmentState(sample.id, theta_axis, actual, position)


def _move_position(position, context):
    import bluesky.plan_stubs as bps

    args = []
    for key, value in position.items():
        if value is not None:
            if key not in context.motors:
                raise ValueError(f"No motor supplied for recorded coordinate {key}")
            motor = context.motors[key]
            if not math.isfinite(float(value)):
                raise ValueError(f"Nonfinite position for {key}")
            motor.check_value(value)
            args.extend([motor, value])
    if args:
        yield from bps.mv(*args)


def grazing_scan(samples, spec, context, *, coverage=None, reuse_alignment=None,
                 batch_id=None, md=None):
    """Plan: align/configure/measure each selected sample, with stable event IDs.

    Fresh scans require context.align or explicit AlignmentState objects keyed by
    sample ID. Continuation requires coverage and explicit reuse_alignment for
    every started, unfinished sample (usually coverage.alignments converted to
    AlignmentState). Never silently realign an already exposed spot. New samples
    are positioned/aligned normally. Completed samples are not moved to.
    """
    import bluesky.plan_stubs as bps
    from ophyd import Signal

    from ._compose import ScanAxis, acquire, energy_axis, move_energy_fb

    samples = select_samples(samples)
    reuse_alignment = dict(reuse_alignment or {})
    if spec.theta_axis not in context.motors:
        raise ValueError("theta_axis is missing from context.motors")
    if coverage is not None and batch_id is not None and batch_id != coverage.batch_id:
        raise ValueError("Continuation must retain its batch ID")
    batch_id = coverage.batch_id if coverage else (batch_id or uuid.uuid4().hex)
    work = []
    for sample in samples:
        points = coverage.pending(spec, sample.id) if coverage else list(spec.points())
        if not points:
            continue
        state = reuse_alignment[sample.id] if sample.id in reuse_alignment else None  # noqa: SIM401 - purity guard distinguishes device .get
        if state is not None and (state.sample_id != sample.id or state.theta_axis != spec.theta_axis):
            raise ValueError("Reused alignment does not match sample/theta axis")
        if (coverage and sample.id in coverage.alignments
                and (state is None or state.to_dict() != coverage.alignments[sample.id])):
            raise ValueError("Explicit original aligned state required for continued sample")
        if state is None and context.align is None:
            raise ValueError("Supply an alignment plan or explicit aligned state")
        work.append((sample, points, state))
    if not work:
        return []
    if context.harmonic_lock is None:
        raise ValueError("Supply the profile harmonic-lock wrapper")
    if context.attenuation is None:
        raise ValueError("Supply the attenuation device to establish measurement foils")
    # Check all planned coordinates before beginning this batch. Full coupled
    # motor feasibility remains the device's responsibility at move time.
    for sample, _, state in work:
        position = state.position if state else sample.runnable_position().to_dict()
        for key, value in position.items():
            if key in ("frame", "incident_angles") or value is None:
                continue
            if key not in context.motors:
                raise ValueError(f"No motor supplied for recorded coordinate {key}")
            if not math.isfinite(float(value)):
                raise ValueError(f"Nonfinite coordinate for {sample.name}")
            context.motors[key].check_value(value)

    def body():
        uids = []
        theta = context.motors[spec.theta_axis]
        if spec.settle:
            yield from bps.sleep(spec.settle)  # settle after the wrapper establishes the initial energy
        for sample, points, state in work:
            yield from bps.mv(context.arc, points[0].arc)
            reference = spec.edges[0][1][0]
            yield from move_energy_fb(reference, settle=spec.settle, device=context.energy)
            if state is None:
                pos = sample.runnable_position().to_dict()
                coordinates = {k: v for k, v in pos.items() if k not in ("frame", "incident_angles") and v is not None}
                yield from _move_position(coordinates, context)
                yield from context.align(sample)
                state = yield from capture_alignment(sample, context, spec.theta_axis, tolerance=spec.theta_tolerance)
            else:
                yield from _move_position(state.position, context)
            # Alignment may have changed exposure, energy, foils and detector arc.
            yield from context.exposure(spec.exposure, spec.exposure)
            yield from move_energy_fb(reference, settle=spec.settle, device=context.energy)
            yield from bps.mv(context.attenuation, spec.attenuation_factor)
            # Record exact foil identity when the adapter exposes it. Re-selecting
            # the same factor after a calibration change need not select the same foils.
            inserted = getattr(context.attenuation, "inserted", None)
            foil_state = None
            if inserted is not None:
                yield from bps.read(context.attenuation)
                foil_state = str((yield from bps.rd(inserted)))
            if coverage and sample.id in coverage.foil_states and foil_state != coverage.foil_states[sample.id]:
                raise RuntimeError("Attenuator foils differ from the original sample measurement")
            for group in _groups(points, spec.fast_axis):
                first = group[0]
                yield from bps.mv(context.arc, first.arc)
                detectors = list(context.detectors())
                ai = Signal(name="incident_angle", value=first.angle)
                pid = Signal(name="scan_point_id", value="")
                arc_signal = Signal(name="scan_arc", value=first.arc)
                e_axis = energy_axis([], settle=spec.settle, device=context.energy)
                last_angle = [None]

                def move_point(index, group=group, last_angle=last_angle, state=state,
                               ai=ai, e_axis=e_axis, pid=pid):
                    point = group[index]
                    if last_angle[0] != point.angle:
                        yield from bps.mv(theta, state.theta_zero + point.angle, ai, point.angle)
                        last_angle[0] = point.angle
                    yield from e_axis.move_to(point.energy)
                    actual = float((yield from bps.rd(theta)))
                    if not math.isfinite(actual) or abs(actual - state.theta_zero - point.angle) > spec.theta_tolerance:
                        raise RuntimeError("Theta did not reach requested angle; stopping before exposure")
                    yield from bps.mv(pid, point.id)

                axis = ScanAxis("scan_point", range(len(group)), move=move_point,
                                reads=[ai, pid, arc_signal, e_axis.record])
                info = {"schema_version": 1, "batch_id": batch_id, "fingerprint": spec.fingerprint,
                        "sample_order": [s.id for s in samples],
                        "spec": spec.to_dict(), "phase": first.phase, "edge": first.edge,
                        "arc": first.arc, "point_ids": [p.id for p in group],
                        "alignment": state.to_dict(), "theta_readback_key": theta.name,
                        "attenuation_inserted": foil_state,
                        "continuation_of": coverage.source_uids if coverage else []}
                # theta.name must be a readable key, not merely an object label.
                if theta.name not in theta.describe():
                    raise ValueError("Theta device must expose its name as a scalar readback key")
                phase_name = "energy_scan" if first.phase == "main" else "beam_damage_check_" + first.phase
                reads = list(context.reads) + [context.energy, context.arc, context.attenuation] + list(context.motors.values())
                run_md = dict(sample.md)
                run_md.update(md or {})
                run_md.update(sample_id=sample.id, holder_id=sample.holder_id, grazing_scan=info)
                uid = yield from acquire(
                    f"{sample.name}_{first.edge}_{phase_name}", detectors, [axis],
                    reads=reads, geometry="reflection", scan_name=phase_name,
                    name_tokens=list(spec.name_tokens), md=run_md,
                )
                uids.append(uid)
        return uids

    harmonic = coverage.harmonic if coverage and coverage.harmonic is not None else spec.harmonic
    return (yield from context.harmonic_lock(body(), start=spec.energy_range[0],
                                           stop=spec.energy_range[1], harmonic=harmonic))
