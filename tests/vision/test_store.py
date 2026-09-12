"""The enrolled-faces store: persistence, and refusing to compare apples to oranges."""

from __future__ import annotations

import json

import pytest

from tests.vision.conftest import FAR, NEAR_A, NEAR_B
from iris.app.vision.robot_eye.errors import BackendMismatch
from iris.app.vision.robot_eye.faces import COSINE, EUCLIDEAN
from iris.app.vision.robot_eye.store import (
    MAX_SAMPLES_PER_PERSON,
    STORE_VERSION,
    FaceStore,
    Person,
    UnknownPerson,
    normalize_person_name,
)

NOW = 1_700_000_000.0
DLIB = dict(backend="face_recognition", metric=EUCLIDEAN)


class TestNameNormalization:
    def test_whitespace_is_tidied(self):
        assert normalize_person_name("  Prakash   Sahu ") == "Prakash Sahu"

    def test_case_is_preserved_because_the_robot_says_it_out_loud(self):
        assert normalize_person_name("Prakash") == "Prakash"

    def test_an_empty_name_is_refused(self):
        for value in ("", "   ", None):
            with pytest.raises(ValueError, match="needs a name"):
                normalize_person_name(value)      # type: ignore[arg-type]

    def test_an_absurdly_long_name_is_refused(self):
        with pytest.raises(ValueError, match="longer than"):
            normalize_person_name("x" * 200)

    def test_line_breaks_collapse_into_spaces(self):
        # Whatever a speech transcript hands over becomes one clean line.
        assert normalize_person_name("Prakash\nSahu") == "Prakash Sahu"
        assert normalize_person_name("Prakash\t \r\n Sahu") == "Prakash Sahu"


class TestEnrolling:
    def test_a_new_person_is_created_with_one_sample(self, store):
        person = store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        assert person.name == "Prakash"
        assert person.sample_count == 1
        assert len(store) == 1

    def test_enrolling_again_adds_a_sample_rather_than_a_person(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        person = store.enroll("Prakash", NEAR_B, now=NOW + 60, **DLIB)
        assert person.sample_count == 2
        assert len(store) == 1
        assert person.updated_at == NOW + 60
        assert person.created_at == NOW

    def test_the_name_is_matched_case_insensitively(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("prakash", NEAR_B, now=NOW, **DLIB)
        assert len(store) == 1
        assert store.get("PRAKASH") is not None

    def test_an_empty_embedding_is_refused(self, store):
        with pytest.raises(ValueError, match="empty face embedding"):
            store.enroll("Prakash", (), now=NOW, **DLIB)

    def test_samples_are_capped_and_the_newest_are_kept(self, store):
        for index in range(MAX_SAMPLES_PER_PERSON + 5):
            store.enroll("Prakash", (float(index), 0.0, 0.0, 0.0), now=NOW + index, **DLIB)
        person = store.get("Prakash")
        assert person is not None
        assert person.sample_count == MAX_SAMPLES_PER_PERSON
        # The oldest fell off the front, not the newest off the back.
        assert person.samples[-1][0] == float(MAX_SAMPLES_PER_PERSON + 4)
        assert person.samples[0][0] == float(5)


class TestBackendIsolation:
    """A dlib vector and an ArcFace vector describe the same face in different
    languages. Comparing them yields a number, and the number is meaningless."""

    def test_enrolling_the_same_person_with_a_different_backend_is_refused(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        with pytest.raises(BackendMismatch, match="cannot be compared"):
            store.enroll("Prakash", NEAR_B, now=NOW, backend="insightface", metric=COSINE)

    def test_the_refusal_says_what_to_do_about_it(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        with pytest.raises(BackendMismatch) as excinfo:
            store.enroll("Prakash", NEAR_B, now=NOW, backend="insightface", metric=COSINE)
        assert "introduce them again" in str(excinfo.value)

    def test_a_changed_embedding_width_is_refused_too(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        with pytest.raises(BackendMismatch, match="numbers"):
            store.enroll("Prakash", (1.0, 2.0), now=NOW, **DLIB)

    def test_matching_against_another_backends_faces_explains_itself(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        with pytest.raises(BackendMismatch, match="different recogniser"):
            store.match(NEAR_A, backend="insightface", metric=COSINE, threshold=0.5)

    def test_two_people_on_different_backends_can_coexist(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("Aditi", NEAR_B, now=NOW, backend="insightface", metric=COSINE)
        assert len(store) == 2
        # ...and matching only ever considers the one that made the query.
        match = store.match(NEAR_A, backend="face_recognition",
                            metric=EUCLIDEAN, threshold=0.5)
        assert match is not None and match.name == "Prakash"


class TestMatching:
    def test_a_known_face_is_found(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        match = store.match(NEAR_B, backend="face_recognition",
                            metric=EUCLIDEAN, threshold=0.5)
        assert match is not None and match.name == "Prakash"

    def test_a_stranger_matches_nobody(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        assert store.match(FAR, backend="face_recognition",
                           metric=EUCLIDEAN, threshold=0.5) is None

    def test_an_empty_store_matches_nobody_without_complaining(self, store):
        assert store.match(NEAR_A, backend="face_recognition",
                           metric=EUCLIDEAN, threshold=0.5) is None

    def test_every_sample_is_considered_not_just_a_person_average(self, store):
        # Two samples far apart: with glasses and without. Their average is
        # close to neither, so an averaging store would recognise neither.
        store.enroll("Prakash", (1.0, 0.0, 0.0, 0.0), now=NOW, **DLIB)
        store.enroll("Prakash", (0.0, 1.0, 0.0, 0.0), now=NOW, **DLIB)

        for probe in ((0.95, 0.05, 0.0, 0.0), (0.05, 0.95, 0.0, 0.0)):
            match = store.match(probe, backend="face_recognition",
                                metric=EUCLIDEAN, threshold=0.2)
            assert match is not None, f"{probe} should match a stored sample"
            assert match.name == "Prakash"

        # And the averaged point really would have missed both.
        person = store.get("Prakash")
        assert person is not None
        centroid = person.centroid()
        from iris.app.vision.robot_eye.faces import euclidean_distance
        assert euclidean_distance(centroid, (0.95, 0.05, 0.0, 0.0)) > 0.2

    def test_the_closest_person_wins_when_two_are_similar(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("Cousin", (0.6, 0.0, 0.0, 0.0), now=NOW, **DLIB)
        match = store.match(NEAR_B, backend="face_recognition",
                            metric=EUCLIDEAN, threshold=0.9)
        assert match is not None and match.name == "Prakash"


class TestOwner:
    def test_nobody_owns_the_robot_to_begin_with(self, store):
        assert store.owner() is None

    def test_enrolling_as_owner_records_it(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        owner = store.owner()
        assert owner is not None and owner.name == "Prakash"

    def test_there_is_only_ever_one_owner(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        store.enroll("Aditi", NEAR_B, now=NOW, owner=True, **DLIB)
        owners = [person.name for person in store.people() if person.owner]
        assert owners == ["Aditi"]

    def test_the_owner_can_be_set_afterwards(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("Aditi", NEAR_B, now=NOW, owner=True, **DLIB)
        store.set_owner("Prakash")
        owner = store.owner()
        assert owner is not None and owner.name == "Prakash"

    def test_setting_an_unknown_owner_is_refused(self, store):
        with pytest.raises(UnknownPerson, match="don't know anyone"):
            store.set_owner("Nobody")

    def test_enrolling_again_without_saying_leaves_ownership_alone(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        store.enroll("Prakash", NEAR_B, now=NOW + 1, **DLIB)
        owner = store.owner()
        assert owner is not None and owner.name == "Prakash"


class TestForgetAndRename:
    def test_forgetting_removes_the_person(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        assert store.forget("prakash") is True
        assert len(store) == 0

    def test_forgetting_a_stranger_reports_false_rather_than_raising(self, store):
        assert store.forget("Nobody") is False

    def test_forgetting_an_invalid_name_is_also_just_false(self, store):
        assert store.forget("") is False

    def test_renaming_keeps_the_samples(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("Prakash", NEAR_B, now=NOW, **DLIB)
        person = store.rename("Prakash", "Prakash Sahu", now=NOW + 10)
        assert person.name == "Prakash Sahu"
        assert person.sample_count == 2
        assert store.get("Prakash") is None
        assert store.get("prakash sahu") is not None

    def test_renaming_onto_an_existing_person_is_refused(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("Aditi", NEAR_B, now=NOW, **DLIB)
        with pytest.raises(ValueError, match="already know"):
            store.rename("Prakash", "Aditi", now=NOW)

    def test_changing_only_the_capitalisation_is_allowed(self, store):
        store.enroll("prakash", NEAR_A, now=NOW, **DLIB)
        person = store.rename("prakash", "Prakash", now=NOW)
        assert person.name == "Prakash"

    def test_renaming_a_stranger_is_refused(self, store):
        with pytest.raises(UnknownPerson):
            store.rename("Nobody", "Somebody", now=NOW)

    def test_clearing_empties_the_store(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.clear()
        assert len(store) == 0


class TestPersistence:
    def test_faces_survive_a_restart(self, tmp_path):
        path = tmp_path / "faces.json"
        first = FaceStore(path)
        first.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)

        second = FaceStore(path)
        assert len(second) == 1
        person = second.get("Prakash")
        assert person is not None
        assert person.owner
        assert person.samples[0] == pytest.approx(NEAR_A)
        assert person.backend == "face_recognition"

    def test_the_file_is_readable_json_with_a_version(self, tmp_path):
        path = tmp_path / "faces.json"
        FaceStore(path).enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["version"] == STORE_VERSION
        assert payload["people"][0]["name"] == "Prakash"

    def test_no_temporary_files_are_left_behind(self, tmp_path):
        path = tmp_path / "faces.json"
        store = FaceStore(path)
        for index in range(4):
            store.enroll("Prakash", (float(index), 0.0, 0.0, 0.0), now=NOW, **DLIB)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["faces.json"]

    def test_a_corrupt_store_reads_as_empty_and_is_not_overwritten(self, tmp_path):
        path = tmp_path / "faces.json"
        path.write_text("{not json at all", encoding="utf-8")
        store = FaceStore(path)
        assert len(store) == 0
        # Left alone: silently replacing a file that might be recoverable,
        # or might be the wrong path entirely, is not this code's decision.
        assert path.read_text(encoding="utf-8") == "{not json at all"

    def test_a_missing_store_is_simply_empty(self, tmp_path):
        assert len(FaceStore(tmp_path / "nope" / "faces.json")) == 0

    def test_the_directory_is_created_on_first_save(self, tmp_path):
        path = tmp_path / "nested" / "deeper" / "faces.json"
        FaceStore(path).enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        assert path.exists()

    def test_one_unreadable_entry_does_not_lose_the_others(self, tmp_path):
        path = tmp_path / "faces.json"
        path.write_text(json.dumps({
            "version": 1,
            "people": [
                {"no_name_key": True},
                {"name": "Broken", "samples": []},
                {"name": "Prakash", "backend": "face_recognition",
                 "metric": "euclidean", "samples": [[1.0, 0.0, 0.0, 0.0]]},
            ],
        }), encoding="utf-8")
        store = FaceStore(path)
        assert [p.name for p in store] == ["Prakash"]

    def test_one_corrupt_sample_does_not_lose_the_person(self, tmp_path):
        path = tmp_path / "faces.json"
        path.write_text(json.dumps({
            "version": 1,
            "people": [{
                "name": "Prakash", "backend": "face_recognition", "metric": "euclidean",
                "samples": [["not", "numbers"], [1.0, 0.0, 0.0, 0.0], None],
            }],
        }), encoding="utf-8")
        store = FaceStore(path)
        person = store.get("Prakash")
        assert person is not None and person.sample_count == 1


class TestDescribe:
    def test_the_summary_counts_people_without_exposing_embeddings(self, store):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        store.enroll("Prakash", NEAR_B, now=NOW, **DLIB)
        summary = store.describe()
        assert summary["count"] == 1
        assert summary["people"][0] == {
            "name": "Prakash", "samples": 2, "owner": True,
            "backend": "face_recognition", "notes": "",
        }
        assert "samples" not in json.dumps(summary["people"][0]["name"])

    def test_people_come_back_in_a_stable_order(self, store):
        for name in ("Zara", "Aditi", "Prakash"):
            store.enroll(name, NEAR_A, now=NOW, **DLIB)
        assert [p.name for p in store.people()] == ["Aditi", "Prakash", "Zara"]


class TestPersonRoundTrip:
    def test_a_person_survives_a_dict_round_trip(self):
        person = Person(name="Prakash", backend="face_recognition", metric=EUCLIDEAN,
                        samples=[(1.0, 2.0)], created_at=NOW, updated_at=NOW,
                        owner=True, notes="the owner")
        clone = Person.from_dict(person.to_dict())
        assert clone.name == person.name
        assert clone.samples == person.samples
        assert clone.owner is True
        assert clone.notes == "the owner"
