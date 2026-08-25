"""WebSocket and SSE endpoint tests."""

import json

from fastapi.testclient import TestClient

from iris.app.main import app


def test_websocket_chat_roundtrip():
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            assert json.loads(ws.receive_text())["type"] == "pong"

            ws.send_text(json.dumps({"type": "chat", "message": "what is 6 * 7"}))
            # Bus events stream interleaved with the final response.
            for _ in range(50):
                data = json.loads(ws.receive_text())
                if data["type"] == "response":
                    assert "42" in data["response"]
                    assert data["handler"] == "nlu"
                    break
            else:
                raise AssertionError("No response frame received")


def test_websocket_plain_text_treated_as_chat():
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws") as ws:
            ws.send_text("hello")
            for _ in range(50):
                data = json.loads(ws.receive_text())
                if data["type"] == "response":
                    assert data["handler"] == "smalltalk"
                    break
            else:
                raise AssertionError("No response frame received")
