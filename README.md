# AMB - Agent Message Bus
## "The Nervous System" for AI Agents

A lightweight, broker-agnostic transport layer designed specifically for AI Agents. AMB allows agents to emit signals ("I am thinking," "I am stuck," "I need verification") without knowing who is listening. It decouples the "Sender" from the "Receiver" and handles the async nature of agent communication.

## Features

- **Broker-Agnostic Transport**: Pure transport layer that doesn't care about message content
- **Decoupled Communication**: Senders don't know who receives; receivers don't know who sends
- **Async-First**: Built entirely on `asyncio`/`anyio` to prevent blocking agent thought loops
- **Strict PubSub Interface**: Simple `publish()`, `subscribe()`, and acknowledgment patterns
- **Type-Safe**: Full type hints and Pydantic message validation
- **Communication Patterns**:
  - Fire and forget (fast, no guarantee)
  - Wait for acknowledgment (slower, with confirmation)
  - Request-response (wait for reply)
- **Minimal Core Dependencies**: Only `pydantic` and `anyio`
- **No Business Logic**: The bus stays dumb and fast - it just broadcasts
- **Adapters**:
  - **MemoryAdapter** (InMemoryBroker): For local development/testing, no external dependencies
  - **RedisAdapter**: For production deployments
  - **RabbitMQ & Kafka**: Optional adapters for specific use cases

## Installation

### Core Installation

```bash
pip install amb-core
```

### With Optional Adapters

```bash
# Redis support
pip install amb-core[redis]

# RabbitMQ support
pip install amb-core[rabbitmq]

# Kafka support
pip install amb-core[kafka]

# All adapters
pip install amb-core[all]

# Development dependencies
pip install amb-core[dev]
```

## Quick Start

### Basic Usage with In-Memory Broker (MemoryAdapter)

```python
import asyncio
from amb_core import MessageBus, Message

async def main():
    # Create message bus (uses in-memory broker by default)
    # No external dependencies required!
    async with MessageBus() as bus:
        # Subscribe to a topic
        async def handle_message(msg: Message):
            print(f"Received: {msg.payload}")
        
        await bus.subscribe("agent.thoughts", handle_message)
        
        # Publish a message (fire and forget)
        await bus.publish("agent.thoughts", {"thought": "Hello World!"})
        
        # Wait a bit for async processing
        await asyncio.sleep(0.1)

asyncio.run(main())
```

### Agent Signal Examples

AMB is designed for agent communication patterns. Here are typical signals agents might emit:

```python
# Agent emits a thinking signal
await bus.publish("agent.thinking", {
    "agent_id": "agent-1",
    "thought": "Analyzing user request..."
})

# Agent emits a stuck signal
await bus.publish("agent.stuck", {
    "agent_id": "agent-1", 
    "reason": "Insufficient context",
    "needs": "user_clarification"
})

# Agent requests verification
response = await bus.request("agent.verification", {
    "agent_id": "agent-1",
    "action": "delete_file",
    "requires_approval": True
})
```

### Fire and Forget Pattern

```python
# Fast publishing without waiting for confirmation
await bus.publish(
    "agent.actions",
    {"action": "process", "data": "..."},
    wait_for_confirmation=False  # Default
)
```

### Acknowledgment Pattern (Wait for Verification)

```python
# Slower but ensures message was acknowledged by the broker
msg_id = await bus.publish(
    "critical.task",
    {"task": "important"},
    wait_for_confirmation=True  # Wait for broker acknowledgment
)
print(f"Message {msg_id} acknowledged by broker!")
```

### Request-Response Pattern

```python
# Set up a responder
async def handle_request(msg: Message):
    # Process request
    result = process(msg.payload)
    # Send response
    await bus.reply(msg, {"result": result})

await bus.subscribe("agent.query", handle_request)

# Send request and wait for response
response = await bus.request(
    "agent.query",
    {"query": "What is the status?"},
    timeout=10.0
)
print(f"Response: {response.payload}")
```

### Using Redis Broker

```python
from amb_core import MessageBus
from amb_core.adapters.redis_broker import RedisBroker

async def main():
    # Create Redis broker
    redis_broker = RedisBroker("redis://localhost:6379/0")
    
    # Use with message bus
    async with MessageBus(adapter=redis_broker) as bus:
        await bus.publish("agent.topic", {"data": "hello"})

asyncio.run(main())
```

### Using RabbitMQ Broker

```python
from amb_core import MessageBus
from amb_core.adapters.rabbitmq_broker import RabbitMQBroker

async def main():
    # Create RabbitMQ broker
    rabbitmq_broker = RabbitMQBroker("amqp://guest:guest@localhost/")
    
    async with MessageBus(adapter=rabbitmq_broker) as bus:
        await bus.publish("agent.topic", {"data": "hello"})

asyncio.run(main())
```

### Using Kafka Broker

```python
from amb_core import MessageBus
from amb_core.adapters.kafka_broker import KafkaBroker

async def main():
    # Create Kafka broker
    kafka_broker = KafkaBroker("localhost:9092")
    
    async with MessageBus(adapter=kafka_broker) as bus:
        await bus.publish("agent.topic", {"data": "hello"})

asyncio.run(main())
```

## Message Model

Messages are defined using Pydantic models:

```python
from amb_core import Message, MessagePriority

msg = Message(
    id="unique-id",
    topic="agent.topic",
    payload={"key": "value"},
    priority=MessagePriority.HIGH,
    sender="agent-1",
    correlation_id="request-123",  # For request-response
    reply_to="reply.topic",         # Where to send replies
    ttl=60,                         # Time to live in seconds
    metadata={"custom": "data"}     # Additional metadata
)
```

## Message Priorities

- `MessagePriority.LOW` - Low priority messages
- `MessagePriority.NORMAL` - Normal priority (default)
- `MessagePriority.HIGH` - High priority messages
- `MessagePriority.URGENT` - Urgent messages

## Architecture

AMB follows a strict broker-agnostic architecture with **zero business logic**:

```
┌─────────────────────┐
│    Agent / App      │  ← Your code decides what to send/receive
└──────────┬──────────┘
           │
    ┌──────▼──────┐
    │  MessageBus │        ← Thin wrapper (publish/subscribe/acknowledge)
    └──────┬──────┘
           │
    ┌──────▼──────────┐
    │ BrokerAdapter   │    ← Abstract interface
    └──────┬──────────┘
           │
      ┌────┴─────┐
      │          │
┌─────▼────┐  ┌─▼─────────┐
│  Memory  │  │   Redis   │  ← Concrete implementations
│ Adapter  │  │  Adapter  │
└──────────┘  └───────────┘
```

### Design Principles

1. **No Business Logic**: The bus never decides "if message X, send to Y". It just broadcasts.
2. **Dumb and Fast**: The bus doesn't inspect payloads, validate schemas, or enforce policies.
3. **Broker Agnostic**: Swap brokers without changing your code.
4. **Local-First**: Must work with MemoryAdapter on a laptop without Docker.
5. **Separation of Concerns**:
   - **The Bus**: Transports the envelope
   - **The Receiver**: Opens the envelope and decides what to do
   - **NOT the Bus**: Doesn't verify trust, validate permissions, or enforce rules

## Dependencies

### Core Dependencies (Always Installed)
- `pydantic>=2.0.0` - For message schema validation
- `anyio>=3.0.0` - For async support

### Optional Dependencies (Install as extras)
- `redis>=4.0.0` - For Redis broker adapter
- `aio-pika>=9.0.0` - For RabbitMQ broker adapter
- `aiokafka>=0.8.0` - For Kafka broker adapter

### Forbidden Dependencies
The following packages are explicitly **NOT** used to keep the bus pure and fast:
- `agent-control-plane` - The bus must not import the agent. It doesn't know about agent logic.
- `iatp` - The bus transports the envelope; it does not verify trust (that's the receiver's job)
- `scak` - Not required for core functionality

**Philosophy**: Keep the pipe dumb and fast. No business logic in the bus.

## Development

### Running Tests

```bash
# Install dev dependencies
pip install -e .[dev]

# Run tests
pytest

# Run with coverage
pytest --cov=amb_core --cov-report=html
```

### Building the Package

```bash
python -m build
```

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
