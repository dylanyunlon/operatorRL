# Quick Start Guide - IATP v0.2.0

Get up and running with IATP in under 2 minutes.

## 🚀 One-Line Deploy (Docker)

```bash
docker-compose up -d
```

That's it! You now have:
- **Secure Bank Agent** + Sidecar at `http://localhost:8081`
- **Honeypot Agent** + Sidecar at `http://localhost:9001`

Test it:
```bash
# Check sidecar health
curl http://localhost:8081/health

# Get agent capabilities (the IATP handshake)
curl http://localhost:8081/.well-known/agent-manifest

# Send a request through the sidecar
curl -X POST http://localhost:8081/proxy \
  -H "Content-Type: application/json" \
  -d '{"action": "check_balance", "account": "12345"}'
```

## 📦 Installation

### Option 1: PyPI (Recommended)

```bash
pip install inter-agent-trust-protocol
```

### Option 2: Source

```bash
git clone https://github.com/imran-siddique/inter-agent-trust-protocol.git
cd inter-agent-trust-protocol
pip install -e .
```

### Option 3: Docker

```bash
docker-compose up -d
```

## 🏃 Running the Sidecar

### Method 1: Direct (uvicorn)

```bash
# Set environment variables
export IATP_AGENT_URL=http://localhost:8000
export IATP_AGENT_ID=my-agent
export IATP_TRUST_LEVEL=trusted

# Run the sidecar
uvicorn iatp.main:app --host 0.0.0.0 --port 8081
```

### Method 2: Docker

```bash
docker build -t iatp-sidecar .
docker run -p 8081:8081 \
  -e IATP_AGENT_URL=http://my-agent:8000 \
  -e IATP_AGENT_ID=my-agent \
  -e IATP_TRUST_LEVEL=trusted \
  iatp-sidecar
```

## Your First Protected Agent

### Step 1: Create Your Agent

`my_agent.py`:
```python
from fastapi import FastAPI

app = FastAPI()

@app.post("/process")
async def handle_task(request: dict):
    # Your agent logic here
    return {"status": "success", "result": "Task completed"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

### Step 2: Run Agent + Sidecar

Terminal 1 (Your Agent):
```bash
python my_agent.py
```

Terminal 2 (IATP Sidecar):
```bash
IATP_AGENT_URL=http://localhost:8000 uvicorn iatp.main:app --port 8081
```

### Step 3: Test It

```bash
# All requests now go through the sidecar (port 8081)
curl -X POST http://localhost:8081/proxy \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello from IATP!"}'
```

## 🎯 Using the Python SDK

For programmatic control, use the SDK:

`run_sidecar.py`:
```python
from iatp.models import (
    CapabilityManifest,
    TrustLevel,
    AgentCapabilities,
    ReversibilityLevel,
    PrivacyContract,
    RetentionPolicy
)
from iatp.sidecar import create_sidecar

# Define your agent's capabilities
manifest = CapabilityManifest(
    agent_id="my-agent",
    trust_level=TrustLevel.TRUSTED,
    capabilities=AgentCapabilities(
        reversibility=ReversibilityLevel.FULL,
        idempotency=True,
        concurrency_limit=100,
        sla_latency_ms=2000
    ),
    privacy_contract=PrivacyContract(
        retention=RetentionPolicy.EPHEMERAL,
        human_in_loop=False,
        training_consent=False
    )
)

# Create sidecar pointing to your agent
sidecar = create_sidecar(
    agent_url="http://localhost:8000",
    manifest=manifest,
    port=8001
)

# Run sidecar
sidecar.run()
```

Terminal 3 (Test):
```bash
curl -X POST http://localhost:8001/proxy \
  -H 'Content-Type: application/json' \
  -d '{"task": "hello"}'
```

**That's it!** Your agent is now protected by IATP.

## Using the Go Sidecar (Production)

For production deployments, use the Go sidecar for better performance:

### Build

```bash
cd sidecar/go
go build -o iatp-sidecar main.go
```

### Run

```bash
export IATP_AGENT_URL=http://localhost:8000
export IATP_AGENT_ID=my-agent
export IATP_TRUST_LEVEL=trusted
export IATP_REVERSIBILITY=full
export IATP_RETENTION=ephemeral
./iatp-sidecar
```

### Docker

```bash
cd sidecar/go
docker build -t iatp-sidecar:latest .
docker run -p 8001:8001 \
  -e IATP_AGENT_URL=http://host.docker.internal:8000 \
  -e IATP_AGENT_ID=my-agent \
  -e IATP_TRUST_LEVEL=trusted \
  iatp-sidecar:latest
```

## Understanding Trust Levels

### Verified Partner (Score: 10)
- Official partners with SLAs
- Full reversibility
- Ephemeral data retention
- **Example**: Stripe, AWS

```python
trust_level=TrustLevel.VERIFIED_PARTNER
```

### Trusted (Score: 7)
- Known reliable agents
- Partial reversibility
- Temporary retention
- **Example**: Internal company agents

```python
trust_level=TrustLevel.TRUSTED
```

### Standard (Score: 5)
- Default for unknown agents
- May have reversibility
- Varies on retention
- **Example**: Third-party APIs

```python
trust_level=TrustLevel.STANDARD
```

### Unknown (Score: 2)
- New or unverified agents
- Limited guarantees
- **Example**: Beta services

```python
trust_level=TrustLevel.UNKNOWN
```

### Untrusted (Score: 0)
- Known risky agents
- No reversibility
- Permanent storage
- **Example**: Honeypots, test agents

```python
trust_level=TrustLevel.UNTRUSTED
```

## What IATP Does Automatically

### ✅ Security Checks
- Detects credit cards (with Luhn validation)
- Detects SSNs
- Blocks sensitive data to untrusted agents

### ✅ Trust Scoring
- Calculates 0-10 trust score
- Factors in reversibility, retention, etc.
- Warns on low trust operations

### ✅ Policy Enforcement
- **Score >= 7**: Allow immediately
- **Score 3-6**: Warn (user can override)
- **Score < 3**: Warn (user can override)
- **Credit card + permanent storage**: Block (403)
- **SSN + non-ephemeral storage**: Block (403)

### ✅ Audit Logging
- Every request gets unique trace ID
- Full request/response logging (scrubbed)
- Retrieve logs: `GET /trace/{trace_id}`

## Advanced: User Overrides

For risky (but not blocked) operations, users can override:

```bash
# First attempt - get warning
curl -X POST http://localhost:8001/proxy \
  -H 'Content-Type: application/json' \
  -d '{"task": "risky_operation"}'

# Response: 449 Retry With
# {"warning": "Low trust score...", "requires_override": true}

# Second attempt - with override
curl -X POST http://localhost:8001/proxy \
  -H 'Content-Type: application/json' \
  -H 'X-User-Override: true' \
  -d '{"task": "risky_operation"}'

# Response: 200 OK (marked as quarantined)
```

## Docker Compose (Multi-Agent Setup)

`docker-compose.yml`:
```yaml
version: '3.8'

services:
  my-agent:
    build: .
    ports:
      - "8000:8000"
  
  iatp-sidecar:
    image: iatp-sidecar:latest
    ports:
      - "8001:8001"
    environment:
      - IATP_AGENT_URL=http://my-agent:8000
      - IATP_AGENT_ID=my-agent
      - IATP_TRUST_LEVEL=trusted
      - IATP_REVERSIBILITY=full
      - IATP_RETENTION=ephemeral
    depends_on:
      - my-agent
```

Run:
```bash
docker-compose up
```

## Testing with the Honeypot

Test your sidecar configuration with the built-in honeypot:

```bash
# Start honeypot agent (untrusted)
python examples/untrusted_agent.py

# Start sidecar for honeypot
python examples/run_untrusted_sidecar.py

# Try sending sensitive data (will be BLOCKED)
curl -X POST http://localhost:8001/proxy \
  -H 'Content-Type: application/json' \
  -d '{"payment":"4532-0151-1283-0366"}'
```

## Running the Cascading Hallucination Experiment

See the research in action:

```bash
cd experiments/cascading_hallucination
python run_experiment.py
```

This demonstrates IATP preventing cascading failures (100% success rate).

## Next Steps

1. **Read the README**: Comprehensive overview in [README.md](README.md)
2. **Explore Examples**: Working demos in `/examples`
3. **Run Experiment**: Research setup in `/experiments`
4. **Deploy with Docker**: One-line deploy with `docker-compose up`
5. **Read the Blog**: Launch post in [BLOG.md](BLOG.md)
6. **Contribute**: See [RFC_SUBMISSION.md](RFC_SUBMISSION.md) for standardization efforts

## Getting Help

- **GitHub Issues**: https://github.com/imran-siddique/inter-agent-trust-protocol/issues
- **Documentation**: See repository README and `/spec` directory
- **Examples**: `/examples` directory has working code

## Common Issues

### "Module not found" errors
```bash
pip install -e .
```

### Agent can't reach sidecar
Make sure both are running and ports are correct:
- Agent: `http://localhost:8000`
- Sidecar: `http://localhost:8001`

### Docker connection issues
Use `host.docker.internal` instead of `localhost` when connecting from container to host:
```bash
-e IATP_AGENT_URL=http://host.docker.internal:8000
```

## Summary

IATP is "Envoy for Agents" - infrastructure that makes agent-to-agent collaboration safe, auditable, and reversible. With just a few lines of code, you get:

- ✅ Automatic security validation
- ✅ Trust-based policy enforcement
- ✅ Complete audit trails
- ✅ User override capabilities
- ✅ Production-ready Go sidecar

**Welcome to the Agent Mesh. Welcome to IATP.**
