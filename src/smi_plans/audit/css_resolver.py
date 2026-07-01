"""
css_resolver -- resolve the concrete PVs reachable from the CS-Studio / Phoebus 12ID screens.

Both screen dialects are handled:

* **Phoebus** ``.bob``  -- ``<widget type="group|embedded">``, ``<action type="open_display">``;
  file refs in ``<file>``; macros in ``<macros><NAME>val</NAME>...</macros>``.
* **BOY** ``.opi``      -- ``<widget typeId="...groupingContainer|linkingContainer">``,
  ``<action type="OPEN_DISPLAY">``; file refs in ``<opi_file>`` / ``<path>``;
  macros likewise, with an explicit ``<include_parent_macros>`` flag.

Macro scope is inherited down the widget tree: a grouping container that sets ``Sys``/``Dev`` makes
those visible to every descendant widget *and* to any sub-screen it embeds/opens.  Leaf widgets
carry ``<pv_name>`` strings full of ``$(Macro)`` references which we expand against the accumulated
scope.  PVs that still contain unresolved ``$(...)`` after expansion are reported (not dropped) so
the resolver's blind spots are visible.

NO network, NO EPICS: this only reads files.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional
import xml.etree.ElementTree as ET


# --------------------------------------------------------------------------- macro expansion
# Phoebus/BOY accept both $(name) and ${name}; defaults via $(name=default).  Literal braces in
# PV names (e.g. XF:12IDC{...}) are never preceded by '$', so keying on '$' is unambiguous.
_MACRO_RE = re.compile(r"\$[({]([^(){}]*?)[)}]")


def expand_macros(s: str, scope: dict, max_iter: int = 25) -> str:
    """Expand ``$(name)`` / ``${name}`` / ``$(name=default)`` against ``scope``, iterating for
    chained macros.  Unknown macros with no default are left verbatim (so callers can flag them).
    """
    if not s:
        return s
    for _ in range(max_iter):
        if "$(" not in s and "${" not in s:
            break

        def _repl(m):
            spec = m.group(1)
            if "=" in spec:
                name, default = spec.split("=", 1)
            else:
                name, default = spec, None
            name = name.strip()
            if name in scope:
                return scope[name]
            return default if default is not None else m.group(0)

        new = _MACRO_RE.sub(_repl, s)
        if new == s:
            break
        s = new
    return s


def is_resolved(s: str) -> bool:
    return "$(" not in s and "${" not in s


# --------------------------------------------------------------------------- results
@dataclass
class CssPV:
    raw: str                      # the literal <pv_name> text (pre-expansion)
    resolved: str                 # after macro expansion
    leaf_file: str                # screen file the <pv_name> literally lives in (repo-relative)
    subsystem: str                # 12id top-level dir (op/va/ct/es/bi/det/ut/feedback/pps/...)
    root_file: str                # the 12id screen this was reached from
    resolved_ok: bool


@dataclass
class _Stats:
    files_parsed: int = 0
    pv_refs: int = 0
    parse_errors: list = field(default_factory=list)


# --------------------------------------------------------------------------- the resolver
class CssResolver:
    """Walk the include/macro graph from one or more root screens and collect concrete PVs.

    Parameters
    ----------
    repo_root : str
        Path to ``cs-studio-xf`` (the parent that contains both ``12id`` and ``common``).
    beamline_dir : str
        Name of the beamline screen dir under ``repo_root`` (default ``"12id"``).
    prefer_bob : bool
        When a referenced ``foo.opi`` has a sibling ``foo.bob``, follow the ``.bob`` (Phoebus
        supersedes BOY).  Extensionless refs try ``.bob`` then ``.opi``.
    max_depth : int
        Include-graph depth guard.
    """

    _FILE_TAGS = ("file", "opi_file", "path")
    _CONTAINER_WIDGET_HINTS = ("group", "embedded", "linkingContainer", "groupingContainer")

    def __init__(self, repo_root, beamline_dir="12id", prefer_bob=True, max_depth=30,
                 allow_prefixes=None):
        self.repo_root = os.path.abspath(repo_root)
        self.beamline_dir = beamline_dir
        self.prefer_bob = prefer_bob
        self.max_depth = max_depth
        # Only follow file references whose repo-relative path starts with one of these prefixes.
        # This confines the walk to the beamline's own screens plus the shared SMI/vendor templates
        # those screens *embed* (which carry a beamline macro scope and so resolve to real PVs),
        # and stops it wandering into other beamlines / the accelerator / vendor top-menus via
        # navigation links.  Undulator / front-end / EPS PVs that are placed directly on the 12ID
        # screens are still captured (they appear as literal <pv_name> there).
        self.allow_prefixes = tuple(allow_prefixes) if allow_prefixes is not None else (
            beamline_dir + "/", "12id1/", "common/",
        )
        self.pvs: list[CssPV] = []
        self.stats = _Stats()
        self._parse_cache: dict[str, Optional[ET.Element]] = {}
        # global memo of (file, scope) already walked -- collapses the exponential screen-to-screen
        # navigation graph (the same page re-reached with the same macro scope is processed once).
        self._visited: set = set()

    # -- public ---------------------------------------------------------------------------
    def resolve_all(self, roots=None):
        """Resolve from ``roots`` (repo-relative paths).  Defaults to the beamline main screens."""
        if roots is None:
            roots = []
            for name in ("main.bob", "main.opi"):
                p = os.path.join(self.beamline_dir, name)
                if os.path.exists(os.path.join(self.repo_root, p)):
                    roots.append(p)
        for root in roots:
            abspath = os.path.join(self.repo_root, root)
            abspath = self._prefer(abspath)
            rel = os.path.relpath(abspath, self.repo_root)
            sub = self._subsystem_of(rel)
            self._walk_file(abspath, scope={}, subsystem=sub, root_file=rel,
                            path_stack=())
        return self.pvs

    # -- file handling --------------------------------------------------------------------
    def _prefer(self, abspath):
        """Apply the prefer-.bob rule and extensionless resolution."""
        if self.prefer_bob and abspath.endswith(".opi"):
            bob = abspath[:-4] + ".bob"
            if os.path.exists(bob):
                return bob
        if not abspath.endswith((".opi", ".bob")):
            for ext in (".bob", ".opi"):
                if os.path.exists(abspath + ext):
                    return abspath + ext
        return abspath

    def _parse(self, abspath) -> Optional[ET.Element]:
        if abspath in self._parse_cache:
            return self._parse_cache[abspath]
        root = None
        try:
            root = ET.parse(abspath).getroot()
            self.stats.files_parsed += 1
        except Exception as exc:  # noqa: BLE001
            self.stats.parse_errors.append((os.path.relpath(abspath, self.repo_root), str(exc)))
        self._parse_cache[abspath] = root
        return root

    def _subsystem_of(self, rel):
        parts = rel.replace("\\", "/").split("/")
        if parts and parts[0] == self.beamline_dir and len(parts) > 1:
            return parts[1] if not parts[1].endswith((".opi", ".bob")) else "(root)"
        if parts and parts[0] == "common":
            return "common"
        return parts[0] if parts else "?"

    def _walk_file(self, abspath, scope, subsystem, root_file, path_stack):
        if len(path_stack) > self.max_depth:
            return
        if abspath in path_stack:           # cycle guard (current DFS branch only)
            return
        if not abspath.endswith((".opi", ".bob")):
            return
        rel_check = os.path.relpath(abspath, self.repo_root).replace("\\", "/")
        if not rel_check.startswith(self.allow_prefixes):
            return                          # out of audit scope (other beamline / accelerator)
        # global memo: same file + same macro scope -> already fully processed once
        memo_key = (abspath, frozenset(scope.items()))
        if memo_key in self._visited:
            return
        self._visited.add(memo_key)
        root = self._parse(abspath)
        if root is None:
            return
        rel = os.path.relpath(abspath, self.repo_root)
        # a screen's own subsystem if it lives under the beamline dir; templates keep parent's
        sub = self._subsystem_of(rel)
        if sub in ("common", "?") or rel.split("/")[0] != self.beamline_dir:
            sub = subsystem
        self._walk_element(root, scope, base_dir=os.path.dirname(abspath),
                            subsystem=sub, root_file=root_file, leaf_rel=rel,
                            path_stack=path_stack + (abspath,))

    # -- element handling -----------------------------------------------------------------
    def _local_scope(self, el, scope):
        """Return the scope for this element's subtree given its direct-child <macros> (if any)."""
        macros_el = el.find("macros")
        if macros_el is None:
            return scope
        inc = macros_el.findtext("include_parent_macros", default="true")
        include = str(inc).strip().lower() != "false"
        new = dict(scope) if include else {}
        for child in list(macros_el):
            if child.tag == "include_parent_macros":
                continue
            # macro values may themselves reference other macros -> expand against current view
            new[child.tag] = expand_macros((child.text or ""), {**scope, **new})
        return new

    def _file_ref(self, el):
        """Return the (raw) file reference string carried directly by this element, if any."""
        for tag in self._FILE_TAGS:
            child = el.find(tag)
            if child is not None and (child.text or "").strip():
                return child.text.strip()
        return None

    def _walk_element(self, el, scope, base_dir, subsystem, root_file, leaf_rel, path_stack):
        scope = self._local_scope(el, scope)

        # 1) PVs declared directly on this element
        for pv_el in el.findall("pv_name"):
            raw = (pv_el.text or "").strip()
            if not raw or raw.startswith(("loc://", "sim://", "=", "//", "pva://", "ca://")):
                continue
            resolved = expand_macros(raw, scope)
            ok = is_resolved(resolved)
            if not ok:
                # drop pure template placeholders (e.g. "$(pv_name)") -- a pv_name that is *only*
                # macro with no structural text is a pass-through slot, not a real PV.  Keep
                # partially-resolved real PVs (residual like ".RBV"/"allstop.VAL") as flagged.
                residual = _MACRO_RE.sub("", resolved).strip(" .:-{}")
                if not residual:
                    continue
            self.stats.pv_refs += 1
            self.pvs.append(CssPV(
                raw=raw, resolved=resolved, leaf_file=leaf_rel, subsystem=subsystem,
                root_file=root_file, resolved_ok=ok,
            ))

        # 2) a file this element pulls in (embedded widget, linkingContainer, or action)
        ref = self._file_ref(el)
        if ref:
            ref = expand_macros(ref, scope)
            if ref.endswith((".opi", ".bob")) or "." not in os.path.basename(ref):
                target = self._prefer(os.path.normpath(os.path.join(base_dir, ref)))
                self._walk_file(target, scope, subsystem, root_file, path_stack)

        # 3) recurse into child elements (widgets, <actions>/<action>, nested groups, ...)
        for child in list(el):
            if child.tag in ("macros", "pv_name") + self._FILE_TAGS:
                continue
            self._walk_element(child, scope, base_dir, subsystem, root_file, leaf_rel, path_stack)
