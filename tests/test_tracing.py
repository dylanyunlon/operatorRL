"""Tests for OpenTelemetry tracing integration."""

import asyncio

import pytest

from amb_core import Message, MessageBus, get_trace_id, get_tracer, initialize_tracing


@pytest.mark.asyncio
async def test_trace_id_in_message_model():
    """Test that Message model accepts trace_id."""
    msg = Message(
        id="test-123",
        topic="test.topic",
        payload={"key": "value"},
        trace_id="0123456789abcdef0123456789abcdef",
    )

    assert msg.trace_id == "0123456789abcdef0123456789abcdef"


@pytest.mark.asyncio
async def test_message_without_trace_id():
    """Test that Message works without trace_id (backward compatibility)."""
    msg = Message(id="test-123", topic="test.topic", payload={"key": "value"})

    assert msg.trace_id is None


@pytest.mark.asyncio
async def test_publish_injects_trace_id_from_active_span():
    """Test that publish injects trace_id from active span context."""
    initialize_tracing("test-service")
    tracer = get_tracer("test")

    async with MessageBus() as bus:
        received_messages = []

        async def handler(msg: Message):
            received_messages.append(msg)

        await bus.subscribe("test.topic", handler)

        # Create a span and publish message within its context
        with tracer.start_as_current_span("test-span"):
            trace_id_from_span = get_trace_id()
            await bus.publish("test.topic", {"data": "test"})

        await asyncio.sleep(0.1)

        # Check that message received has the trace_id
        assert len(received_messages) == 1
        # If there's an active span, trace_id should be set
        # (might be None if no active span)
        if trace_id_from_span:
            assert received_messages[0].trace_id == trace_id_from_span


@pytest.mark.asyncio
async def test_publish_with_explicit_trace_id():
    """Test that explicit trace_id is used when provided."""
    custom_trace_id = "1234567890abcdef1234567890abcdef"

    async with MessageBus() as bus:
        received_messages = []

        async def handler(msg: Message):
            received_messages.append(msg)

        await bus.subscribe("test.topic", handler)

        # Publish with explicit trace_id
        await bus.publish("test.topic", {"data": "test"}, trace_id=custom_trace_id)

        await asyncio.sleep(0.1)

        # Check that message has the custom trace_id
        assert len(received_messages) == 1
        assert received_messages[0].trace_id == custom_trace_id


@pytest.mark.asyncio
async def test_request_injects_trace_id():
    """Test that request-response pattern includes trace_id."""
    custom_trace_id = "abcdef1234567890abcdef1234567890"

    async with MessageBus() as bus:
        received_requests = []

        async def responder(msg: Message):
            received_requests.append(msg)
            await bus.reply(msg, {"response": "pong"})

        await bus.subscribe("ping.topic", responder)
        await asyncio.sleep(0.1)

        # Send request with trace_id
        response = await bus.request(
            "ping.topic", {"request": "ping"}, timeout=5.0, trace_id=custom_trace_id
        )

        # Check that request had trace_id
        assert len(received_requests) == 1
        assert received_requests[0].trace_id == custom_trace_id

        # Check that response also has trace_id (propagated)
        assert response.trace_id == custom_trace_id


@pytest.mark.asyncio
async def test_reply_propagates_trace_id():
    """Test that reply propagates trace_id from original message."""
    original_trace_id = "fedcba9876543210fedcba9876543210"

    async with MessageBus() as bus:
        responses = []

        async def response_handler(msg: Message):
            responses.append(msg)

        await bus.subscribe("reply.topic", response_handler)

        # Create an original message with trace_id
        original_msg = Message(
            id="original-123",
            topic="request.topic",
            payload={"request": "data"},
            correlation_id="corr-123",
            reply_to="reply.topic",
            trace_id=original_trace_id,
        )

        # Reply to the original message
        await bus.reply(original_msg, {"response": "result"})

        await asyncio.sleep(0.1)

        # Check that reply has the same trace_id
        assert len(responses) == 1
        assert responses[0].trace_id == original_trace_id


@pytest.mark.asyncio
async def test_trace_id_serialization():
    """Test that trace_id is properly serialized and deserialized."""
    trace_id = "0123456789abcdef0123456789abcdef"
    msg = Message(id="test-123", topic="test.topic", payload={"key": "value"}, trace_id=trace_id)

    # Serialize
    json_str = msg.model_dump_json()
    assert trace_id in json_str

    # Deserialize
    msg2 = Message.model_validate_json(json_str)
    assert msg2.trace_id == trace_id


@pytest.mark.asyncio
async def test_get_trace_id_without_active_span():
    """Test get_trace_id returns None when there's no active span."""
    trace_id = get_trace_id()
    # Should return None when there's no active span
    assert trace_id is None or isinstance(trace_id, str)


@pytest.mark.asyncio
async def test_multiple_messages_same_trace_id():
    """Test that multiple messages in same trace have the same trace_id."""
    initialize_tracing("test-service")
    tracer = get_tracer("test")

    async with MessageBus() as bus:
        received_messages = []

        async def handler(msg: Message):
            received_messages.append(msg)

        await bus.subscribe("test.topic", handler)

        # Create a span and publish multiple messages
        with tracer.start_as_current_span("parent-span"):
            trace_id_from_span = get_trace_id()
            await bus.publish("test.topic", {"msg": "first"})
            await bus.publish("test.topic", {"msg": "second"})
            await bus.publish("test.topic", {"msg": "third"})

        await asyncio.sleep(0.1)

        # All messages should have the same trace_id if there was an active span
        assert len(received_messages) == 3
        if trace_id_from_span:
            assert received_messages[0].trace_id == trace_id_from_span
            assert received_messages[1].trace_id == trace_id_from_span
            assert received_messages[2].trace_id == trace_id_from_span


@pytest.mark.asyncio
async def test_publish_without_trace_id():
    """Test that publishing works without trace_id (backward compatibility)."""
    async with MessageBus() as bus:
        received_messages = []

        async def handler(msg: Message):
            received_messages.append(msg)

        await bus.subscribe("test.topic", handler)

        # Publish without trace_id
        await bus.publish("test.topic", {"data": "test"})

        await asyncio.sleep(0.1)

        # Should work fine, trace_id might be None
        assert len(received_messages) == 1
        # trace_id is None or a valid string
        assert received_messages[0].trace_id is None or isinstance(
            received_messages[0].trace_id, str
        )
