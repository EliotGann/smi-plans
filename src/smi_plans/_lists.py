"""
smi_plans._lists
================

A **named-list library**: typed, reusable scan-input lists (edges/energies, incident angles,
temperatures, exposure times, ...) stored alongside the samples and **referenced by name** in plans
-- the same "no copy-paste" pattern the sample store proved.  A GUI curates the lists; a plan takes
either a name (resolved here) or a literal list.

Pieces
------
* :class:`NamedList` -- the pure, JSON-round-trippable model (a ``kind`` + explicit ``values`` and/or
  a generator ``spec`` the GUI edits, e.g. an energy edge's pre/near/post).
* :class:`ListStore` -- a typed facade over Redis db=2 (prefix ``swaxslists``) **or any dict**
  (tests/offline).  Mirrors :class:`smi_plans._store.SampleStore`; Redis is imported lazily in
  :meth:`ListStore.from_redis` only.
* :func:`resolve_list` -- the name-or-list seam: a literal sequence is returned as-is (no store
  needed); a name is looked up and materialized (from ``values`` or, failing that, ``spec``).

Purity
------
This module is **pure Python** (no bluesky/ophyd; ``redis`` only inside ``from_redis``).  Resolution
runs at plan-build time on the inputs, so plans stay message-pure.
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


__all__ = ["NamedList", "ListStore", "resolve_list", "LIST_SCHEMA_VERSION"]

LIST_SCHEMA_VERSION = 1

#: Recognized list kinds (open set -- unknown kinds still store/resolve their explicit ``values``).
KINDS = ("energy", "incidence", "temperature", "time")


def _new_id():
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Generator builders: spec -> values (per kind).  Pure Python (no numpy) so they run in CI.
# ---------------------------------------------------------------------------
def _frange(start, stop, step):
    """Inclusive-of-start, exclusive-of-stop float range (like np.arange), pure Python."""
    if step == 0:
        return []
    out = []
    n = 0
    # iterate by count to avoid float drift
    while True:
        v = start + n * step
        if (step > 0 and v >= stop) or (step < 0 and v <= stop):
            break
        out.append(round(v, 6))
        n += 1
    return out


def _energy_grid_from_spec(spec):
    """Absolute energies from an edge spec ``{edge, pre, near, post}`` (each ``[a, b, step]`` eV
    offsets).  Numerically matches :func:`technique_A_energy_edge.energy_grid` (coarse pre / fine
    near / coarse post, de-duplicated + sorted) but pure-Python so the library is CI-testable."""
    edge = float(spec["edge"])
    segs = []
    for key, default in (("pre", (-30, -2, 5.0)), ("near", (-2, 2, 0.25)), ("post", (2, 60, 5.0))):
        seg = spec[key] if key in spec and spec[key] is not None else default
        a, b, s = float(seg[0]), float(seg[1]), float(seg[2])
        if s <= 0:
            continue
        segs.append([edge + v for v in _frange(a, b, s)])
    vals = sorted({round(v, 6) for seg in segs for v in seg}) if segs else [edge]
    return vals


def _linspace_from_spec(spec):
    """Generic ``{start, stop, step}`` (or ``{start, stop, n}``) -> values.  Used for
    incidence/temperature/time specs that aren't an explicit list."""
    start, stop = float(spec["start"]), float(spec["stop"])
    if "step" in spec and spec["step"]:
        return _frange(start, stop, float(spec["step"])) + [stop] if start != stop else [start]
    n = int(spec.get("n", 2)) if hasattr(spec, "get") else int(spec["n"])
    if n <= 1:
        return [start]
    step = (stop - start) / (n - 1)
    return [round(start + i * step, 6) for i in range(n)]


#: kind -> spec builder.  Unknown kinds have no builder (must supply explicit ``values``).
_SPEC_BUILDERS = {
    "energy": _energy_grid_from_spec,
    "incidence": _linspace_from_spec,
    "temperature": _linspace_from_spec,
    "time": _linspace_from_spec,
}


def materialize(kind, spec):
    """Build the explicit value list for ``kind`` from its generator ``spec``."""
    if kind not in _SPEC_BUILDERS:
        raise ValueError(
            "no spec builder for list kind {!r}; provide explicit values".format(kind))
    return _SPEC_BUILDERS[kind](spec)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
@dataclass
class NamedList:
    """A reusable, named scan-input list (referenced by name in plans).

    ``values`` is authoritative when set; ``spec`` is the (optional) generator recipe the GUI edits
    (e.g. an energy edge ``{edge, pre, near, post}``), kept so the entry stays re-editable.  The GUI
    typically writes BOTH (it computes ``values`` from ``spec`` once); :func:`resolve_list` trusts
    ``values`` if present, else materializes from ``spec``.
    """
    name: str
    kind: str
    values: Optional[List[float]] = None
    spec: Optional[Dict] = None
    units: Optional[str] = None
    id: str = field(default_factory=_new_id)
    md: Dict = field(default_factory=dict)

    def resolved_values(self):
        """The explicit values: ``values`` if set, else materialized from ``spec``."""
        if self.values is not None:
            return list(self.values)
        if self.spec is not None:
            return materialize(self.kind, self.spec)
        raise ValueError(
            "NamedList {!r} (kind {!r}) has neither values nor spec".format(self.name, self.kind))

    def to_dict(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "values": None if self.values is None else list(self.values),
            "spec": dict(self.spec) if self.spec else None,
            "units": self.units,
            "id": self.id,
            "md": dict(self.md),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            name=d["name"],
            kind=d["kind"],
            values=(list(d["values"]) if d.get("values") is not None else None),
            spec=(dict(d["spec"]) if d.get("spec") else None),
            units=d.get("units"),
            id=d.get("id", _new_id()),
            md=dict(d.get("md", {}) or {}),
        )


# ---------------------------------------------------------------------------
# Store (mirrors SampleStore; own prefix so it never collides with swaxssamples)
# ---------------------------------------------------------------------------
_LIST_PREFIX = "list:"
_LIST_INDEX_KEY = "index:list"          # {kind: {name: id}}
_LIST_SCHEMA_KEY = "schema_version"
_LIST_RESERVED = frozenset({_LIST_INDEX_KEY, _LIST_SCHEMA_KEY})


class ListStore:
    """A typed facade over a Redis db=2 ``RedisJSONDict`` (prefix ``swaxslists``) or any ``dict``.

    Key layout (dict-keys; the backend prefixes them)::

        list:<id>     -> NamedList.to_dict()
        index:list    -> { kind: { name: id } }
        schema_version-> int
    """

    def __init__(self, backend):
        self.backend = backend
        if _LIST_SCHEMA_KEY not in self.backend:
            self.backend[_LIST_SCHEMA_KEY] = LIST_SCHEMA_VERSION

    @classmethod
    def from_redis(cls, *, host="xf12id2-smi-redis1.nsls2.bnl.gov", port=6380, ssl=True,
                   db=2, prefix="swaxslists", password=None,
                   secret_path="/etc/bluesky/redis.secret"):
        """Open an INDEPENDENT db=2 connection (own prefix), like ``SampleStore.from_redis``.

        Redis is imported HERE only -- the package imports cleanly without it.
        """
        import redis
        from redis_json_dict import RedisJSONDict
        if password is None:
            with open(secret_path) as fh:
                password = fh.read().strip()
        client = redis.Redis(host, db=db, ssl=ssl, port=port, password=password)
        return cls(RedisJSONDict(client, prefix))

    # -- internals --
    @staticmethod
    def _key(list_id):
        return _LIST_PREFIX + str(list_id)

    def _index(self):
        return dict(self.backend.get(_LIST_INDEX_KEY, {}) or {})

    def _put_index(self, index):
        self.backend[_LIST_INDEX_KEY] = dict(index)

    def _iter_keys(self):
        for key in list(self.backend):
            if key in _LIST_RESERVED:
                continue
            if key.startswith(_LIST_PREFIX):
                yield key

    # -- CRUD --
    def put_list(self, nl):
        """Upsert a :class:`NamedList`; (kind, name) is the unique handle."""
        self.backend[self._key(nl.id)] = nl.to_dict()
        index = self._index()
        index.setdefault(nl.kind, {})
        index[nl.kind][nl.name] = nl.id
        self._put_index(index)
        return nl

    def get_list(self, name, kind):
        """Return the :class:`NamedList` for ``(kind, name)``; raise ``KeyError`` if absent."""
        index = self._index()
        per_kind = index[kind] if kind in index else {}
        if name not in per_kind:
            avail = sorted(per_kind)
            raise KeyError(
                "no {!r} list named {!r}; available {} lists: {}".format(kind, name, kind, avail))
        return NamedList.from_dict(self.backend[self._key(per_kind[name])])

    def find_list(self, name, kind):
        """Like :meth:`get_list` but returns ``None`` instead of raising."""
        try:
            return self.get_list(name, kind)
        except KeyError:
            return None

    def list_lists(self, kind=None):
        """Return all :class:`NamedList` (optionally only those of ``kind``)."""
        out = []
        for key in self._iter_keys():
            nl = NamedList.from_dict(self.backend[key])
            if kind is None or nl.kind == kind:
                out.append(nl)
        return out

    def delete_list(self, name, kind):
        """Remove the ``(kind, name)`` list (no-op if absent)."""
        index = self._index()
        per_kind = index[kind] if kind in index else {}
        if name not in per_kind:
            return
        list_id = per_kind.pop(name)
        self._put_index(index)
        key = self._key(list_id)
        if key in self.backend:
            del self.backend[key]


# ---------------------------------------------------------------------------
# Resolution (the name-or-list seam)
# ---------------------------------------------------------------------------
def resolve_list(value, *, kind, store=None):
    """Resolve ``value`` to an explicit list of values.

    * ``value`` a sequence (list/tuple) -> returned as a plain list, used as-is (NO store needed).
    * ``value`` a string -> looked up as a :class:`NamedList` named ``value`` of ``kind`` in
      ``store``, then materialized (its ``values``, or built from its ``spec``).

    Raises ``ValueError`` for a name with no ``store``, and ``KeyError`` (clear, listing available)
    for a name not found.  Keeps plans backward compatible: pass a literal and nothing changes.
    """
    if value is None:
        return None
    if isinstance(value, str):
        if store is None:
            raise ValueError(
                "list {!r} is a NAMED list but no store was provided to resolve it; pass "
                "store=ListStore(...) or an explicit list of values".format(value))
        return store.get_list(value, kind).resolved_values()
    # already an explicit sequence
    return [v for v in value]
