"""
smi_plans._holder
=================

The **holder bridge**: load a holder's samples from the persistent store (:class:`SampleStore`,
Redis db=2 or any dict backend) as a runnable :class:`SampleList`, and read/write a sample's cached
alignment -- so a bar plan can run from a *holder name* (no copy-paste of position lists), with
alignment persisted back to the store.

This promotes the field-validated ``holder_bar.py`` (used live at the beamline) into the backend,
built on the typed model + ``SampleStore`` rather than ad-hoc Redis access.

Purity & optionality
--------------------
* The store is **any** ``MutableMapping``-backed :class:`SampleStore`; Redis is reached only via
  ``SampleStore.from_redis`` (lazy ``redis`` import, ``[beamline]`` extra, local secret).  Nothing
  here imports Redis at module import -- importing/running off-beamline (CI, GUI) needs no Redis.
* The **dict backend is the primary tested path** (see ``tests/test_holder.py``).
* ``load_holder`` / ``get_aligned`` / ``needs_alignment`` / ``sample_center`` are **pure** (no
  bluesky).  Only ``save_aligned`` / ``clear_aligned`` are *plans* (they ``yield from bps.null()``
  so they compose inside ``RE(...)``), and they import ``bps`` lazily so the module stays importable
  without bluesky.
"""
import time

from ._samples import AlignmentResult, Position, Sample, SampleList
from ._store import SampleStore


__all__ = [
    "HolderBar",
    "load_holder",
    "get_aligned",
    "is_aligned",
    "needs_alignment",
    "save_aligned",
    "clear_aligned",
    "sample_center",
]


class HolderBar(SampleList):
    """A :class:`SampleList` that also carries the ``store`` and ``holder`` it came from.

    Behaves as a normal ``SampleList`` (iterate / index the samples) for plans, but the
    ``store``/``holder`` handles let :func:`save_aligned` persist alignment back.
    """

    def __init__(self, samples, *, store=None, holder=None):
        super().__init__(list(samples))
        self.store = store
        self.holder = holder

    def __repr__(self):
        hn = getattr(self.holder, "name", None)
        return "HolderBar(holder={!r}, n={})".format(hn, len(self))


def load_holder(holder_name, *, store=None, order_by_slot=True, require=True):
    """Return a :class:`HolderBar` of every sample in the holder named ``holder_name``.

    Drop-in replacement for a hard-coded ``SampleList.from_columns(...)``.  Each returned
    ``Sample`` carries any previously-saved aligned position in its ``refined`` field, so
    :func:`get_aligned` / :func:`needs_alignment` work without re-aligning.

    Parameters
    ----------
    holder_name : str
        The human holder name (``Holder.name``); first match wins (names need not be unique).
    store : SampleStore, optional
        An existing store (any backend).  If ``None``, opens ``SampleStore.from_redis()`` (db=2) --
        which is the ONLY place Redis is touched, and only then.
    order_by_slot : bool
        Order samples by ``slot`` (stable human order) within the holder's declared member order.
    require : bool
        Raise ``KeyError`` when no such holder exists / it has no samples (else return empty).
    """
    if store is None:
        store = SampleStore.from_redis()

    holder = next((h for h in store.list_holders() if h.name == holder_name), None)
    if holder is None:
        if require:
            raise KeyError(
                "No holder named {!r} in the sample store. Available: {}".format(
                    holder_name, sorted(h.name for h in store.list_holders())))
        return HolderBar([], store=store, holder=None)

    samples = store.list_samples(holder_id=holder.id)

    id_rank = {sid: i for i, sid in enumerate(holder.sample_ids or [])}

    def _key(s):
        primary = id_rank[s.id] if s.id in id_rank else len(id_rank)
        if order_by_slot and s.slot is not None:
            try:
                return (primary, 0, float(s.slot))
            except (TypeError, ValueError):
                return (primary, 1, str(s.slot))
        return (primary, 2, s.name)

    samples.sort(key=_key)

    if require and not samples:
        raise KeyError("Holder {!r} ({}) has no samples.".format(holder_name, holder.id))

    return HolderBar(samples, store=store, holder=holder)


# ---------------------------------------------------------------------------
# Cached alignment (theta, height) -- read from / written to the sample's refined Position
# ---------------------------------------------------------------------------
def get_aligned(sample):
    """Return the cached aligned ``(piezo_th, piezo_y)`` for ``sample``, or ``(None, None)``.

    Reads ``sample.refined`` (set by a previous :func:`save_aligned`)."""
    pos = sample.refined
    if pos is None:
        return None, None
    return pos.piezo_th, pos.piezo_y


def is_aligned(sample):
    """True if the sample has a cached aligned theta AND height in ``refined``."""
    th, y = get_aligned(sample)
    return (th is not None) and (y is not None)


def needs_alignment(sample, *, force=False):
    """True if the sample should be aligned now (no cached alignment, or ``force``)."""
    return force or not is_aligned(sample)


def sample_center(sample):
    """Return ``(x, y)`` from the sample's runnable position (``piezo_x``/``piezo_y``), or
    ``(None, None)`` -- the spot-grid center, read from the runnable Position (refined else
    nominal), the correct source for GUI/spreadsheet samples."""
    pos = sample.runnable_position()
    return pos.piezo_x, pos.piezo_y


# ---------------------------------------------------------------------------
# Persisting alignment back to the store (plans)
# ---------------------------------------------------------------------------
def _store_of(bar):
    store = getattr(bar, "store", None)
    if store is None:
        raise TypeError(
            "expected a HolderBar with a .store (from load_holder); got {!r}".format(type(bar)))
    return store


def save_aligned(bar, sample, th, y, *, code="gisaxs", run_uids=None, extra_fit=None):
    """Plan: persist the aligned ``th``/``y`` for ``sample`` to the store.

    Writes the sample's ``refined`` position (so :func:`get_aligned` returns it next time) and
    appends an :class:`AlignmentResult` audit entry.  Preserves any other axes already in the
    sample's runnable position (only theta + height are overwritten).  A plan (yields a no-op) so it
    composes inside ``RE(...)``; the write itself is a quick synchronous store update.
    """
    import bluesky.plan_stubs as bps  # lazy: keep the module importable without bluesky

    store = _store_of(bar)
    th = float(th)
    y = float(y)

    base = Position.from_dict(sample.runnable_position().to_dict())
    base.frame = "lab"
    base.piezo_th = th
    base.piezo_y = y

    fit = {"th_found": th, "y_found": y}
    if extra_fit:
        fit.update(extra_fit)

    res = AlignmentResult(
        code=code, status="ok", when=time.time(), refined=base, fit=fit,
        run_uids=list(run_uids) if run_uids else [],
    )
    store.append_alignment(sample.id, res)   # sets sample.refined from res.refined AND persists

    # keep the in-memory Sample in sync for this session
    sample.refined = Position.from_dict(base.to_dict())
    sample.alignments.append(res)

    yield from bps.null()


def clear_aligned(bar, sample):
    """Plan: forget a sample's cached alignment (``refined`` -> None) in the store."""
    import bluesky.plan_stubs as bps  # lazy

    store = _store_of(bar)
    store.update_refined(sample.id, None)
    sample.refined = None
    yield from bps.null()
