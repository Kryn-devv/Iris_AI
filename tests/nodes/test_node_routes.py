"""Tests for the node endpoints: the socket's front door and the voice loop."""

from __future__ import annotations

import io
import wave

import pytest
from fastapi.testclient import TestClient

from iris.app.api.routes.nodes import ALLOWED_RATES, MAX_AUDIO_BYTES, MIN_AUDIO_BYTES, pcm_to_wav
from iris.app.main import app


@pytest.fixture()
def client():
    # No lifespan: these tests exercise routing and validation, not startup.
    return TestClient(app)


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setattr("iris.app.core.config.settings.NODE_LINK_TOKEN", "s3cret-token")
    monkeypatch.setattr("iris.app.core.config.settings.NODE_LINK_ENABLED", True)
    monkeypatch.setattr("iris.app.core.config.settings.NODE_VOICE_ENABLED", True)
    return "s3cret-token"


def silence(seconds: float = 1.0, rate: int = 16000) -> bytes:
    return b"\x00\x00" * int(rate * seconds)


class TestPcmToWav:
    """The node streams raw samples because a WAV header needs the total length
    up front, which a device recording and uploading at once does not know."""

    def test_produces_a_readable_wav(self):
        data = pcm_to_wav(silence(0.5), 16000)
        with wave.open(io.BytesIO(data), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == 16000
            assert wav.getnframes() == 8000

    def test_header_is_the_expected_size(self):
        pcm = silence(0.25)
        assert len(pcm_to_wav(pcm, 16000)) == len(pcm) + 44

    @pytest.mark.parametrize("rate", ALLOWED_RATES)
    def test_every_allowed_rate_round_trips(self, rate):
        data = pcm_to_wav(b"\x01\x02" * 100, rate)
        with wave.open(io.BytesIO(data), "rb") as wav:
            assert wav.getframerate() == rate

    def test_empty_pcm_is_still_a_valid_wav(self):
        with wave.open(io.BytesIO(pcm_to_wav(b"", 16000)), "rb") as wav:
            assert wav.getnframes() == 0

    def test_samples_survive_unchanged(self):
        pcm = bytes(range(256)) * 4
        with wave.open(io.BytesIO(pcm_to_wav(pcm, 16000)), "rb") as wav:
            assert wav.readframes(wav.getnframes()) == pcm


class TestNodeListing:
    def test_reports_whether_links_are_configured(self, client, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.NODE_LINK_TOKEN", None)
        body = client.get("/api/v1/nodes").json()
        assert body["link_configured"] is False
        assert body["count"] == 0
        assert body["nodes"] == []

    def test_reports_configured_when_a_token_is_set(self, client, token):
        assert client.get("/api/v1/nodes").json()["link_configured"] is True


class TestVoiceEndpointGuards:
    """The audio path is the one an ESP32 hits directly, so its refusals have
    to be precise: a device cannot read a stack trace."""

    def test_no_token_configured_is_refused_with_instructions(self, client, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.NODE_LINK_TOKEN", None)
        response = client.post("/api/v1/nodes/voice?token=anything", content=silence())
        assert response.status_code == 503
        assert "NODE_LINK_TOKEN" in response.json()["detail"]

    def test_a_wrong_token_is_rejected(self, client, token):
        response = client.post("/api/v1/nodes/voice?token=nope", content=silence())
        assert response.status_code == 401

    def test_a_missing_token_is_rejected(self, client, token):
        assert client.post("/api/v1/nodes/voice", content=silence()).status_code == 401

    def test_the_feature_can_be_turned_off(self, client, token, monkeypatch):
        monkeypatch.setattr("iris.app.core.config.settings.NODE_VOICE_ENABLED", False)
        response = client.post(f"/api/v1/nodes/voice?token={token}", content=silence())
        assert response.status_code == 503

    def test_an_odd_sample_rate_is_refused(self, client, token):
        response = client.post(
            f"/api/v1/nodes/voice?token={token}&rate=12345", content=silence()
        )
        assert response.status_code == 400
        assert "rate must be" in response.json()["detail"]

    def test_a_recording_that_is_too_short_is_refused(self, client, token):
        response = client.post(f"/api/v1/nodes/voice?token={token}", content=b"\x00" * 10)
        assert response.status_code == 400
        assert "too short" in response.json()["detail"]

    def test_a_recording_that_is_too_long_is_refused(self, client, token):
        response = client.post(
            f"/api/v1/nodes/voice?token={token}", content=b"\x00" * (MAX_AUDIO_BYTES + 2)
        )
        assert response.status_code == 413

    def test_a_missing_stt_engine_says_what_to_install(self, client, token, monkeypatch):
        """The most likely real failure on a fresh VPS, so it must not be a 500."""
        async def no_engine(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.transcribe", no_engine
        )
        response = client.post(f"/api/v1/nodes/voice?token={token}", content=silence())
        assert response.status_code == 503
        assert "faster-whisper" in response.json()["detail"]

    def test_unintelligible_audio_is_a_422_not_a_crash(self, client, token, monkeypatch):
        async def blank(*args, **kwargs):
            return {"text": "   "}

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.transcribe", blank
        )
        response = client.post(f"/api/v1/nodes/voice?token={token}", content=silence())
        assert response.status_code == 422

    def test_the_limits_are_sane_relative_to_each_other(self):
        assert MIN_AUDIO_BYTES < MAX_AUDIO_BYTES
        assert 16000 in ALLOWED_RATES


class TestVoiceRoundTrip:
    @pytest.mark.parametrize("engine_suffix", [".wav"])
    def test_audio_in_reply_audio_out(self, client, token, monkeypatch, tmp_path, engine_suffix):
        heard, spoken = "turn on the light", "Turning on the light."

        async def fake_transcribe(*args, **kwargs):
            return {"text": heard}

        class FakeReply:
            speech = spoken
            response = spoken

        async def fake_kernel(**kwargs):
            assert kwargs["user_input"] == heard
            return FakeReply()

        out = tmp_path / f"reply{engine_suffix}"
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x10\x00" * 2205)

        async def fake_synthesize(text, language="en"):
            assert text == spoken
            return str(out)

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.transcribe", fake_transcribe
        )
        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.synthesize", fake_synthesize
        )
        monkeypatch.setattr(
            "iris.app.agent.kernel.default_kernel.process_request", fake_kernel
        )

        response = client.post(
            f"/api/v1/nodes/voice?token={token}&node=face", content=silence()
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "audio/wav"
        # The device parses the rate out of the header, which is why no
        # resampling happens server-side.
        with wave.open(io.BytesIO(response.content), "rb") as wav:
            assert wav.getframerate() == 22050
            assert wav.getnchannels() == 1
        assert response.headers["X-Iris-Heard"] == heard
        assert response.headers["X-Iris-Reply"] == spoken

    def test_a_missing_tts_engine_says_what_to_install(self, client, token, monkeypatch):
        async def fake_transcribe(*args, **kwargs):
            return {"text": "hello"}

        class FakeReply:
            speech = "Hi there."
            response = "Hi there."

        async def fake_kernel(**kwargs):
            return FakeReply()

        async def no_tts(text, language="en"):
            return None

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.transcribe", fake_transcribe
        )
        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.synthesize", no_tts
        )
        monkeypatch.setattr(
            "iris.app.agent.kernel.default_kernel.process_request", fake_kernel
        )
        response = client.post(f"/api/v1/nodes/voice?token={token}", content=silence())
        assert response.status_code == 503
        assert "piper" in response.json()["detail"]

    def test_non_ascii_headers_do_not_break_the_response(self, client, token, monkeypatch):
        """Header values must be latin-1 encodable; Hindi replies are not."""
        async def fake_transcribe(*args, **kwargs):
            return {"text": "गैस का स्तर क्या है"}

        class FakeReply:
            speech = "गैस का स्तर सामान्य है।"
            response = speech

        async def fake_kernel(**kwargs):
            return FakeReply()

        async def fake_synthesize(text, language="en"):
            return None      # stop before the file stage; headers are the point

        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.transcribe", fake_transcribe
        )
        monkeypatch.setattr(
            "iris.app.voice.service.default_voice_service.synthesize", fake_synthesize
        )
        monkeypatch.setattr(
            "iris.app.agent.kernel.default_kernel.process_request", fake_kernel
        )
        response = client.post(f"/api/v1/nodes/voice?token={token}", content=silence())
        assert response.status_code == 503     # not a UnicodeEncodeError


class TestMiddlewareInteraction:
    def test_the_voice_path_carries_its_own_credential(self):
        """It must not also require the IRIS user token — firmware carrying two
        different tokens would be a worse design, not a safer one."""
        from iris.app.main import _NODE_TOKEN_PATHS, _is_public_path

        assert "/api/v1/nodes/voice" in _NODE_TOKEN_PATHS
        assert _is_public_path("/api/v1/nodes/voice") is True
        # The listing keeps normal auth: it is for the UI, not for a board.
        assert _is_public_path("/api/v1/nodes") is False
