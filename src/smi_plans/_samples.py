"""
smi_plans._samples
==================

A small, typed, **GUI-serializable** sample data model for SMI-SWAXS experiments.

Why
---
The legacy corpus represents a sample bar as a set of parallel lists guarded by asserts::

    names   = ['s1', 's2', 's3']
    x_piezo = [55000, 42000, 25000]
    y_piezo = [5000,  5000,  5000]
    ...
    assert len(x_piezo) == len(names)

This is error-prone (easy to misalign columns), not type-safe, and awkward to drive from a
GUI.  Here a sample is a :class:`Sample` dataclass and a bar is a :class:`SampleList`.  The
parallel-list form is still supported as a *constructor* (:meth:`SampleList.from_columns`) so
existing tables paste straight in, but everything downstream is structured.

Coordinate convention (SMI)
---------------------------
* ``piezo_x/y/z`` -- SmarAct fine stage (microns), ``piezo_th`` incident angle (deg).
* ``hexa_x/y/z``  -- hexapod coarse stage (mm), ``hexa_th`` incident angle (deg).
* ``incident_angles`` -- list of grazing angles (deg) to measure, *relative to aligned 0*.
* ``md`` -- free-form per-sample metadata merged into the run's ``md={}`` (e.g.
  ``{'project_name': ..., 'temperature_set': 35, 'thickness_nm': 40}``).  This is where
  "the user told me X" context lives until it is also recorded as a Signal at acquisition.

Nothing here imports bluesky/ophyd; this module is pure Python and safe to import anywhere
(including a GUI process).

Extended model (SAMPLE_SYSTEM_PLAN.md §2)
-----------------------------------------
On top of the legacy ``Sample``/``SampleList`` this module now also carries the typed,
JSON-serializable model the Redis db=2 ``SampleStore`` persists: :class:`Position`,
:class:`AlignmentResult`, :class:`SpotSummary`, :class:`ScanRecord`, :class:`HolderTransform`,
:class:`Holder`, :class:`Magazine`, plus the :func:`slot_to_position` helper.  Every type has
``to_dict()``/``from_dict()`` that round-trip through JSON (and therefore through
``RedisJSONDict``/orjson, where **sequences read back as lists** -- so nothing here relies on
tuples surviving).  Still pure Python: no bluesky/ophyd/redis imports.
"""

import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence


__all__ = [
    "Position",
    "AlignmentResult",
    "SpotSummary",
    "ScanRecord",
    "HolderTransform",
    "Holder",
    "Magazine",
    "Sample",
    "SampleList",
    "slot_to_position",
]


def _coerce_optional_float(v):
    return None if v is None else float(v)


def _now():
    return time.time()


# ---------------------------------------------------------------------------
# Position (§2.1) -- a named coordinate set
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """A named set of motor targets ("where to put the motors").

    Used three ways (SAMPLE_SYSTEM_PLAN.md §2.1): a sample's **nominal** position
    (holder-relative, ``frame="holder"``), its **refined** position (absolute,
    ``frame="lab"``, alignment-updated), and the **as-run** position captured in a
    :class:`ScanRecord`.  ``None`` means "do not move this axis".
    """

    # frame: "holder" (relative to the holder origin) or "lab" (absolute machine coords)
    frame: str = "lab"

    # SmarAct fine stage
    piezo_x: Optional[float] = None
    piezo_y: Optional[float] = None
    piezo_z: Optional[float] = None
    piezo_th: Optional[float] = None

    # Huber coarse stage (post-hexapod swap; see §8)
    stage_x: Optional[float] = None
    stage_y: Optional[float] = None
    stage_z: Optional[float] = None
    stage_theta: Optional[float] = None
    stage_chi: Optional[float] = None
    stage_phi: Optional[float] = None

    # per-sample grazing angles to measure (relative to aligned zero)
    incident_angles: List[float] = field(default_factory=list)

    def __post_init__(self):
        for attr in ("piezo_x", "piezo_y", "piezo_z", "piezo_th",
                     "stage_x", "stage_y", "stage_z",
                     "stage_theta", "stage_chi", "stage_phi"):
            setattr(self, attr, _coerce_optional_float(getattr(self, attr)))
        self.incident_angles = [float(a) for a in self.incident_angles]

    def to_dict(self):
        """JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        """Reconstruct a :class:`Position`, accepting legacy ``hexa_*`` aliases (§2.1).

        Today's CSVs/JSON may carry the old hexapod field names ``hexa_x/y/z/th``; these map
        onto the renamed Huber ``stage_x/y/z/theta`` so existing data loads unchanged.
        Explicit ``stage_*`` keys win over the aliases.
        """
        d = dict(d or {})
        # Back-compat reader: hexa_* -> stage_* (only when the new key is absent).
        for legacy, new in (("hexa_x", "stage_x"), ("hexa_y", "stage_y"),
                            ("hexa_z", "stage_z"), ("hexa_th", "stage_theta")):
            if legacy in d and new not in d:
                d[new] = d[legacy]
            d.pop(legacy, None)
        return cls(
            frame=d.get("frame", "lab"),
            piezo_x=d.get("piezo_x"),
            piezo_y=d.get("piezo_y"),
            piezo_z=d.get("piezo_z"),
            piezo_th=d.get("piezo_th"),
            stage_x=d.get("stage_x"),
            stage_y=d.get("stage_y"),
            stage_z=d.get("stage_z"),
            stage_theta=d.get("stage_theta"),
            stage_chi=d.get("stage_chi"),
            stage_phi=d.get("stage_phi"),
            incident_angles=list(d.get("incident_angles", []) or []),
        )

    def is_empty(self):
        """True if no coordinate is set (only the default ``frame``/empty angles)."""
        coords = (self.piezo_x, self.piezo_y, self.piezo_z, self.piezo_th,
                  self.stage_x, self.stage_y, self.stage_z,
                  self.stage_theta, self.stage_chi, self.stage_phi)
        return all(c is None for c in coords) and not self.incident_angles


# ---------------------------------------------------------------------------
# AlignmentResult (§2.2) -- what an alignment produced (D5)
# ---------------------------------------------------------------------------
@dataclass
class AlignmentResult:
    """The outcome of a named alignment routine (SAMPLE_SYSTEM_PLAN.md §2.2)."""

    code: str = ""                  # registry name, e.g. "gisaxs_hex" (D6)
    status: str = "ok"             # "ok" | "failed" | "skipped"
    when: float = field(default_factory=_now)   # epoch seconds
    refined: Position = field(default_factory=Position)   # absolute converged position
    params: Dict[str, Any] = field(default_factory=dict)   # inputs (angle, range, ...)
    fit: Dict[str, float] = field(default_factory=dict)    # th_found, y_found, peak, ...
    run_uids: List[str] = field(default_factory=list)      # alignment scan(s) in Tiled
    notes: str = ""

    def to_dict(self):
        return {
            "code": self.code,
            "status": self.status,
            "when": self.when,
            "refined": self.refined.to_dict(),
            "params": dict(self.params),
            "fit": dict(self.fit),
            "run_uids": list(self.run_uids),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(
            code=d.get("code", ""),
            status=d.get("status", "ok"),
            when=d.get("when", 0.0),
            refined=Position.from_dict(d.get("refined", {})),
            params=dict(d.get("params", {}) or {}),
            fit=dict(d.get("fit", {}) or {}),
            run_uids=list(d.get("run_uids", []) or []),
            notes=d.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# SpotSummary (§7.2) -- the irradiated region
# ---------------------------------------------------------------------------
@dataclass
class SpotSummary:
    """The auto-derived "which parts of the sample saw beam" summary (§7.2)."""

    kind: str = "none"              # "none" | "point" | "points" | "bbox" | "grid"
    points: List[List[float]] = field(default_factory=list)   # [[x,y], ...] in sample frame
    bbox: Optional[List[float]] = None    # [xmin, ymin, xmax, ymax]
    count: int = 0                  # number of distinct irradiated spots
    motor_x: Optional[str] = None   # which motor was x (e.g. "piezo_x")
    motor_y: Optional[str] = None
    units: str = "um"

    def to_dict(self):
        return {
            "kind": self.kind,
            "points": [list(p) for p in self.points],
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "count": self.count,
            "motor_x": self.motor_x,
            "motor_y": self.motor_y,
            "units": self.units,
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        bbox = d.get("bbox")
        return cls(
            kind=d.get("kind", "none"),
            points=[list(p) for p in (d.get("points", []) or [])],
            bbox=list(bbox) if bbox is not None else None,
            count=int(d.get("count", 0) or 0),
            motor_x=d.get("motor_x"),
            motor_y=d.get("motor_y"),
            units=d.get("units", "um"),
        )


# ---------------------------------------------------------------------------
# ScanRecord (§2.3) -- one entry in a sample's history (D7)
# ---------------------------------------------------------------------------
@dataclass
class ScanRecord:
    """A compact, run-level history entry (SAMPLE_SYSTEM_PLAN.md §2.3).

    The full data is always in Tiled via ``run_uid``; this is the convenience cache the
    GUI/spreadsheet read.
    """

    run_uid: str = ""               # the Bluesky run (join into Tiled) -- primary key
    scan_name: str = ""             # e.g. "giwaxs_tempramp_energy_5loc"
    scan_type: str = "data"        # coarse class: "alignment" | "data" | "calibration"
    when: float = field(default_factory=_now)   # epoch seconds (run start)
    # --- the as-run conditions (recorded, not guessed) ---
    position: Position = field(default_factory=Position)   # where the sample was
    energy_eV: Optional[float] = None
    transmission: Optional[float] = None        # net beam transmission 0-1 (§7.3)
    attenuation_factor: Optional[float] = None  # 1/T >= 1 (§7.3)
    exposure_s: Optional[float] = None
    geometry: Optional[str] = None              # "reflection" | "transmission"
    detectors: List[str] = field(default_factory=list)
    # --- the dose map (D13 / §7) ---
    spots: SpotSummary = field(default_factory=SpotSummary)
    # --- analysis tie-in ---
    result: Dict[str, Any] = field(default_factory=dict)    # alignment/analysis result
    md: Dict[str, Any] = field(default_factory=dict)        # extra intent snapshot

    def to_dict(self):
        return {
            "run_uid": self.run_uid,
            "scan_name": self.scan_name,
            "scan_type": self.scan_type,
            "when": self.when,
            "position": self.position.to_dict(),
            "energy_eV": self.energy_eV,
            "transmission": self.transmission,
            "attenuation_factor": self.attenuation_factor,
            "exposure_s": self.exposure_s,
            "geometry": self.geometry,
            "detectors": list(self.detectors),
            "spots": self.spots.to_dict(),
            "result": dict(self.result),
            "md": dict(self.md),
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(
            run_uid=d.get("run_uid", ""),
            scan_name=d.get("scan_name", ""),
            scan_type=d.get("scan_type", "data"),
            when=d.get("when", 0.0),
            position=Position.from_dict(d.get("position", {})),
            energy_eV=d.get("energy_eV"),
            transmission=d.get("transmission"),
            attenuation_factor=d.get("attenuation_factor"),
            exposure_s=d.get("exposure_s"),
            geometry=d.get("geometry"),
            detectors=list(d.get("detectors", []) or []),
            spots=SpotSummary.from_dict(d.get("spots", {})),
            result=dict(d.get("result", {}) or {}),
            md=dict(d.get("md", {}) or {}),
        )


# ---------------------------------------------------------------------------
# HolderTransform (§2.5) -- holder-frame -> lab-frame placement (D4)
# ---------------------------------------------------------------------------
@dataclass
class HolderTransform:
    """A minimal rigid holder placement: offset + in-plane rotation + height (§2.5).

    Defined now, **fit later** (D4): enough to recompute every sample's absolute position from
    its holder-relative ``nominal``.  :meth:`apply` is implemented as pure math; it is the
    identity until ``status == "fit"``.
    """

    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    theta: float = 0.0              # in-plane rotation (deg)
    fiducial_uids: List[str] = field(default_factory=list)   # alignment runs that fit this
    when: Optional[float] = None
    status: str = "unset"           # "unset" | "fit" | "stale"

    def __post_init__(self):
        self.dx = float(self.dx)
        self.dy = float(self.dy)
        self.dz = float(self.dz)
        self.theta = float(self.theta)

    def apply(self, nominal):
        """Map a holder-frame ``nominal`` :class:`Position` to a lab-frame one.

        Identity (returns an equivalent ``frame="lab"`` copy unchanged) unless ``status ==
        "fit"``.  When fit: rotate the in-plane ``(stage_x, stage_y)`` by ``theta`` degrees
        about the origin, then add the ``(dx, dy, dz)`` offset to ``stage_x/y/z``.  Piezo axes
        and incident angles are left untouched.
        """
        # Start from a copy in lab coordinates (piezo + angles carried verbatim).
        out = Position.from_dict(nominal.to_dict())
        out.frame = "lab"
        if self.status != "fit":
            return out

        rad = math.radians(self.theta)
        cos_t = math.cos(rad)
        sin_t = math.sin(rad)
        sx = nominal.stage_x if nominal.stage_x is not None else 0.0
        sy = nominal.stage_y if nominal.stage_y is not None else 0.0
        # In-plane rotation about the holder origin.
        rx = cos_t * sx - sin_t * sy
        ry = sin_t * sx + cos_t * sy
        out.stage_x = rx + self.dx
        out.stage_y = ry + self.dy
        sz = nominal.stage_z if nominal.stage_z is not None else 0.0
        out.stage_z = sz + self.dz
        return out

    def to_dict(self):
        return {
            "dx": self.dx,
            "dy": self.dy,
            "dz": self.dz,
            "theta": self.theta,
            "fiducial_uids": list(self.fiducial_uids),
            "when": self.when,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(
            dx=d.get("dx", 0.0),
            dy=d.get("dy", 0.0),
            dz=d.get("dz", 0.0),
            theta=d.get("theta", 0.0),
            fiducial_uids=list(d.get("fiducial_uids", []) or []),
            when=d.get("when"),
            status=d.get("status", "unset"),
        )


# ---------------------------------------------------------------------------
# Holder (§2.5) -- a bar/plate carrying samples (D3)
# ---------------------------------------------------------------------------
@dataclass
class Holder:
    """A bar/plate/cell carrying samples (SAMPLE_SYSTEM_PLAN.md §2.5)."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""
    kind: str = "bar"               # "bar" | "plate" | "cell" | ...
    # magazine state machine (D3, D11)
    state: str = "in_magazine"      # "in_magazine" | "loading" | "at_measurement" | "unloading"
    magazine_slot: Optional[str] = None    # where it lives when racked
    # the holder origin transform (D4): holder-frame -> lab-frame; None/identity until fit.
    origin: Optional[HolderTransform] = None
    sample_ids: List[str] = field(default_factory=list)   # members (ordered)
    md: Dict[str, Any] = field(default_factory=dict)
    created: float = field(default_factory=_now)
    updated: float = field(default_factory=_now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "magazine_slot": self.magazine_slot,
            "origin": self.origin.to_dict() if self.origin is not None else None,
            "sample_ids": list(self.sample_ids),
            "md": dict(self.md),
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        origin = d.get("origin")
        return cls(
            id=d.get("id") or uuid.uuid4().hex,
            name=d.get("name", ""),
            kind=d.get("kind", "bar"),
            state=d.get("state", "in_magazine"),
            magazine_slot=d.get("magazine_slot"),
            origin=HolderTransform.from_dict(origin) if origin is not None else None,
            sample_ids=list(d.get("sample_ids", []) or []),
            md=dict(d.get("md", {}) or {}),
            created=d.get("created", _now()),
            updated=d.get("updated", _now()),
        )


# ---------------------------------------------------------------------------
# Magazine (§2.6) -- the set of holders + the active pointer (D3, D12)
# ---------------------------------------------------------------------------
@dataclass
class Magazine:
    """The set of holders, the one at the beam, and the active-sample pointer (§2.6)."""

    holder_ids: List[str] = field(default_factory=list)
    measurement_holder_id: Optional[str] = None   # which holder is AT the beam (one at a time)
    active_sample_id: Optional[str] = None         # the "loaded" sample (D11/D12)
    slots: Dict[str, Optional[str]] = field(default_factory=dict)   # magazine_slot -> holder_id

    def to_dict(self):
        return {
            "holder_ids": list(self.holder_ids),
            "measurement_holder_id": self.measurement_holder_id,
            "active_sample_id": self.active_sample_id,
            "slots": dict(self.slots),
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(
            holder_ids=list(d.get("holder_ids", []) or []),
            measurement_holder_id=d.get("measurement_holder_id"),
            active_sample_id=d.get("active_sample_id"),
            slots=dict(d.get("slots", {}) or {}),
        )


@dataclass
class Sample:
    """One physical sample / position on the bar (SAMPLE_SYSTEM_PLAN.md §2.4).

    Keeps today's ergonomics -- only ``name`` is required, the legacy flat ``piezo_*``/``hexa_*``
    coordinates and ``md`` still construct and drive ``piezo_moves()``/``hexa_moves()`` -- and
    **adds** the typed identity/holder/position/history model: a stable ``id``, ``holder_id``/
    ``slot`` linkage, the ``nominal``/``refined`` :class:`Position` pair (D1), ``alignments`` and
    ``history`` lists.  All new fields are additive, so every current call site and CSV keeps
    loading; coordinates left ``None`` mean "do not move this axis".
    """

    name: str

    # STABLE unique id (uuid4 hex by default) -- NEVER edited (D10).  Declared right after the
    # only required field so legacy ``Sample(name=...)`` construction is unchanged.
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # SmarAct fine stage (legacy flat coords; still drive piezo_moves()).
    piezo_x: Optional[float] = None
    piezo_y: Optional[float] = None
    piezo_z: Optional[float] = None
    piezo_th: Optional[float] = None

    # Hexapod coarse stage (legacy flat coords; still drive hexa_moves()).
    hexa_x: Optional[float] = None
    hexa_y: Optional[float] = None
    hexa_z: Optional[float] = None
    hexa_th: Optional[float] = None

    # Holder linkage (§2.4).
    holder_id: Optional[str] = None     # which holder this sample sits on
    slot: Optional[str] = None          # encoded physical first-guess position (Q-slot)

    # The nominal/refined position pair (D1).  nominal is holder-relative source of truth.
    nominal: Position = field(default_factory=lambda: Position(frame="holder"))
    refined: Optional[Position] = None  # absolute, set by alignment -- the runnable cache

    # Measurement parameters (per sample; technique plans read what they need).
    incident_angles: List[float] = field(default_factory=list)

    # Free-form metadata merged into md={} for this sample's run(s).
    md: Dict[str, Any] = field(default_factory=dict)

    # Alignment results + scan history (newest-last) (D5/D7).
    alignments: List[AlignmentResult] = field(default_factory=list)
    history: List[ScanRecord] = field(default_factory=list)

    created: float = field(default_factory=_now)
    updated: float = field(default_factory=_now)

    def __post_init__(self):
        for attr in ("piezo_x", "piezo_y", "piezo_z", "piezo_th",
                     "hexa_x", "hexa_y", "hexa_z", "hexa_th"):
            setattr(self, attr, _coerce_optional_float(getattr(self, attr)))
        self.incident_angles = [float(a) for a in self.incident_angles]
        if not self.name or not str(self.name).strip():
            raise ValueError("Sample.name must be a non-empty string")
        # Back-compat bridge: if the legacy flat coords were supplied but `nominal` is still the
        # default-empty holder Position, seed `nominal` from them (piezo_*->nominal.piezo_*,
        # hexa_*->nominal.stage_* per the Huber rename, incident_angles->nominal.incident_angles).
        # Do NOT clobber an explicitly-passed nominal.
        flat_set = any(getattr(self, a) is not None for a in
                       ("piezo_x", "piezo_y", "piezo_z", "piezo_th",
                        "hexa_x", "hexa_y", "hexa_z", "hexa_th")) or bool(self.incident_angles)
        if flat_set and self.nominal.is_empty():
            self.nominal.piezo_x = self.piezo_x
            self.nominal.piezo_y = self.piezo_y
            self.nominal.piezo_z = self.piezo_z
            self.nominal.piezo_th = self.piezo_th
            self.nominal.stage_x = self.hexa_x
            self.nominal.stage_y = self.hexa_y
            self.nominal.stage_z = self.hexa_z
            self.nominal.stage_theta = self.hexa_th
            if self.incident_angles:
                self.nominal.incident_angles = list(self.incident_angles)

    # -- convenience views ---------------------------------------------------
    def piezo_moves(self):
        """Return a dict ``{axis_name: value}`` of the piezo axes that are set (not None).

        Intended to be expanded into ``bps.mv`` pairs by a technique plan, e.g.::

            for axis, val in sample.piezo_moves().items():
                ...  # map 'x'->piezo.x etc.
        """
        out = {}
        for short, attr in (("x", "piezo_x"), ("y", "piezo_y"),
                            ("z", "piezo_z"), ("th", "piezo_th")):
            v = getattr(self, attr)
            if v is not None:
                out[short] = v
        return out

    def hexa_moves(self):
        """Return a dict ``{axis_name: value}`` of the hexapod axes that are set."""
        out = {}
        for short, attr in (("x", "hexa_x"), ("y", "hexa_y"),
                            ("z", "hexa_z"), ("th", "hexa_th")):
            v = getattr(self, attr)
            if v is not None:
                out[short] = v
        return out

    # -- derived / convenience (§2.4) ----------------------------------------
    def runnable_position(self):
        """The :class:`Position` to actually move to: ``refined`` if set, else ``nominal``.

        The doc specifies nominal-resolved-to-absolute via the holder transform, but the
        holder-fiducial transform fitting isn't wired yet (D4), so returning ``nominal``
        unchanged is correct for now -- when the fiducial routine lands this prefers
        ``holder.origin.apply(nominal)`` with no schema change.
        """
        return self.refined if self.refined is not None else self.nominal

    def base_md(self):
        """``{'sample_id','sample_name','holder_id','slot', **self.md}`` for run md (§2.4)."""
        out = {
            "sample_id": self.id,
            "sample_name": self.name,
            "holder_id": self.holder_id,
            "slot": self.slot,
        }
        out.update(self.md)
        return out

    def last_alignment(self, code=None):
        """The newest matching :class:`AlignmentResult` (optionally by ``code``) or ``None``."""
        for res in reversed(self.alignments):
            if code is None or res.code == code:
                return res
        return None

    def n_scans(self, scan_type=None):
        """Count history entries, optionally filtered by ``scan_type``."""
        if scan_type is None:
            return len(self.history)
        return sum(1 for r in self.history if r.scan_type == scan_type)

    def to_dict(self):
        """JSON-serializable dict (for GUIs / persistence) -- round-trips all fields."""
        return {
            "name": self.name,
            "id": self.id,
            "piezo_x": self.piezo_x,
            "piezo_y": self.piezo_y,
            "piezo_z": self.piezo_z,
            "piezo_th": self.piezo_th,
            "hexa_x": self.hexa_x,
            "hexa_y": self.hexa_y,
            "hexa_z": self.hexa_z,
            "hexa_th": self.hexa_th,
            "holder_id": self.holder_id,
            "slot": self.slot,
            "nominal": self.nominal.to_dict(),
            "refined": self.refined.to_dict() if self.refined is not None else None,
            "incident_angles": list(self.incident_angles),
            "md": dict(self.md),
            "alignments": [a.to_dict() for a in self.alignments],
            "history": [h.to_dict() for h in self.history],
            "created": self.created,
            "updated": self.updated,
        }

    @classmethod
    def from_dict(cls, d):
        """Reconstruct a :class:`Sample`, minting an ``id`` if absent (back-compat).

        Robust to missing keys and reconstructs every nested type (``nominal``/``refined``
        :class:`Position`, lists of :class:`AlignmentResult`/:class:`ScanRecord`).
        """
        d = dict(d or {})
        refined = d.get("refined")
        # Build with an empty nominal so __post_init__ does not re-seed from the flat coords;
        # then attach the stored nominal explicitly (preserving its frame/coords verbatim).
        obj = cls(
            name=d.get("name", ""),
            id=d.get("id") or uuid.uuid4().hex,
            piezo_x=d.get("piezo_x"),
            piezo_y=d.get("piezo_y"),
            piezo_z=d.get("piezo_z"),
            piezo_th=d.get("piezo_th"),
            hexa_x=d.get("hexa_x"),
            hexa_y=d.get("hexa_y"),
            hexa_z=d.get("hexa_z"),
            hexa_th=d.get("hexa_th"),
            holder_id=d.get("holder_id"),
            slot=d.get("slot"),
            nominal=(Position.from_dict(d["nominal"]) if d.get("nominal") is not None
                     else Position(frame="holder")),
            refined=Position.from_dict(refined) if refined is not None else None,
            incident_angles=list(d.get("incident_angles", []) or []),
            md=dict(d.get("md", {}) or {}),
            alignments=[AlignmentResult.from_dict(a) for a in (d.get("alignments", []) or [])],
            history=[ScanRecord.from_dict(h) for h in (d.get("history", []) or [])],
            created=d.get("created", _now()),
            updated=d.get("updated", _now()),
        )
        return obj


class SampleList:
    """An ordered collection of :class:`Sample` with construction & validation helpers."""

    def __init__(self, samples: Sequence[Sample] = ()):
        self.samples: List[Sample] = list(samples)
        self.validate()

    # -- container protocol --------------------------------------------------
    def __iter__(self):
        return iter(self.samples)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, i):
        return self.samples[i]

    def __repr__(self):
        return "SampleList({} samples: {})".format(
            len(self), ", ".join(s.name for s in self.samples))

    # -- validation ----------------------------------------------------------
    def validate(self):
        """Raise if names are duplicated or empty.  Returns self for chaining."""
        names = [s.name for s in self.samples]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise ValueError("Duplicate sample names: {}".format(dupes))
        return self

    # -- constructors --------------------------------------------------------
    @classmethod
    def from_columns(cls, names, *,
                     piezo_x=None, piezo_y=None, piezo_z=None, piezo_th=None,
                     hexa_x=None, hexa_y=None, hexa_z=None, hexa_th=None,
                     incident_angles=None, md=None):
        """Build from the legacy parallel-list convention with length checks.

        Any column left ``None`` is treated as all-``None`` (axis not used).  ``md`` may be
        a single dict (applied to all) or a per-sample list of dicts.  ``incident_angles``
        may be a single list (shared) or a per-sample list-of-lists.

        Example
        -------
        >>> bar = SampleList.from_columns(
        ...     names   = ['s1', 's2', 's3'],
        ...     piezo_x = [55000, 42000, 25000],
        ...     piezo_y = [5000, 5000, 5000],
        ...     incident_angles = [0.1, 0.2],          # shared by all
        ... )
        """
        n = len(names)

        def _col(col, label):
            if col is None:
                return [None] * n
            if len(col) != n:
                raise ValueError(
                    "Column '{}' has length {} but there are {} samples"
                    .format(label, len(col), n))
            return list(col)

        px, py, pz, pth = (_col(piezo_x, "piezo_x"), _col(piezo_y, "piezo_y"),
                           _col(piezo_z, "piezo_z"), _col(piezo_th, "piezo_th"))
        hx, hy, hz, hth = (_col(hexa_x, "hexa_x"), _col(hexa_y, "hexa_y"),
                           _col(hexa_z, "hexa_z"), _col(hexa_th, "hexa_th"))

        # incident_angles: shared list, per-sample list-of-lists, or None
        if incident_angles is None:
            ia = [[] for _ in range(n)]
        elif len(incident_angles) and isinstance(incident_angles[0], (list, tuple)):
            ia = _col(incident_angles, "incident_angles")
        else:
            ia = [list(incident_angles) for _ in range(n)]

        # md: shared dict or per-sample list
        if md is None:
            mds = [dict() for _ in range(n)]
        elif isinstance(md, dict):
            mds = [dict(md) for _ in range(n)]
        else:
            mds = _col(md, "md")

        samples = [
            Sample(name=names[i],
                   piezo_x=px[i], piezo_y=py[i], piezo_z=pz[i], piezo_th=pth[i],
                   hexa_x=hx[i], hexa_y=hy[i], hexa_z=hz[i], hexa_th=hth[i],
                   incident_angles=list(ia[i]), md=dict(mds[i]))
            for i in range(n)
        ]
        return cls(samples)

    @classmethod
    def from_dicts(cls, dicts):
        """Build from a list of dicts (e.g. a GUI table or JSON)."""
        return cls([Sample.from_dict(d) for d in dicts])

    @classmethod
    def from_csv(cls, path):
        """Build from a CSV whose header columns match :class:`Sample` fields.

        ``incident_angles`` may be a space- or semicolon-separated string in the cell.
        Unknown columns are folded into ``md``.  Blank cells become ``None``.
        """
        import csv

        known = {"name", "piezo_x", "piezo_y", "piezo_z", "piezo_th",
                 "hexa_x", "hexa_y", "hexa_z", "hexa_th", "incident_angles"}
        out = []
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                kwargs = {}
                md = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    key = k.strip()
                    val = (v or "").strip()
                    if key == "name":
                        kwargs["name"] = val
                    elif key == "incident_angles":
                        kwargs["incident_angles"] = [
                            float(x) for x in val.replace(";", " ").split()] if val else []
                    elif key in known:
                        kwargs[key] = (float(val) if val != "" else None)
                    elif key:
                        md[key] = val
                kwargs["md"] = md
                out.append(Sample(**kwargs))
        return cls(out)

    def to_dicts(self):
        return [s.to_dict() for s in self.samples]


# ---------------------------------------------------------------------------
# Slot -> nominal position (§2.4 "Slot -> nominal position", Q-slot)
# ---------------------------------------------------------------------------
def slot_to_position(holder_kind, slot, *, pitch=1000.0):
    """Map a holder ``slot`` to its first-guess holder-frame :class:`Position` (§2.4).

    A ``slot`` is an *encoded, addressable physical place* on the holder that **seeds** a
    sample's ``nominal`` (holder-relative) position; alignment then refines ``refined`` on top.

    **Interim encoding (implemented here):** ``slot`` is a numeric index (``"0"``, ``"1"``, ...)
    and this is a simple ``pitch x index`` along one axis for a 1-D bar -- the holder-frame
    ``stage_x`` is ``pitch * index``.  The **target** is a real encoded position (row/col or an
    engraved fiducial id) the holder geometry resolves; designing for the encoded form now means
    the numeric index is just the trivial encoding, so no schema change is needed later.  An
    unparseable/empty slot yields an empty holder-frame :class:`Position`.
    """
    if slot is None or str(slot).strip() == "":
        return Position(frame="holder")
    try:
        index = int(str(slot).strip())
    except (TypeError, ValueError):
        # Non-numeric (target encoding) -- not resolvable in the interim mapping; return empty.
        return Position(frame="holder")
    return Position(frame="holder", stage_x=float(pitch) * index)
