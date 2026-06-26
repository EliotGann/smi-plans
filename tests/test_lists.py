"""WS7 tests: the named-list library (NamedList + ListStore + resolve_list).

Pure tests against an in-memory ``dict()`` backend -- no Redis, no secret, no bluesky.  This is the
PRIMARY tested path (the live Redis db=2 store is a ``MutableMapping`` too).
"""
import pytest

from smi_plans._lists import (
    NamedList, ListStore, resolve_list, materialize, LIST_SCHEMA_VERSION,
)


# -- model round-trip --------------------------------------------------------
def test_namedlist_roundtrip_values():
    nl = NamedList(name="Fe_K_XANES", kind="energy", values=[7090.0, 7100.0, 7110.0], units="eV")
    nl2 = NamedList.from_dict(nl.to_dict())
    assert nl2.name == "Fe_K_XANES" and nl2.kind == "energy"
    assert nl2.resolved_values() == [7090.0, 7100.0, 7110.0]


def test_namedlist_spec_materializes_energy_grid():
    nl = NamedList(name="S_K", kind="energy", spec={"edge": 2472.0})
    vals = nl.resolved_values()
    assert vals[0] < 2472.0 < vals[-1] and len(vals) > 10  # pre/near/post around the edge
    # values authoritative over spec when both set
    nl2 = NamedList(name="x", kind="energy", values=[1.0, 2.0], spec={"edge": 2472.0})
    assert nl2.resolved_values() == [1.0, 2.0]


def test_namedlist_no_values_no_spec_raises():
    with pytest.raises(ValueError):
        NamedList(name="empty", kind="energy").resolved_values()


def test_unknown_kind_needs_explicit_values():
    # unknown kind + spec -> can't materialize
    with pytest.raises(ValueError):
        materialize("mystery", {"foo": 1})
    # but explicit values are fine for any kind
    assert NamedList(name="m", kind="mystery", values=[1, 2]).resolved_values() == [1, 2]


# -- store CRUD --------------------------------------------------------------
def test_store_put_get_by_kind_and_name():
    store = ListStore(dict())
    store.put_list(NamedList(name="Fe", kind="energy", values=[7090.0]))
    store.put_list(NamedList(name="fine", kind="incidence", values=[0.1, 0.2]))
    assert store.get_list("Fe", "energy").values == [7090.0]
    assert store.get_list("fine", "incidence").values == [0.1, 0.2]


def test_store_same_name_different_kind_coexist():
    """Names are unique WITHIN a kind: 'fine' can be both an incidence and a time list."""
    store = ListStore(dict())
    store.put_list(NamedList(name="fine", kind="incidence", values=[0.1, 0.2]))
    store.put_list(NamedList(name="fine", kind="time", values=[1.0, 2.0]))
    assert store.get_list("fine", "incidence").values == [0.1, 0.2]
    assert store.get_list("fine", "time").values == [1.0, 2.0]


def test_store_get_missing_lists_available():
    store = ListStore(dict())
    store.put_list(NamedList(name="Fe", kind="energy", values=[7090.0]))
    with pytest.raises(KeyError) as ei:
        store.get_list("Cu", "energy")
    assert "Fe" in str(ei.value)
    assert store.find_list("Cu", "energy") is None


def test_store_list_and_delete():
    store = ListStore(dict())
    store.put_list(NamedList(name="a", kind="energy", values=[1.0]))
    store.put_list(NamedList(name="b", kind="energy", values=[2.0]))
    store.put_list(NamedList(name="c", kind="time", values=[3.0]))
    assert {n.name for n in store.list_lists(kind="energy")} == {"a", "b"}
    assert {n.name for n in store.list_lists()} == {"a", "b", "c"}
    store.delete_list("a", "energy")
    assert store.find_list("a", "energy") is None
    assert {n.name for n in store.list_lists(kind="energy")} == {"b"}


# -- resolve_list (the name-or-list seam) ------------------------------------
def test_resolve_list_literal_needs_no_store():
    assert resolve_list([7090, 7100, 7110], kind="energy") == [7090, 7100, 7110]
    assert resolve_list((0.1, 0.2), kind="incidence") == [0.1, 0.2]
    assert resolve_list(None, kind="energy") is None


def test_resolve_list_name_from_store():
    store = ListStore(dict())
    store.put_list(NamedList(name="Fe_K_XANES", kind="energy", values=[7090.0, 7100.0]))
    assert resolve_list("Fe_K_XANES", kind="energy", store=store) == [7090.0, 7100.0]


def test_resolve_list_name_materializes_spec():
    store = ListStore(dict())
    store.put_list(NamedList(name="S_K", kind="energy", spec={"edge": 2472.0}))
    vals = resolve_list("S_K", kind="energy", store=store)
    assert vals[0] < 2472.0 < vals[-1]


def test_resolve_list_name_without_store_errors():
    with pytest.raises(ValueError):
        resolve_list("Fe_K_XANES", kind="energy")  # no store


def test_resolve_list_wrong_kind_not_found():
    store = ListStore(dict())
    store.put_list(NamedList(name="Fe", kind="energy", values=[7090.0]))
    with pytest.raises(KeyError):
        resolve_list("Fe", kind="incidence", store=store)  # right name, wrong kind


def test_no_redis_import_for_lists():
    import sys
    import smi_plans._lists  # noqa
    assert "redis" not in sys.modules
