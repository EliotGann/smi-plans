"""
smi_plans
=========

Modern, modular, GUI-ready Bluesky data-acquisition templates for the NSLS-II SMI-SWAXS
beamline.

This package is the *target* style for SMI user scripts.  It is organized so that the
reusable parts (sample model, preprocessors, run-shaping primitives) are separate from the
per-technique plan files, and so that a GUI could eventually import the same building blocks.

Layout
------
* ``_samples``       -- :class:`Sample` / :class:`SampleList` (pure Python; GUI-safe).
* ``_preprocessors`` -- opt-in plan-mutating decorators (fresh-spot, ensure-in, beam-loss
  re-seek, baseline, cleanup, extra-dets).
* ``_core``          -- run-shaping primitives (one-sample run, multi-open-run interleave,
  sample positioning, detector selection, filename tokens).
* ``technique_*``    -- one file per use-case archetype (A-O); each provides clean, composable
  plan functions plus a runnable example.

Authoring rules (the short version)
-----------------------------------
1. ONE run per logical sample (or interleaved runs via :func:`_core.multi_sample_run`).
2. Record context as devices/Signals in the stream (or baseline if constant); never bake it
   into the filename with ``.get()``.
3. Build the filename from recorded fields with :func:`_core.fname` (``{device_field}``).
4. Pass intent via ``md={}``; never use ``sample_id``/``RE.md`` mutation.
5. Plans are generators end-to-end; never call ``RE()`` inside a plan, never ``cam.put`` to
   trigger.
6. Keep the physics idioms via the ``_preprocessors`` wrappers.
7. Keep sample tables out of plan bodies -- use :class:`SampleList`.

.. important::
    The technique files and ``_core``/``_preprocessors`` reference beamline globals injected
    by the SMI profile collection at runtime (``bps``, ``bpp``, ``Signal``, ``np``, ``piezo``,
    ``stage``, ``waxs``, ``energy``, ``pil2M``, ``pil900KW``, ``xbpm2/3``,
    ``det_exposure_time``, alignment routines ...).  They are meant to be ``%run`` / imported
    inside the live beamline IPython session.  ``_samples`` is pure Python and importable
    anywhere.
"""

from ._samples import (  # noqa: F401  (pure python, always safe)
    AlignmentResult,
    Holder,
    Magazine,
    Position,
    Sample,
    SampleList,
    ScanRecord,
    SpotSummary,
    HolderTransform,
    slot_to_position,
)
from ._store import SampleStore  # noqa: F401  (pure python; redis imported lazily in from_redis)
from ._holder import (  # noqa: F401  (pure loader; bluesky lazy in save/clear_aligned)
    HolderBar,
    load_holder,
    get_aligned,
    is_aligned,
    needs_alignment,
    save_aligned,
    clear_aligned,
    sample_center,
)
from ._bar_helpers import (  # noqa: F401  (pure console/GUI helpers)
    POSITION_AXES,
    bar_name_tokens,
    apply_name_prefix,
    preview_bar_name,
    preview_name,
    adjust_holder_positions,
    adjust_bar_positions,
    sort_holder_by_name,
    sort_bar_by_name,
)
from ._lists import (  # noqa: F401  (pure python; redis imported lazily in from_redis)
    NamedList,
    ListStore,
    resolve_list,
)

# The peak/edge analyzer (pf).  Hard-imports only numpy; scipy/bokeh/databroker are lazy, so this
# is safe to expose even off-beamline.  Guard anyway so a missing numpy never breaks the import.
try:  # pragma: no cover
    from . import analysis  # noqa: F401
    from .analysis import pf, analyze_xy, PeakResult  # noqa: F401
except Exception:  # pragma: no cover
    analysis = None

# The device-dependent modules import bluesky lazily; importing the package outside the
# beamline env should still expose the sample model without exploding.
try:  # pragma: no cover
    from . import _preprocessors, _core, _compose  # noqa: F401
except Exception:  # pragma: no cover
    _preprocessors = None
    _core = None
    _compose = None

__all__ = [
    "Sample",
    "SampleList",
    "Position",
    "Holder",
    "Magazine",
    "AlignmentResult",
    "ScanRecord",
    "SpotSummary",
    "HolderTransform",
    "slot_to_position",
    "SampleStore",
    "HolderBar",
    "load_holder",
    "get_aligned",
    "is_aligned",
    "needs_alignment",
    "save_aligned",
    "clear_aligned",
    "sample_center",
    "POSITION_AXES",
    "bar_name_tokens",
    "apply_name_prefix",
    "preview_bar_name",
    "preview_name",
    "adjust_holder_positions",
    "adjust_bar_positions",
    "sort_holder_by_name",
    "sort_bar_by_name",
    "NamedList",
    "ListStore",
    "resolve_list",
    "analysis",
    "pf",
    "analyze_xy",
    "PeakResult",
    "_preprocessors",
    "_core",
    "_compose",
]
