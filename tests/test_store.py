"""Pure tests for the extended sample model + the :class:`SampleStore` facade.

No redis, no bluesky: every test runs against an in-memory ``dict()`` backend (the §1b
"tests/offline backend"), exercising the whole sample lifecycle exactly as the live Redis db=2
store would (``RedisJSONDict`` is a ``MutableMapping`` too).  Round-trips assert that each
dataclass survives ``to_dict()``/``from_dict()`` (and therefore JSON/orjson, where sequences come
back as lists).
"""
import csv

import pytest

from smi_plans import (
    AlignmentResult,
    Holder,
    HolderTransform,
    Magazine,
    Position,
    Sample,
    SampleStore,
    ScanRecord,
    SpotSummary,
    slot_to_position,
)
from smi_plans._store import SCHEMA_VERSION


# ---------------------------------------------------------------------------
# dataclass round-trips
# ---------------------------------------------------------------------------
def test_position_roundtrip_and_hexa_alias():
    p = Position(frame="holder", piezo_x=1.0, stage_x=2.0, stage_phi=3.0,
                 incident_angles=[0.1, 0.2])
    assert Position.from_dict(p.to_dict()).to_dict() == p.to_dict()

    # legacy hexa_* aliases map onto stage_* on input (§2.1 compatibility note)
    legacy = Position.from_dict({"frame": "lab", "hexa_x": 5, "hexa_y": 6,
                                 "hexa_z": 7, "hexa_th": 8, "piezo_x": 1})
    assert legacy.stage_x == 5.0
    assert legacy.stage_y == 6.0
    assert legacy.stage_z == 7.0
    assert legacy.stage_theta == 8.0
    assert legacy.piezo_x == 1.0
    # explicit stage_* wins over the alias
    both = Position.from_dict({"hexa_x": 5, "stage_x": 99})
    assert both.stage_x == 99.0


def test_alignment_result_roundtrip_nested_position():
    ar = AlignmentResult(code="gisaxs_hex", status="ok", when=1.5,
                         refined=Position(frame="lab", stage_x=3.0),
                         params={"angle": 0.1}, fit={"peak": 9.0, "fwhm": 0.2},
                         run_uids=["u1", "u2"], notes="hi")
    ar2 = AlignmentResult.from_dict(ar.to_dict())
    assert ar2.to_dict() == ar.to_dict()
    assert ar2.refined.stage_x == 3.0
    assert ar2.run_uids == ["u1", "u2"]


def test_spot_summary_roundtrip():
    ss = SpotSummary(kind="grid", points=[[1.0, 2.0], [3.0, 4.0]],
                     bbox=[1.0, 2.0, 3.0, 4.0], count=2, motor_x="piezo_x",
                     motor_y="piezo_y", units="um")
    ss2 = SpotSummary.from_dict(ss.to_dict())
    assert ss2.to_dict() == ss.to_dict()
    assert ss2.points == [[1.0, 2.0], [3.0, 4.0]]
    # a fresh/empty one round-trips with bbox None
    empty = SpotSummary()
    assert SpotSummary.from_dict(empty.to_dict()).bbox is None


def test_scan_record_roundtrip_nested():
    sr = ScanRecord(run_uid="r1", scan_name="n", scan_type="data", when=2.0,
                    position=Position(frame="lab", stage_x=1.0),
                    energy_eV=2470.0, transmission=0.5, attenuation_factor=2.0,
                    exposure_s=0.1, geometry="reflection", detectors=["pil2M"],
                    spots=SpotSummary(kind="point", count=1),
                    result={"th_found": 0.12}, md={"k": "v"})
    sr2 = ScanRecord.from_dict(sr.to_dict())
    assert sr2.to_dict() == sr.to_dict()
    assert sr2.position.stage_x == 1.0
    assert sr2.spots.count == 1
    assert sr2.detectors == ["pil2M"]


def test_holder_transform_identity():
    ht = HolderTransform()  # status="unset"
    nom = Position(frame="holder", stage_x=10.0, stage_y=0.0, piezo_x=5.0)
    out = ht.apply(nom)
    # identity: coords unchanged, frame normalized to lab, piezo carried verbatim
    assert out.frame == "lab"
    assert out.stage_x == 10.0
    assert out.stage_y == 0.0
    assert out.piezo_x == 5.0
    assert HolderTransform.from_dict(ht.to_dict()).to_dict() == ht.to_dict()


def test_holder_transform_fitted_rotation():
    # 90 deg rotation about origin + (dx,dy,dz) offset
    ht = HolderTransform(dx=100.0, dy=200.0, dz=5.0, theta=90.0, status="fit",
                         fiducial_uids=["f1"], when=3.0)
    nom = Position(frame="holder", stage_x=10.0, stage_y=0.0, stage_z=1.0, piezo_x=7.0)
    out = ht.apply(nom)
    # (10, 0) rotated 90deg -> (0, 10); + offset (100, 200) -> (100, 210)
    assert out.stage_x == pytest.approx(100.0)
    assert out.stage_y == pytest.approx(210.0)
    assert out.stage_z == pytest.approx(6.0)   # 1 + dz
    assert out.piezo_x == 7.0                   # piezo untouched
    assert out.frame == "lab"
    assert HolderTransform.from_dict(ht.to_dict()).to_dict() == ht.to_dict()


def test_holder_roundtrip_origin_none_and_set():
    h = Holder(name="bar_A", kind="bar", sample_ids=["a", "b"], md={"owner": "me"},
               origin=HolderTransform(dx=1.0, status="fit"))
    h2 = Holder.from_dict(h.to_dict())
    assert h2.to_dict() == h.to_dict()
    assert h2.origin is not None
    assert h2.origin.dx == 1.0
    assert h2.sample_ids == ["a", "b"]
    # origin None round-trips
    h3 = Holder(name="bar_B")
    assert Holder.from_dict(h3.to_dict()).origin is None
    # auto id minted
    assert h3.id and len(h3.id) == 32


def test_magazine_roundtrip():
    mg = Magazine(holder_ids=["h1", "h2"], measurement_holder_id="h1",
                  active_sample_id="s1", slots={"A": "h1", "B": None})
    assert Magazine.from_dict(mg.to_dict()).to_dict() == mg.to_dict()


def test_extended_sample_roundtrip_with_history_and_alignments():
    s = Sample(name="x", holder_id="hh", slot="2",
               nominal=Position(frame="holder", stage_x=5.0),
               refined=Position(frame="lab", stage_x=6.0),
               md={"project_name": "p"})
    s.alignments.append(AlignmentResult(code="c", status="ok",
                                        refined=Position(frame="lab", stage_x=6.0)))
    s.history.append(ScanRecord(run_uid="u1", scan_type="data",
                                position=Position(frame="lab", stage_x=6.0)))
    s.history.append(ScanRecord(run_uid="u2", scan_type="alignment",
                                position=Position(frame="lab", stage_x=6.0)))
    s2 = Sample.from_dict(s.to_dict())
    assert s2.to_dict() == s.to_dict()
    assert len(s2.history) == 2
    assert len(s2.alignments) == 1
    assert s2.refined.stage_x == 6.0
    assert s2.nominal.stage_x == 5.0


# ---------------------------------------------------------------------------
# Sample back-compat
# ---------------------------------------------------------------------------
def test_sample_backcompat_populates_nominal():
    s = Sample(name="s", piezo_x=1, hexa_x=2)
    # legacy flat coords still drive the old views
    assert s.piezo_moves() == {"x": 1.0}
    assert s.hexa_moves() == {"x": 2.0}
    # ... and seed the nominal Position (piezo_*->piezo_*, hexa_*->stage_*)
    assert s.nominal.piezo_x == 1.0
    assert s.nominal.stage_x == 2.0
    assert s.nominal.frame == "holder"
    # auto id
    assert s.id and len(s.id) == 32
    # round-trip equal
    assert Sample.from_dict(s.to_dict()).to_dict() == s.to_dict()


def test_sample_explicit_nominal_not_clobbered():
    explicit = Position(frame="holder", stage_x=42.0)
    s = Sample(name="s", piezo_x=1, hexa_x=2, nominal=explicit)
    # explicit nominal is preserved (not overwritten from the flat coords)
    assert s.nominal.stage_x == 42.0
    assert s.nominal.piezo_x is None


def test_sample_from_dict_mints_id_when_absent():
    s = Sample.from_dict({"name": "legacy", "piezo_x": 3.0})
    assert s.name == "legacy"
    assert s.id and len(s.id) == 32
    assert s.piezo_x == 3.0


def test_sample_helpers_last_alignment_and_n_scans():
    s = Sample(name="s")
    assert s.last_alignment() is None
    assert s.n_scans() == 0
    s.alignments.append(AlignmentResult(code="a", when=1.0))
    s.alignments.append(AlignmentResult(code="b", when=2.0))
    assert s.last_alignment().code == "b"          # newest
    assert s.last_alignment(code="a").code == "a"   # filtered
    s.history.append(ScanRecord(scan_type="data"))
    s.history.append(ScanRecord(scan_type="alignment"))
    s.history.append(ScanRecord(scan_type="data"))
    assert s.n_scans() == 3
    assert s.n_scans("data") == 2


def test_runnable_position_prefers_refined():
    s = Sample(name="s", nominal=Position(frame="holder", stage_x=1.0))
    assert s.runnable_position().stage_x == 1.0     # nominal when no refined
    s.refined = Position(frame="lab", stage_x=9.0)
    assert s.runnable_position().stage_x == 9.0     # refined wins


def test_slot_to_position_interim_encoding():
    assert slot_to_position("bar", "3", pitch=1000.0).stage_x == 3000.0
    assert slot_to_position("bar", "0").stage_x == 0.0
    assert slot_to_position("bar", "3").frame == "holder"
    assert slot_to_position("bar", None).is_empty()
    assert slot_to_position("bar", "").is_empty()
    assert slot_to_position("bar", "nonnumeric").is_empty()


# ---------------------------------------------------------------------------
# SampleStore full lifecycle against a dict()
# ---------------------------------------------------------------------------
def test_store_init_stamps_schema_version():
    be = {}
    SampleStore(be)
    assert be["schema_version"] == SCHEMA_VERSION


def test_store_sample_crud():
    st = SampleStore({})
    s = Sample(name="alpha", piezo_x=1.0)
    st.put_sample(s)
    # get
    got = st.get_sample(s.id)
    assert got.name == "alpha"
    # find by name
    assert st.find_sample("alpha").id == s.id
    assert st.find_sample("missing") is None
    # list
    assert [x.name for x in st.list_samples()] == ["alpha"]
    # missing get raises
    with pytest.raises(KeyError):
        st.get_sample("nope")
    # delete
    st.delete_sample(s.id)
    assert st.list_samples() == []
    assert st.find_sample("alpha") is None


def test_store_put_sample_bumps_updated():
    st = SampleStore({})
    s = Sample(name="a")
    s.updated = 0.0
    st.put_sample(s)
    assert st.get_sample(s.id).updated > 0.0


def test_store_holder_crud_and_magazine():
    st = SampleStore({})
    h = Holder(name="bar1")
    st.put_holder(h)
    assert st.get_holder(h.id).name == "bar1"
    assert [x.id for x in st.list_holders()] == [h.id]
    with pytest.raises(KeyError):
        st.get_holder("nope")
    # magazine starts empty
    assert isinstance(st.magazine(), Magazine)
    assert st.magazine().holder_ids == []
    # set measurement holder persists
    st.set_measurement_holder(h.id)
    assert st.magazine().measurement_holder_id == h.id
    st.set_measurement_holder(None)
    assert st.magazine().measurement_holder_id is None


def test_store_active_sample():
    st = SampleStore({})
    assert st.get_active_sample() is None
    s = Sample(name="s")
    st.put_sample(s)
    st.set_active_sample(s.id)
    assert st.get_active_sample().id == s.id
    # dangling pointer (deleted sample) -> None, not an error
    st.delete_sample(s.id)
    assert st.get_active_sample() is None


def test_store_history_append_paths():
    st = SampleStore({})
    s = Sample(name="s")
    st.put_sample(s)
    # append_scan_record shows up in get_sample
    rec = ScanRecord(run_uid="u1", scan_type="data",
                     position=Position(frame="lab", stage_x=1.0), energy_eV=2470.0)
    st.append_scan_record(s.id, rec)
    loaded = st.get_sample(s.id)
    assert len(loaded.history) == 1
    assert loaded.history[0].run_uid == "u1"
    # append_alignment sets refined from the result
    res = AlignmentResult(code="c", status="ok",
                          refined=Position(frame="lab", stage_x=8.0))
    st.append_alignment(s.id, res)
    loaded = st.get_sample(s.id)
    assert len(loaded.alignments) == 1
    assert loaded.refined.stage_x == 8.0
    # update_refined
    st.update_refined(s.id, Position(frame="lab", stage_x=42.0))
    assert st.get_sample(s.id).refined.stage_x == 42.0


def test_store_import_samples():
    st = SampleStore({})
    h = Holder(name="bar", magazine_slot="A")
    s1 = Sample(name="s1")
    s2 = Sample(name="s2")
    st.import_samples([s1, s2], h)
    # holder gets the sample ids
    assert st.get_holder(h.id).sample_ids == [s1.id, s2.id]
    # each sample stamped with holder_id
    assert {x.name for x in st.list_samples(holder_id=h.id)} == {"s1", "s2"}
    # magazine knows the holder + slot
    m = st.magazine()
    assert h.id in m.holder_ids
    assert m.slots.get("A") == h.id


def test_store_export_tables_joinable():
    st = SampleStore({})
    h = Holder(name="bar")
    s = Sample(name="s", slot="1",
               nominal=Position(frame="holder", stage_x=5.0, incident_angles=[0.1]),
               md={"thickness_nm": 40})
    st.import_samples([s], h)
    st.append_scan_record(s.id, ScanRecord(run_uid="u1", scan_name="scanA",
                                           scan_type="data", when=10.0,
                                           position=Position(frame="lab", stage_x=5.0),
                                           energy_eV=2475.0,
                                           spots=SpotSummary(kind="point", count=1),
                                           result={"q_peak": 0.3}))
    samples_rows, scans_rows = st.export_tables()
    assert len(samples_rows) == 1
    assert len(scans_rows) == 1
    srow = samples_rows[0]
    assert srow["sample_id"] == s.id
    assert srow["name"] == "s"
    assert srow["nominal_stage_x"] == 5.0
    assert srow["n_total_scans"] == 1
    assert srow["n_data_scans"] == 1
    assert srow["last_energy_eV"] == 2475.0
    assert srow["md.thickness_nm"] == 40
    scrow = scans_rows[0]
    assert scrow["sample_id"] == s.id        # joinable on sample_id
    assert scrow["run_uid"] == "u1"
    assert scrow["pos_stage_x"] == 5.0
    assert scrow["spots_kind"] == "point"
    assert scrow["result_q_peak"] == 0.3


def test_store_export_csv_writes_two_files(tmp_path):
    st = SampleStore({})
    h = Holder(name="bar")
    s = Sample(name="s")
    st.import_samples([s], h)
    st.append_scan_record(s.id, ScanRecord(run_uid="u1", scan_type="data"))
    samples_path, scans_path = st.export_csv(str(tmp_path))
    assert (tmp_path / "samples_out.csv").exists()
    assert (tmp_path / "scans_out.csv").exists()
    # files are readable CSV with the expected key columns
    with open(samples_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["sample_id"] == s.id
    with open(scans_path, newline="") as fh:
        srows = list(csv.DictReader(fh))
    assert srows[0]["run_uid"] == "u1"
    assert srows[0]["sample_id"] == s.id


def test_store_prune_requires_export(tmp_path):
    st = SampleStore({})
    h = Holder(name="bar")
    s = Sample(name="s")
    st.import_samples([s], h)
    # without a prior export, prune refuses
    with pytest.raises(RuntimeError):
        st.prune(sample_ids=[s.id])
    assert st.find_sample("s") is not None     # nothing removed
    # after an export, prune proceeds
    st.export_csv(str(tmp_path))
    result = st.prune(sample_ids=[s.id], holders=[h.id])
    assert result["samples"] == [s.id]
    assert result["holders"] == [h.id]
    assert st.find_sample("s") is None
    assert st.list_holders() == []
    # holder removed from magazine bookkeeping too
    assert h.id not in st.magazine().holder_ids
    # require_export=False bypasses the gate on a fresh store
    st2 = SampleStore({})
    s2 = Sample(name="z")
    st2.put_sample(s2)
    st2.prune(sample_ids=[s2.id], require_export=False)
    assert st2.find_sample("z") is None


# ---------------------------------------------------------------------------
# cross-connection simulation (shared bus, but with a dict)
# ---------------------------------------------------------------------------
def test_two_stores_sharing_one_backend():
    """Mirror the GUI/beamline shared db=2 bus: two SampleStore over the SAME mapping."""
    shared = {}
    gui = SampleStore(shared)
    beamline = SampleStore(shared)

    # GUI writes a sample; the beamline reads it (live, no shared imports)
    s = Sample(name="shared_sample", nominal=Position(frame="holder", stage_x=1.0))
    gui.put_sample(s)
    assert beamline.get_sample(s.id).name == "shared_sample"
    assert beamline.find_sample("shared_sample").id == s.id

    # beamline appends history; the GUI sees it
    beamline.append_scan_record(s.id, ScanRecord(run_uid="u1", scan_type="data"))
    assert len(gui.get_sample(s.id).history) == 1

    # GUI sets the active sample; the beamline agrees (the shared hand-off, D12)
    gui.set_active_sample(s.id)
    assert beamline.get_active_sample().id == s.id

    # beamline refines; the GUI reads the new refined position
    beamline.update_refined(s.id, Position(frame="lab", stage_x=99.0))
    assert gui.get_sample(s.id).refined.stage_x == 99.0
