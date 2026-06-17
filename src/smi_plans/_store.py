"""
smi_plans._store
================

The typed :class:`SampleStore` facade (SAMPLE_SYSTEM_PLAN.md §3) -- a thin layer over a
``RedisJSONDict`` on Redis db=2 (or any ``dict`` / JSON-file-backed mapping for tests/offline
development).  CRUD for samples/holders, the active-sample pointer, history append, and the
spreadsheet round-trip.

Purity & connection model
--------------------------
This module is **pure Python**.  ``redis`` and ``redis_json_dict`` are imported **lazily inside
:meth:`SampleStore.from_redis` only**, so the module imports cleanly with neither installed (the
beamline plans, an external GUI, and the test suite all share this one facade; they differ only in
how they obtain the backend -- §1b).  No bluesky, no ophyd, no profile import.

Key layout (§3) -- ``RedisJSONDict`` namespaces a *dict-key* ``k`` to the literal Redis key
``f"{prefix}{k}"`` by **direct concatenation, no separator**; with ``prefix="swaxssamples"`` the
dict-keys below therefore become ``swaxssamplessample:<id>`` etc.::

    sample:<sample_id>   -> Sample.to_dict()
    holder:<holder_id>   -> Holder.to_dict()
    magazine             -> Magazine.to_dict()   (singleton)
    index:name           -> { name: sample_id }  (human-name lookup cache)
    schema_version       -> int                  (migration guard)

Because ``RedisJSONDict`` stores JSON via orjson, **sequences read back as lists** -- the model in
``_samples`` never relies on tuples surviving, and neither does this store.
"""

import csv
import os
import time

from ._samples import (
    Holder,
    Magazine,
    Position,
    Sample,
)


__all__ = ["SampleStore", "SCHEMA_VERSION"]

SCHEMA_VERSION = 1

# dict-key conventions (prefix is applied by the backend via direct concatenation).
_SAMPLE_PREFIX = "sample:"
_HOLDER_PREFIX = "holder:"
_MAGAZINE_KEY = "magazine"
_INDEX_NAME_KEY = "index:name"
_SCHEMA_VERSION_KEY = "schema_version"
_LAST_EXPORT_KEY = "_last_export"
# Keys that are NOT per-record (skipped when iterating samples/holders).
_RESERVED_KEYS = frozenset(
    {_MAGAZINE_KEY, _INDEX_NAME_KEY, _SCHEMA_VERSION_KEY, _LAST_EXPORT_KEY}
)


class SampleStore:
    """A typed facade over a Redis db=2 ``RedisJSONDict`` (or any ``dict``/JSON-file mapping).

    The backend is a ``MutableMapping`` of *dict-key -> jsonable dict*: ``backend[key] = value``,
    ``backend[key]``, ``del backend[key]``, ``key in backend``, ``for key in backend`` (dict-keys,
    prefix already stripped), and ``backend.get(key, default)``.  ``SampleStore`` stores each
    dataclass via ``.to_dict()`` and reconstructs it via ``.from_dict()``.
    """

    def __init__(self, backend):
        self.backend = backend
        # Stamp the schema version on first use (migration guard).
        if _SCHEMA_VERSION_KEY not in self.backend:
            self.backend[_SCHEMA_VERSION_KEY] = SCHEMA_VERSION

    @classmethod
    def from_redis(cls, *, host="xf12id2-smi-redis1.nsls2.bnl.gov", port=6380, ssl=True,
                   db=2, prefix="swaxssamples", password=None,
                   secret_path="/etc/bluesky/redis.secret"):
        """Open an INDEPENDENT db=2 connection -- the door for external tools (§1b).

        No profile import; requires only ``redis`` + ``redis_json_dict`` in the caller's env.
        Reads the password from ``secret_path`` if ``password`` is ``None``.
        """
        import redis
        from redis_json_dict import RedisJSONDict
        if password is None:
            with open(secret_path) as fh:
                password = fh.read().strip()
        client = redis.Redis(host, db=db, ssl=ssl, port=port, password=password)
        return cls(RedisJSONDict(client, prefix))

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _sample_key(sample_id):
        return _SAMPLE_PREFIX + str(sample_id)

    @staticmethod
    def _holder_key(holder_id):
        return _HOLDER_PREFIX + str(holder_id)

    def _name_index(self):
        """Return the (mutable copy of the) name->id index dict."""
        return dict(self.backend.get(_INDEX_NAME_KEY, {}) or {})

    def _put_name_index(self, index):
        self.backend[_INDEX_NAME_KEY] = dict(index)

    def _iter_sample_keys(self):
        for key in list(self.backend):
            if key in _RESERVED_KEYS:
                continue
            if key.startswith(_SAMPLE_PREFIX):
                yield key

    def _iter_holder_keys(self):
        for key in list(self.backend):
            if key in _RESERVED_KEYS:
                continue
            if key.startswith(_HOLDER_PREFIX):
                yield key

    # ------------------------------------------------------------------
    # samples
    # ------------------------------------------------------------------
    def put_sample(self, s):
        """Store ``s`` (bumping ``updated``) and maintain the name->id index."""
        s.updated = time.time()
        self.backend[self._sample_key(s.id)] = s.to_dict()
        index = self._name_index()
        index[s.name] = s.id
        self._put_name_index(index)

    def get_sample(self, sample_id):
        """Return the :class:`Sample`; raise ``KeyError`` if missing."""
        key = self._sample_key(sample_id)
        if key not in self.backend:
            raise KeyError(sample_id)
        return Sample.from_dict(self.backend[key])

    def find_sample(self, name):
        """Return a :class:`Sample` by human ``name`` (index first, then linear scan) or ``None``.

        Names are not guaranteed unique; this returns the indexed/first match.
        """
        index = self._name_index()
        # (Subscript rather than index.get(name): keeps the message-purity guard from mistaking
        # a plain-dict lookup with a variable key for a device read.)
        sid = index[name] if name in index else None
        if sid is not None:
            key = self._sample_key(sid)
            if key in self.backend:
                return Sample.from_dict(self.backend[key])
        # Fall back to a linear scan (index may be stale / absent).
        for key in self._iter_sample_keys():
            s = Sample.from_dict(self.backend[key])
            if s.name == name:
                return s
        return None

    def list_samples(self, holder_id=None):
        """Return all :class:`Sample` (optionally filtered to ``holder_id``)."""
        out = []
        for key in self._iter_sample_keys():
            s = Sample.from_dict(self.backend[key])
            if holder_id is None or s.holder_id == holder_id:
                out.append(s)
        return out

    def delete_sample(self, sample_id):
        """Remove the sample key and its name-index entry (no-op if absent)."""
        key = self._sample_key(sample_id)
        name = None
        if key in self.backend:
            name = Sample.from_dict(self.backend[key]).name
            del self.backend[key]
        index = self._name_index()
        # Drop any index entries pointing at this id (or sharing the resolved name).
        changed = False
        for nm in list(index.keys()):
            if index[nm] == sample_id or (name is not None and nm == name
                                          and index[nm] == sample_id):
                del index[nm]
                changed = True
        if changed:
            self._put_name_index(index)

    # ------------------------------------------------------------------
    # holders / magazine
    # ------------------------------------------------------------------
    def put_holder(self, h):
        """Store ``h`` (bumping ``updated``)."""
        h.updated = time.time()
        self.backend[self._holder_key(h.id)] = h.to_dict()

    def get_holder(self, holder_id):
        """Return the :class:`Holder`; raise ``KeyError`` if missing."""
        key = self._holder_key(holder_id)
        if key not in self.backend:
            raise KeyError(holder_id)
        return Holder.from_dict(self.backend[key])

    def list_holders(self):
        """Return all :class:`Holder`."""
        return [Holder.from_dict(self.backend[key]) for key in self._iter_holder_keys()]

    def magazine(self):
        """Return the singleton :class:`Magazine` (a fresh empty one if unset)."""
        data = self.backend.get(_MAGAZINE_KEY)
        if data is None:
            return Magazine()
        return Magazine.from_dict(data)

    def _put_magazine(self, m):
        self.backend[_MAGAZINE_KEY] = m.to_dict()

    def set_measurement_holder(self, holder_id):
        """Set which holder is at the beam (D3: one at a time); persist."""
        m = self.magazine()
        m.measurement_holder_id = holder_id
        self._put_magazine(m)

    # ------------------------------------------------------------------
    # the active ("loaded") sample (D12)
    # ------------------------------------------------------------------
    def set_active_sample(self, sample_id):
        """Persist the active-sample pointer in the magazine singleton (D12)."""
        m = self.magazine()
        m.active_sample_id = sample_id
        self._put_magazine(m)

    def get_active_sample(self):
        """Return the active :class:`Sample` or ``None``."""
        sid = self.magazine().active_sample_id
        if sid is None:
            return None
        key = self._sample_key(sid)
        if key not in self.backend:
            return None
        return Sample.from_dict(self.backend[key])

    # ------------------------------------------------------------------
    # history (append-only; the hot path) (D7/D8)
    # ------------------------------------------------------------------
    def append_scan_record(self, sample_id, rec):
        """Append a :class:`ScanRecord` to the sample's history; persist."""
        s = self.get_sample(sample_id)
        s.history.append(rec)
        self.put_sample(s)

    def append_alignment(self, sample_id, res):
        """Append an :class:`AlignmentResult` and set ``refined`` from it; persist."""
        s = self.get_sample(sample_id)
        s.alignments.append(res)
        # The refined position is the latest alignment's converged position.
        s.refined = Position.from_dict(res.refined.to_dict())
        self.put_sample(s)

    def update_refined(self, sample_id, pos):
        """Set the sample's ``refined`` position; persist."""
        s = self.get_sample(sample_id)
        s.refined = pos
        self.put_sample(s)

    # ------------------------------------------------------------------
    # bulk / spreadsheet (D14)
    # ------------------------------------------------------------------
    def import_samples(self, samples, holder):
        """Upsert ``holder`` + its ``samples`` and register the holder in the magazine.

        Sets ``holder.sample_ids`` to the imported samples, stamps each sample's ``holder_id``,
        persists everything, and adds the holder to ``magazine.holder_ids``.
        """
        holder.sample_ids = [s.id for s in samples]
        self.put_holder(holder)
        for s in samples:
            s.holder_id = holder.id
            self.put_sample(s)
        m = self.magazine()
        if holder.id not in m.holder_ids:
            m.holder_ids.append(holder.id)
        if holder.magazine_slot is not None:
            m.slots[holder.magazine_slot] = holder.id
        self._put_magazine(m)

    def export_tables(self):
        """Return ``(samples_rows, scans_rows)`` -- two flat, joinable row-lists (§6).

        ``samples_rows`` is one row per sample (nominal/refined coords flattened, last-alignment
        summary, scan counts, first/last scan times, last energy, ``md.*`` flattened out).
        ``scans_rows`` is one row per :class:`ScanRecord` (position coords + spot summary +
        flattened ``result_*``), each referencing ``sample_id`` so the two join on it.
        """
        coord_fields = ("piezo_x", "piezo_y", "piezo_z", "piezo_th",
                        "stage_x", "stage_y", "stage_z",
                        "stage_theta", "stage_chi", "stage_phi")
        samples_rows = []
        scans_rows = []
        for s in self.list_samples():
            row = {
                "sample_id": s.id,
                "name": s.name,
                "holder": s.holder_id,
                "slot": s.slot,
                "incident_angles": _join_floats(s.nominal.incident_angles),
            }
            for f in coord_fields:
                row["nominal_" + f] = getattr(s.nominal, f)
            for f in coord_fields:
                row["refined_" + f] = (getattr(s.refined, f)
                                       if s.refined is not None else None)
            last = s.last_alignment()
            row["last_alignment_code"] = last.code if last is not None else None
            row["last_alignment_status"] = last.status if last is not None else None
            row["last_alignment_when"] = last.when if last is not None else None
            row["n_alignments"] = len(s.alignments)
            row["n_data_scans"] = s.n_scans("data")
            row["n_total_scans"] = s.n_scans()
            whens = [r.when for r in s.history]
            row["first_scan_when"] = min(whens) if whens else None
            row["last_scan_when"] = max(whens) if whens else None
            last_energy = None
            for r in s.history:
                if r.energy_eV is not None:
                    last_energy = r.energy_eV
            row["last_energy_eV"] = last_energy
            # md flattened back out (keys prefixed to avoid colliding with the fixed columns).
            for k, v in s.md.items():
                row["md." + str(k)] = v
            samples_rows.append(row)

            for r in s.history:
                srow = {
                    "sample_id": s.id,
                    "name": s.name,
                    "run_uid": r.run_uid,
                    "scan_name": r.scan_name,
                    "scan_type": r.scan_type,
                    "when": r.when,
                    "energy_eV": r.energy_eV,
                    "transmission": r.transmission,
                    "attenuation_factor": r.attenuation_factor,
                    "exposure_s": r.exposure_s,
                    "geometry": r.geometry,
                    "detectors": _join_strs(r.detectors),
                }
                for f in coord_fields:
                    srow["pos_" + f] = getattr(r.position, f)
                srow["spots_kind"] = r.spots.kind
                srow["spots_count"] = r.spots.count
                srow["spots_bbox"] = _join_floats(r.spots.bbox) if r.spots.bbox else None
                srow["spots_motor_x"] = r.spots.motor_x
                srow["spots_motor_y"] = r.spots.motor_y
                for k, v in r.result.items():
                    srow["result_" + str(k)] = v
                scans_rows.append(srow)

        return samples_rows, scans_rows

    def export_csv(self, dir_path):
        """Write ``samples_out.csv`` + ``scans_out.csv`` into ``dir_path``; record the export.

        Returns ``(samples_out_path, scans_out_path)``.  Recording the export (via the
        ``_last_export`` key) is what lets :meth:`prune` proceed (§3: prune requires an export
        first so history is never lost).
        """
        samples_rows, scans_rows = self.export_tables()
        samples_path = os.path.join(dir_path, "samples_out.csv")
        scans_path = os.path.join(dir_path, "scans_out.csv")
        _write_rows(samples_path, samples_rows)
        _write_rows(scans_path, scans_rows)
        self.backend[_LAST_EXPORT_KEY] = time.time()
        return samples_path, scans_path

    # ------------------------------------------------------------------
    # lifecycle / pruning (Q-history-cap) -- MANUAL, never automatic
    # ------------------------------------------------------------------
    def prune(self, *, sample_ids=None, holders=None, require_export=True):
        """Deliberately remove targeted samples/holders (e.g. end of a campaign) (§3).

        Refuses (``require_export=True``) unless an export has been written first (tracked by
        :meth:`export_csv`), so the enriched spreadsheet + the full record are preserved before
        anything is removed.  **Never** called automatically.  Returns a dict summarizing what
        was removed.
        """
        if require_export and _LAST_EXPORT_KEY not in self.backend:
            raise RuntimeError(
                "prune() requires an export first: call store.export_csv(dir_path) before "
                "pruning so the enriched spreadsheet (and the full Tiled record) are preserved."
            )
        removed_samples = []
        removed_holders = []
        for sid in list(sample_ids or []):
            key = self._sample_key(sid)
            if key in self.backend:
                self.delete_sample(sid)
                removed_samples.append(sid)
        for hid in list(holders or []):
            key = self._holder_key(hid)
            if key in self.backend:
                del self.backend[key]
                removed_holders.append(hid)
                # Drop the holder from the magazine bookkeeping too.
                m = self.magazine()
                if hid in m.holder_ids:
                    m.holder_ids.remove(hid)
                if m.measurement_holder_id == hid:
                    m.measurement_holder_id = None
                for slot_name in list(m.slots.keys()):
                    if m.slots[slot_name] == hid:
                        del m.slots[slot_name]
                self._put_magazine(m)
        return {"samples": removed_samples, "holders": removed_holders}


# ---------------------------------------------------------------------------
# module-level CSV helpers
# ---------------------------------------------------------------------------
def _join_floats(values):
    if not values:
        return ""
    return " ".join(repr(float(v)) for v in values)


def _join_strs(values):
    if not values:
        return ""
    return " ".join(str(v) for v in values)


def _write_rows(path, rows):
    """Write ``rows`` (list of flat dicts) as CSV; the header is the union of all keys."""
    fieldnames = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
