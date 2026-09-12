"""Building the vision-model request, and shaping what comes back."""

from __future__ import annotations

import base64
import json

import pytest

from iris.app.vision.robot_eye.describe import (
    DEFAULT_KIND,
    MAX_FRAME_BYTES,
    PROMPTS,
    SYSTEM_PROMPT,
    FrameTooLarge,
    build_vision_messages,
    data_url,
    estimate_request_bytes,
    no_vision_model_message,
    question_for,
    shape_answer,
)

FRAME = b"\xff\xd8" + b"pretend jpeg" + b"\xff\xd9"


class TestDataUrl:
    def test_the_frame_round_trips_through_base64(self):
        url = data_url(FRAME)
        assert url.startswith("data:image/jpeg;base64,")
        payload = url.split(",", 1)[1]
        assert base64.b64decode(payload) == FRAME

    def test_the_mime_type_can_be_overridden(self):
        assert data_url(FRAME, mime="image/png").startswith("data:image/png;base64,")

    def test_an_empty_frame_is_refused(self):
        for value in (b"", None):
            with pytest.raises(ValueError, match="no frame"):
                data_url(value)          # type: ignore[arg-type]

    def test_an_oversized_frame_is_caught_before_the_request(self):
        with pytest.raises(FrameTooLarge, match="KB"):
            data_url(b"\xff\xd8" + b"x" * MAX_FRAME_BYTES)

    def test_the_size_error_says_what_to_do(self):
        with pytest.raises(FrameTooLarge, match="smaller size"):
            data_url(b"\xff\xd8" + b"x" * MAX_FRAME_BYTES)


class TestQuestionFor:
    def test_the_users_own_words_win(self):
        assert question_for("object", "is this apple ripe?") == "is this apple ripe?"

    def test_a_blank_question_falls_back_to_the_canned_one(self):
        assert question_for("object", "   ") == PROMPTS["object"]
        assert question_for("object", None) == PROMPTS["object"]

    def test_each_kind_has_its_own_question(self):
        assert question_for("ripeness") == PROMPTS["ripeness"]
        assert question_for("text") == PROMPTS["text"]
        assert question_for("object") != question_for("scene")

    def test_an_unknown_kind_falls_back_rather_than_raising(self):
        assert question_for("interpretive-dance") == PROMPTS[DEFAULT_KIND]

    def test_the_kind_is_case_and_space_insensitive(self):
        assert question_for("  RIPENESS ") == PROMPTS["ripeness"]

    def test_no_kind_at_all_gives_the_default(self):
        assert question_for(None) == PROMPTS[DEFAULT_KIND]


class TestBuildVisionMessages:
    def test_the_shape_is_what_an_openai_compatible_api_expects(self):
        messages = build_vision_messages(FRAME, "what is this?")
        assert [m["role"] for m in messages] == ["system", "user"]
        content = messages[1]["content"]
        assert isinstance(content, list)
        assert [part["type"] for part in content] == ["text", "image_url"]
        assert content[0]["text"] == "what is this?"
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_the_instruction_comes_before_the_image(self):
        # Models follow an instruction ahead of an image more reliably than
        # one trailing it.
        content = build_vision_messages(FRAME, "read the label")[1]["content"]
        assert content[0]["type"] == "text"

    def test_the_system_prompt_is_included_by_default(self):
        messages = build_vision_messages(FRAME, "hi")
        assert messages[0]["content"] == SYSTEM_PROMPT

    def test_the_system_prompt_can_be_dropped(self):
        messages = build_vision_messages(FRAME, "hi", system_prompt=None)
        assert [m["role"] for m in messages] == ["user"]

    def test_the_system_prompt_forbids_guessing_who_someone_is(self):
        # Identity is answered locally from enrolled faces, never by asking a
        # hosted model to name a person.
        assert "never guess at the identity" in SYSTEM_PROMPT

    def test_the_detail_level_travels_with_the_image(self):
        content = build_vision_messages(FRAME, "hi", detail="low")[1]["content"]
        assert content[1]["image_url"]["detail"] == "low"

    def test_an_invalid_detail_level_is_refused(self):
        with pytest.raises(ValueError, match="detail must be"):
            build_vision_messages(FRAME, "hi", detail="ultra")

    def test_the_whole_thing_is_json_serialisable(self):
        # It has to survive being posted as a request body.
        payload = json.dumps(build_vision_messages(FRAME, "what is this?"))
        assert "image_url" in payload


class TestEstimateRequestBytes:
    def test_the_estimate_exceeds_the_raw_frame(self):
        # base64 is 4 bytes per 3, so the request is always bigger.
        assert estimate_request_bytes(FRAME) > len(FRAME)

    def test_it_grows_with_the_frame(self):
        assert estimate_request_bytes(b"x" * 1000) > estimate_request_bytes(b"x" * 100)

    def test_the_estimate_is_close_to_the_real_thing(self):
        frame = b"\xff\xd8" + b"x" * 5000 + b"\xff\xd9"
        actual = len(json.dumps(build_vision_messages(frame, "hi")))
        estimate = estimate_request_bytes(frame)
        assert 0.75 <= estimate / actual <= 1.5


class TestShapeAnswer:
    def test_a_short_answer_is_spoken_whole(self):
        shaped = shape_answer("A red apple.", "what is this?")
        assert shaped.speech == "A red apple."
        assert shaped.display == "A red apple."

    def test_a_long_answer_is_clipped_for_speech_but_kept_for_the_screen(self):
        text = ("A red apple. It looks ripe. The skin is unblemished. "
                "There is a mug behind it. The mug is blue.")
        shaped = shape_answer(text, "what is this?")
        assert shaped.speech == "A red apple. It looks ripe."
        assert shaped.display == text

    def test_the_spoken_length_is_adjustable(self):
        text = "One. Two. Three. Four."
        assert shape_answer(text, "q", max_spoken_sentences=3).speech == "One. Two. Three."
        assert shape_answer(text, "q", max_spoken_sentences=1).speech == "One."

    def test_whitespace_and_newlines_are_flattened(self):
        shaped = shape_answer("A red\n\n  apple.\tRipe.", "q")
        assert shaped.speech == "A red apple. Ripe."

    def test_an_answer_with_no_full_stop_is_still_spoken(self):
        shaped = shape_answer("looks like a banana", "q")
        assert shaped.speech == "looks like a banana"

    def test_questions_and_exclamations_end_sentences_too(self):
        shaped = shape_answer("Is that a mango? It looks ripe! And a mug.", "q")
        assert shaped.speech == "Is that a mango? It looks ripe!"

    def test_an_empty_answer_says_so_rather_than_saying_nothing(self):
        for value in ("", "   ", None):
            shaped = shape_answer(value, "q")     # type: ignore[arg-type]
            assert "nothing back" in shaped.speech
            assert shaped.display

    def test_the_metadata_is_carried_through(self):
        shaped = shape_answer("A mug.", "what is this?", model="some-vision-model",
                              frame_bytes=4096)
        assert shaped.question == "what is this?"
        assert shaped.model == "some-vision-model"
        assert shaped.frame_bytes == 4096


class TestNoVisionModelMessage:
    def test_it_names_the_setting_to_change(self):
        message = no_vision_model_message()
        assert "VISION_MODEL" in message

    def test_it_distinguishes_a_missing_model_from_a_broken_camera(self):
        message = no_vision_model_message()
        assert "camera is working" in message
