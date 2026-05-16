from __future__ import annotations

import pytest
from pydantic import ValidationError

from caesar.llm.gateway import ChatMessage, ChatResponse


def test_chat_message_requires_content():
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="")


def test_chat_message_roundtrips():
    m = ChatMessage(role="user", content="hello")
    assert m.model_dump() == {"role": "user", "content": "hello"}


def test_chat_response_holds_usage():
    r = ChatResponse(content="hi", model="x", input_tokens=3, output_tokens=4)
    assert r.input_tokens == 3
    assert r.output_tokens == 4
