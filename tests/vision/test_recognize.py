"""What the robot concludes from a frame, and what it says about it."""

from __future__ import annotations

import pytest

from tests.vision.conftest import DETECT_ONLY_SPEC, FAR, NEAR_A, NEAR_B, FakeRecognizer, big_box, tiny_box
from iris.app.vision.robot_eye.errors import NoFaceFound, VisionUnavailable
from iris.app.vision.robot_eye.faces import EUCLIDEAN, Box
from iris.app.vision.robot_eye.recognize import (
    MIN_ENROLL_FACE_FRACTION,
    describe_presence_only,
    describe_sighting,
    enroll,
    is_owner,
    look,
)

NOW = 1_700_000_000.0
FRAME = b"\xff\xd8ignored\xff\xd9"
DLIB = dict(backend="fake", metric=EUCLIDEAN)


def recognizer(*faces):
    return FakeRecognizer(faces=list(faces))


class TestLook:
    def test_an_empty_room_yields_no_faces(self, store, decode_fake):
        sighting = look(FRAME, recognizer(), store, decode=decode_fake)
        assert sighting.face_count == 0
        assert sighting.primary is None

    def test_the_frame_dimensions_come_from_the_decoded_image(self, store, decode_fake):
        sighting = look(FRAME, recognizer(), store, decode=decode_fake)
        assert (sighting.frame_width, sighting.frame_height) == (800, 600)

    def test_a_known_face_is_identified(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), NEAR_B)), store, decode=decode_fake)
        assert sighting.face_count == 1
        assert sighting.known and sighting.known[0].name == "Prakash"
        assert sighting.primary is not None and sighting.primary.name == "Prakash"

    def test_a_stranger_is_seen_but_not_named(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), FAR)), store, decode=decode_fake)
        assert sighting.face_count == 1
        assert not sighting.known
        assert len(sighting.unknown) == 1

    def test_a_distant_face_is_flagged_rather_than_guessed_at(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((tiny_box(), NEAR_B)), store, decode=decode_fake)
        assert sighting.face_count == 1
        face = sighting.faces[0]
        assert face.too_small
        assert face.match is None            # never compared at all
        assert not sighting.known and not sighting.unknown

    def test_the_nearest_face_becomes_the_primary_one(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        store.enroll("Aditi", FAR, now=NOW, **DLIB)
        near = big_box(fraction=0.25)
        far = Box(x=10, y=10, w=90, h=90)
        sighting = look(FRAME, recognizer((far, FAR), (near, NEAR_B)), store,
                        decode=decode_fake)
        assert sighting.primary is not None
        assert sighting.primary.box == near
        assert sighting.primary.name == "Prakash"

    def test_without_a_store_it_detects_but_does_not_identify(self, decode_fake):
        rec = recognizer((big_box(), NEAR_A))
        sighting = look(FRAME, rec, None, decode=decode_fake)
        assert sighting.face_count == 1
        assert sighting.faces[0].match is None
        assert rec.detect_calls == 1 and rec.embed_calls == 0

    def test_a_detection_only_backend_never_tries_to_embed(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        rec = FakeRecognizer(faces=[(big_box(), NEAR_A)], spec=DETECT_ONLY_SPEC)
        sighting = look(FRAME, rec, store, decode=decode_fake)
        assert sighting.face_count == 1
        assert sighting.faces[0].match is None
        assert rec.embed_calls == 0

    def test_the_smallness_floor_is_adjustable(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        small = Box(x=0, y=0, w=60, h=60)     # 0.75% of the frame
        strict = look(FRAME, recognizer((small, NEAR_B)), store, decode=decode_fake)
        assert strict.faces[0].too_small
        lenient = look(FRAME, recognizer((small, NEAR_B)), store,
                       decode=decode_fake, min_fraction=0.001)
        assert not lenient.faces[0].too_small
        assert lenient.known


class TestEnroll:
    def test_one_clear_face_is_remembered(self, store, decode_fake):
        person, speech = enroll(FRAME, "Prakash", recognizer((big_box(), NEAR_A)),
                                store, now=NOW, decode=decode_fake)
        assert person.name == "Prakash"
        assert person.sample_count == 1
        assert "remember that face" in speech
        assert len(store) == 1

    def test_the_first_sample_asks_for_more(self, store, decode_fake):
        _, speech = enroll(FRAME, "Prakash", recognizer((big_box(), NEAR_A)),
                           store, now=NOW, decode=decode_fake)
        assert "different light" in speech

    def test_later_samples_report_the_running_count(self, store, decode_fake):
        rec = recognizer((big_box(), NEAR_A))
        enroll(FRAME, "Prakash", rec, store, now=NOW, decode=decode_fake)
        _, speech = enroll(FRAME, "Prakash", rec, store, now=NOW + 1, decode=decode_fake)
        assert "2 looks at Prakash" in speech

    def test_it_can_record_the_owner(self, store, decode_fake):
        enroll(FRAME, "Prakash", recognizer((big_box(), NEAR_A)), store,
               now=NOW, owner=True, decode=decode_fake)
        owner = store.owner()
        assert owner is not None and owner.name == "Prakash"

    def test_no_face_asks_the_person_to_face_the_camera(self, store, decode_fake):
        with pytest.raises(NoFaceFound) as excinfo:
            enroll(FRAME, "Prakash", recognizer(), store, now=NOW, decode=decode_fake)
        assert "look straight at the camera" in str(excinfo.value).lower()
        assert len(store) == 0

    def test_two_faces_is_refused_because_it_would_be_a_guess(self, store, decode_fake):
        rec = recognizer((big_box(), NEAR_A), (big_box(fraction=0.1), NEAR_B))
        with pytest.raises(NoFaceFound) as excinfo:
            enroll(FRAME, "Prakash", rec, store, now=NOW, decode=decode_fake)
        assert "2 faces" in str(excinfo.value)
        assert "one person at a time" in str(excinfo.value)
        assert len(store) == 0

    def test_a_distant_face_is_refused_with_advice(self, store, decode_fake):
        with pytest.raises(NoFaceFound, match="come closer"):
            enroll(FRAME, "Prakash", recognizer((tiny_box(), NEAR_A)),
                   store, now=NOW, decode=decode_fake)
        assert len(store) == 0

    def test_the_enrolment_bar_is_higher_than_the_recognition_bar(self):
        # A sample good enough to recognise from is not automatically good
        # enough to be the reference every later answer is measured against.
        from iris.app.vision.robot_eye.faces import MIN_USEFUL_FACE_FRACTION

        assert MIN_ENROLL_FACE_FRACTION > MIN_USEFUL_FACE_FRACTION

    def test_a_face_between_the_two_bars_recognises_but_will_not_enrol(self, store, decode_fake):
        # 2% of the frame: over the recognition floor, under the enrolment one.
        middling = big_box(fraction=0.02)
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((middling, NEAR_B)), store, decode=decode_fake)
        assert sighting.known, "should still be recognisable"
        with pytest.raises(NoFaceFound, match="come closer"):
            enroll(FRAME, "Aditi", recognizer((middling, NEAR_B)), store,
                   now=NOW, decode=decode_fake)

    def test_a_detection_only_backend_explains_it_cannot_remember(self, store, decode_fake):
        rec = FakeRecognizer(faces=[(big_box(), NEAR_A)], spec=DETECT_ONLY_SPEC)
        with pytest.raises(VisionUnavailable) as excinfo:
            enroll(FRAME, "Prakash", rec, store, now=NOW, decode=decode_fake)
        assert "tell them apart" in str(excinfo.value)
        assert excinfo.value.install_hint


class TestDescribeSighting:
    def test_an_empty_room(self, store, decode_fake):
        sighting = look(FRAME, recognizer(), store, decode=decode_fake)
        assert describe_sighting(sighting, store) == "I don't see anyone in front of me."

    def test_the_owner_is_addressed_as_you(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), NEAR_B)), store, decode=decode_fake)
        assert describe_sighting(sighting, store) == "Yes, that's you."

    def test_somebody_else_known_is_named(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        store.enroll("Aditi", FAR, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), FAR)), store, decode=decode_fake)
        assert describe_sighting(sighting, store) == "That's Aditi."

    def test_a_far_away_person_is_distinguished_from_an_unknown_one(self, store, decode_fake):
        # The distinction that stops the robot looking broken when it is only
        # looking across a room.
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        sighting = look(FRAME, recognizer((tiny_box(), NEAR_B)), store, decode=decode_fake)
        said = describe_sighting(sighting, store)
        assert "too far away" in said
        assert "recognise" not in said

    def test_several_distant_people_are_counted(self, store, decode_fake):
        rec = recognizer((tiny_box(), NEAR_A), (Box(200, 200, 8, 8), FAR))
        sighting = look(FRAME, rec, store, decode=decode_fake)
        said = describe_sighting(sighting, store)
        assert "2 people" in said and "too far away" in said

    def test_an_unknown_person_with_nobody_enrolled_offers_the_way_in(self, store, decode_fake):
        sighting = look(FRAME, recognizer((big_box(), NEAR_A)), store, decode=decode_fake)
        said = describe_sighting(sighting, store)
        assert "remember my face" in said

    def test_an_unknown_person_with_others_enrolled_just_says_so(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), FAR)), store, decode=decode_fake)
        assert describe_sighting(sighting, store) == "There's someone there, but I don't recognise them."

    def test_the_owner_and_a_stranger_together(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        rec = recognizer((big_box(fraction=0.2), NEAR_B), (big_box(fraction=0.15), FAR))
        sighting = look(FRAME, rec, store, decode=decode_fake)
        said = describe_sighting(sighting, store)
        assert "you" in said and "someone I don't know" in said

    def test_two_known_people_are_both_named(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        store.enroll("Aditi", FAR, now=NOW, **DLIB)
        rec = recognizer((big_box(fraction=0.2), NEAR_B), (big_box(fraction=0.15), FAR))
        sighting = look(FRAME, rec, store, decode=decode_fake)
        said = describe_sighting(sighting, store)
        assert "you" in said and "Aditi" in said

    def test_several_unknown_people_are_counted(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        rec = recognizer((big_box(fraction=0.2), FAR), (big_box(fraction=0.15), FAR))
        sighting = look(FRAME, rec, store, decode=decode_fake)
        assert describe_sighting(sighting, store) == "I can see 2 people, none of whom I recognise."

    def test_a_known_face_without_an_owner_set_uses_the_name(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), NEAR_B)), store, decode=decode_fake)
        assert describe_sighting(sighting, store) == "That's Prakash."

    def test_it_works_with_no_store_at_all(self, decode_fake):
        sighting = look(FRAME, recognizer((big_box(), NEAR_A)), None, decode=decode_fake)
        assert describe_sighting(sighting, None)


class TestDescribePresenceOnly:
    def test_nobody(self, decode_fake):
        sighting = look(FRAME, recognizer(), None, decode=decode_fake)
        assert describe_presence_only(sighting) == "I don't see anyone in front of me."

    def test_one_person_says_what_is_missing_to_name_them(self, decode_fake):
        sighting = look(FRAME, recognizer((big_box(), NEAR_A)), None, decode=decode_fake)
        said = describe_presence_only(sighting)
        assert "one person" in said and "face recogniser installed" in said

    def test_several_people_are_counted(self, decode_fake):
        rec = recognizer((big_box(fraction=0.2), NEAR_A), (big_box(fraction=0.1), FAR))
        sighting = look(FRAME, rec, None, decode=decode_fake)
        assert "2 people" in describe_presence_only(sighting)


class TestIsOwner:
    def test_the_owner_in_front_of_the_camera(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), NEAR_B)), store, decode=decode_fake)
        assert is_owner(sighting, store)

    def test_somebody_else_is_not_the_owner(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        store.enroll("Aditi", FAR, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), FAR)), store, decode=decode_fake)
        assert not is_owner(sighting, store)

    def test_no_owner_enrolled_means_no(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, **DLIB)
        sighting = look(FRAME, recognizer((big_box(), NEAR_B)), store, decode=decode_fake)
        assert not is_owner(sighting, store)

    def test_an_empty_frame_means_no(self, store, decode_fake):
        store.enroll("Prakash", NEAR_A, now=NOW, owner=True, **DLIB)
        sighting = look(FRAME, recognizer(), store, decode=decode_fake)
        assert not is_owner(sighting, store)
