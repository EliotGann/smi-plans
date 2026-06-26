"""Root conftest: make the test suite bind to the WORKING-TREE source, not any installed copy.

The beamline envs may have an installed ``smi_plans`` that would otherwise shadow ``src/`` and let
tests silently run against stale code (this bit us once: ``test_multi_sample_assets.py`` passed
against ``src`` but failed against an older installed package).  Prepending ``src`` to ``sys.path``
here guarantees ``import smi_plans`` resolves to this checkout, with no editable-install step needed.

If you prefer an editable install (``pip install -e .``), this stays harmless -- ``src`` is simply
first on the path either way.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
