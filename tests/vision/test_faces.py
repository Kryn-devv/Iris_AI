"""The comparison maths, which is where a wrong answer would come from."""

from __future__ import annotations

import math

import pytest

from tests.vision.conftest import FAR, NEAR_A, NEAR_B, big_box, tiny_box
from iris.app.vision.robot_eye.errors import NoFaceFound, VisionUnavailable
from iris.app.vision.robot_eye.faces import (
    BACKENDS,
    BACKENDS_BY_NAME,
    COSINE,
    EUCLIDEAN,
    MIN_USEFUL_FACE_FRACTION,
    Box,
    best_match,
    confidence_from_distance,
    cosine_distance,
    distance,
    euclidean_distance,
    face_fraction,
    l2_norm,
    l2_normalize,
    merge_embeddings,
    primary_face,
    select_backend,
)


class TestDistances:
    def test_identical_vectors_are_zero_apart(self):
        assert euclidean_distance(NEAR_A, NEAR_A) == 0.0
        assert cosine_distance(NEAR_A, NEAR_A) == pytest.approx(0.0, abs=1e-12)

    def test_cosine_of_an_identical_pair_never_goes_negative(self):
        # Float error can make the similarity 1.0000000002, and a negative
        # distance would sort ahead of a genuine perfect match.
        vector = tuple(0.1 * i for i in range(1, 129))
        assert cosine_distance(vector, vector) >= 0.0

    def test_cosine_ignores_length_but_euclidean_does_not(self):
        # A brighter photo of the same face comes back as a longer vector.
        doubled = tuple(v * 2 for v in NEAR_A)
        assert cosine_distance(NEAR_A, doubled) == pytest.approx(0.0, abs=1e-12)
        assert euclidean_distance(NEAR_A, doubled) > 0.5

    def test_opposite_directions_are_two_apart_in_cosine(self):
        flipped = tuple(-v for v in NEAR_A)
        assert cosine_distance(NEAR_A, flipped) == pytest.approx(2.0)

    def test_a_zero_vector_is_exactly_undecided(self):
        # No information should not read as "very similar".
        assert cosine_distance(NEAR_A, (0.0, 0.0, 0.0, 0.0)) == 1.0

    def test_mismatched_widths_are_refused_not_guessed(self):
        with pytest.raises(ValueError, match="Cannot compare"):
            euclidean_distance((1.0, 2.0), (1.0, 2.0, 3.0))
        with pytest.raises(ValueError, match="Cannot compare"):
            cosine_distance((1.0, 2.0), (1.0, 2.0, 3.0))

    def test_distance_dispatches_and_rejects_nonsense(self):
        assert distance(NEAR_A, FAR, EUCLIDEAN) == euclidean_distance(NEAR_A, FAR)
        assert distance(NEAR_A, FAR, COSINE) == cosine_distance(NEAR_A, FAR)
        with pytest.raises(ValueError, match="Unknown distance metric"):
            distance(NEAR_A, FAR, "manhattan")

    def test_distances_are_symmetric(self):
        assert euclidean_distance(NEAR_A, FAR) == pytest.approx(euclidean_distance(FAR, NEAR_A))
        assert cosine_distance(NEAR_A, FAR) == pytest.approx(cosine_distance(FAR, NEAR_A))


class TestNormalization:
    def test_normalizing_gives_unit_length(self):
        assert l2_norm(l2_normalize((3.0, 4.0))) == pytest.approx(1.0)

    def test_normalizing_a_zero_vector_does_not_divide_by_zero(self):
        assert l2_normalize((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)

    def test_normalizing_preserves_direction(self):
        assert l2_normalize((3.0, 4.0)) == pytest.approx((0.6, 0.8))


class TestConfidence:
    def test_a_perfect_match_is_full_confidence(self):
        assert confidence_from_distance(0.0, 0.5) == 1.0

    def test_sitting_exactly_on_the_threshold_reads_as_a_coin_flip(self):
        assert confidence_from_distance(0.5, 0.5) == pytest.approx(0.5)

    def test_confidence_bottoms_out_and_never_goes_negative(self):
        assert confidence_from_distance(1.0, 0.5) == 0.0
        assert confidence_from_distance(50.0, 0.5) == 0.0

    def test_confidence_falls_as_distance_grows(self):
        values = [confidence_from_distance(d / 10.0, 0.5) for d in range(0, 12)]
        assert values == sorted(values, reverse=True)

    def test_a_meaningless_threshold_yields_no_confidence(self):
        # Detection-only backends carry threshold 0; they must not report 1.0.
        assert confidence_from_distance(0.0, 0.0) == 0.0


class TestMergeEmbeddings:
    def test_merging_one_sample_returns_it(self):
        assert merge_embeddings([(1.0, 2.0, 3.0)], EUCLIDEAN) == (1.0, 2.0, 3.0)

    def test_euclidean_merge_is_a_plain_average(self):
        merged = merge_embeddings([(0.0, 0.0), (2.0, 4.0)], EUCLIDEAN)
        assert merged == pytest.approx((1.0, 2.0))

    def test_cosine_merge_returns_a_unit_vector(self):
        merged = merge_embeddings([(1.0, 0.0), (0.0, 1.0)], COSINE)
        assert l2_norm(merged) == pytest.approx(1.0)

    def test_cosine_merge_gives_every_sample_an_equal_vote(self):
        # The long vector is the same direction as the short one, so a
        # length-weighted average would land on it rather than between.
        merged = merge_embeddings([(10.0, 0.0), (0.0, 1.0)], COSINE)
        assert merged == pytest.approx((math.sqrt(0.5), math.sqrt(0.5)))

    def test_merging_nothing_or_ragged_input_is_an_error(self):
        with pytest.raises(ValueError, match="No embeddings"):
            merge_embeddings([], EUCLIDEAN)
        with pytest.raises(ValueError, match="different lengths"):
            merge_embeddings([(1.0,), (1.0, 2.0)], EUCLIDEAN)


class TestBestMatch:
    def test_a_close_face_matches(self):
        match = best_match(NEAR_A, [("Prakash", NEAR_B)], EUCLIDEAN, 0.5)
        assert match is not None
        assert match.name == "Prakash"
        assert match.is_match

    def test_a_distant_face_matches_nobody(self):
        assert best_match(NEAR_A, [("Prakash", FAR)], EUCLIDEAN, 0.5) is None

    def test_it_returns_the_closest_not_the_first_acceptable(self):
        # Both are inside the threshold; only one is right.
        candidates = [("Almost", (0.6, 0.0, 0.0, 0.0)), ("Prakash", NEAR_B)]
        match = best_match(NEAR_A, candidates, EUCLIDEAN, 0.5)
        assert match is not None and match.name == "Prakash"

    def test_order_does_not_change_the_answer(self):
        candidates = [("Almost", (0.6, 0.0, 0.0, 0.0)), ("Prakash", NEAR_B)]
        forward = best_match(NEAR_A, candidates, EUCLIDEAN, 0.5)
        backward = best_match(NEAR_A, list(reversed(candidates)), EUCLIDEAN, 0.5)
        assert forward is not None and backward is not None
        assert forward.name == backward.name

    def test_no_candidates_means_no_match(self):
        assert best_match(NEAR_A, [], EUCLIDEAN, 0.5) is None

    def test_a_stored_face_of_the_wrong_width_is_skipped_not_fatal(self):
        candidates = [("Stale", (1.0, 0.0)), ("Prakash", NEAR_B)]
        match = best_match(NEAR_A, candidates, EUCLIDEAN, 0.5)
        assert match is not None and match.name == "Prakash"

    def test_only_wrong_width_candidates_means_no_match(self):
        assert best_match(NEAR_A, [("Stale", (1.0, 0.0))], EUCLIDEAN, 0.5) is None

    def test_the_match_carries_the_threshold_it_was_judged_against(self):
        match = best_match(NEAR_A, [("Prakash", NEAR_B)], EUCLIDEAN, 0.5)
        assert match is not None
        assert match.threshold == 0.5
        assert match.metric == EUCLIDEAN
        assert 0.0 <= match.confidence <= 1.0


class TestPrimaryFace:
    def test_the_biggest_face_wins(self):
        small = Box(0, 0, 40, 40)
        large = Box(400, 300, 200, 200)
        assert primary_face([small, large], 800, 600) == large

    def test_equal_sizes_break_toward_the_middle(self):
        centred = Box(x=350, y=250, w=100, h=100)
        cornered = Box(x=0, y=0, w=100, h=100)
        assert primary_face([cornered, centred], 800, 600) == centred

    def test_no_faces_is_an_error_not_a_none(self):
        with pytest.raises(NoFaceFound):
            primary_face([], 800, 600)

    def test_a_photo_on_the_wall_loses_to_the_person_in_front(self):
        # The whole point: the nearest face is the one addressing the robot.
        person = big_box(fraction=0.15)
        picture_on_wall = Box(x=700, y=20, w=50, h=60)
        assert primary_face([picture_on_wall, person], 800, 600) == person


class TestFaceFraction:
    def test_a_full_frame_face_is_all_of_it(self):
        assert face_fraction(Box(0, 0, 800, 600), 800, 600) == pytest.approx(1.0)

    def test_a_quarter_sized_face_is_a_quarter(self):
        assert face_fraction(Box(0, 0, 400, 300), 800, 600) == pytest.approx(0.25)

    def test_the_fraction_is_clamped_into_zero_to_one(self):
        assert face_fraction(Box(0, 0, 5000, 5000), 800, 600) == 1.0
        assert face_fraction(Box(0, 0, -5, -5), 800, 600) == 0.0

    def test_a_zero_sized_frame_does_not_divide_by_zero(self):
        assert face_fraction(Box(0, 0, 10, 10), 0, 0) == 1.0

    def test_a_distant_face_falls_below_the_useful_floor(self):
        assert face_fraction(tiny_box(), 800, 600) < MIN_USEFUL_FACE_FRACTION


class TestBackendSelection:
    def test_the_preferred_backend_is_picked_when_installed(self):
        spec = select_backend(lambda module: True, preference="insightface")
        assert spec.name == "insightface"

    def test_preference_order_is_honoured_when_everything_is_installed(self):
        assert select_backend(lambda module: True).name == BACKENDS[0].name

    def test_it_falls_through_to_whatever_is_installed(self):
        available = {"insightface", "numpy"}
        spec = select_backend(lambda module: module in available)
        assert spec.name == "insightface"

    def test_a_detection_only_backend_is_refused_for_recognition(self):
        available = {"cv2", "numpy"}
        with pytest.raises(VisionUnavailable) as excinfo:
            select_backend(lambda module: module in available, need_embeddings=True)
        assert "recognise faces" in str(excinfo.value)

    def test_but_accepted_when_only_detection_is_needed(self):
        available = {"cv2", "numpy"}
        spec = select_backend(lambda module: module in available, need_embeddings=False)
        assert spec.name == "opencv"
        assert not spec.can_embed

    def test_asking_for_a_detection_only_backend_by_name_explains_itself(self):
        with pytest.raises(VisionUnavailable, match="not recognise them"):
            select_backend(lambda module: True, preference="opencv", need_embeddings=True)

    def test_nothing_installed_gives_install_hints_for_every_option(self):
        with pytest.raises(VisionUnavailable) as excinfo:
            select_backend(lambda module: False)
        hint = excinfo.value.install_hint
        assert "face-recognition" in hint
        assert "insightface" in hint

    def test_an_unknown_backend_name_lists_the_real_ones(self):
        with pytest.raises(VisionUnavailable, match="not a face backend"):
            select_backend(lambda module: True, preference="magic")

    def test_a_partly_installed_backend_is_not_used(self):
        # face_recognition without numpy cannot work; it must fall through
        # rather than be chosen and crash on first use.
        available = {"face_recognition", "insightface"}
        with pytest.raises(VisionUnavailable):
            select_backend(lambda module: module in available)

    def test_every_backend_declares_what_it_needs(self):
        for spec in BACKENDS:
            assert spec.modules, f"{spec.name} lists no modules"
            assert spec.install_hint, f"{spec.name} has no install hint"
            assert spec.metric in (EUCLIDEAN, COSINE)
            if spec.can_embed:
                assert spec.threshold > 0, f"{spec.name} can embed but has no threshold"

    def test_the_registry_and_the_lookup_agree(self):
        assert set(BACKENDS_BY_NAME) == {spec.name for spec in BACKENDS}
