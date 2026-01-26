# Product Requirements Document: Dependency Gap Analysis

**Document Version:** 1.0  
**Date:** January 25, 2026  
**Author:** Carbon Auditor Swarm Team  
**Status:** Draft  

---

## Executive Summary

During the implementation of the Carbon Auditor Swarm for Voluntary Carbon Market (VCM) verification, we identified critical feature gaps in our four core PyPI dependencies. This PRD documents these gaps and proposes enhancements that would significantly improve production readiness.

---

## 1. cmvk (Cross-Model Verification Kernel) v0.1.0

### Current State
The `cmvk` package provides `verify_embeddings()` using **cosine similarity** for drift detection.

### Critical Gap: Distance Metric Limitations

| ID | Feature | Priority | Justification |
|----|---------|----------|---------------|
| CMVK-001 | **Euclidean Distance Support** | P0 | Cosine similarity normalizes vectors, losing magnitude information. We had to implement our own `calculate_euclidean_drift()` because cosine returned 0.08 drift for a 61% NDVI discrepancy. |
| CMVK-002 | **Configurable Distance Metrics** | P0 | Different use cases need different metrics (Manhattan, Chebyshev, Mahalanobis) |
| CMVK-003 | **Metric Selection API** | P1 | `verify_embeddings(metric="euclidean")` parameter |

**Impact:** Without Euclidean distance, the kernel **failed to detect obvious fraud** (claimed NDVI=0.82 vs observed=0.316).

### Additional Missing Features

| ID | Feature | Priority | Justification |
|----|---------|----------|---------------|
| CMVK-004 | **Batch Verification** | P1 | Verify multiple claim/observation pairs in single call for efficiency |
| CMVK-005 | **Threshold Profiles** | P1 | Pre-configured thresholds for different domains (carbon, financial, medical) |
| CMVK-006 | **Verification Audit Trail** | P1 | Immutable log of all verifications with timestamps and inputs |
| CMVK-007 | **Confidence Calibration** | P2 | Confidence scores should be calibrated against ground truth |
| CMVK-008 | **Dimensional Weighting** | P2 | Weight certain dimensions higher (e.g., NDVI more important than carbon estimate) |
| CMVK-009 | **Anomaly Detection Mode** | P2 | Flag outliers even within threshold using statistical methods |
| CMVK-010 | **Explainable Drift** | P2 | Which dimensions contributed most to drift score |

### Proposed API Enhancement

```python
# Current (insufficient)
result = cmvk.verify_embeddings(claim_vector, observation_vector)

# Proposed
result = cmvk.verify_embeddings(
    claim_vector,
    observation_vector,
    metric="euclidean",           # NEW: distance metric selection
    weights=[0.6, 0.4],           # NEW: dimensional weighting
    threshold_profile="carbon",    # NEW: domain-specific thresholds
    explain=True                   # NEW: explainability
)

# result.explanation = {
#     "primary_drift_dimension": "carbon_stock",
#     "dimension_contributions": {"ndvi": 0.35, "carbon": 0.65}
# }
```

---

## 2. amb-core (Agent Message Bus) v0.1.0

### Current State
Provides async pub/sub messaging with InMemory, Redis, RabbitMQ, Kafka adapters.

### Critical Gaps

| ID | Feature | Priority | Justification |
|----|---------|----------|---------------|
| AMB-001 | **Message Persistence** | P0 | Messages lost on crash; need replay capability for audit trails |
| AMB-002 | **Dead Letter Queue (DLQ)** | P0 | Failed messages silently dropped; need DLQ for investigation |
| AMB-003 | **Message Schema Validation** | P0 | No built-in validation; malformed messages cause runtime errors |
| AMB-004 | **Distributed Tracing** | P1 | No correlation IDs; impossible to trace message flow across agents |
| AMB-005 | **Message Prioritization** | P1 | All messages treated equally; fraud alerts should be high priority |
| AMB-006 | **Exactly-Once Delivery** | P1 | At-least-once causes duplicate processing |
| AMB-007 | **Message TTL** | P2 | Stale messages processed; need expiration |
| AMB-008 | **Backpressure Handling** | P2 | Slow consumers overwhelmed; need flow control |
| AMB-009 | **Message Compression** | P3 | Large payloads (satellite data) inefficient |
| AMB-010 | **Encryption at Rest** | P3 | Sensitive audit data exposed in queues |

### Proposed API Enhancement

```python
# Current
bus = MessageBus(adapter=InMemoryAdapter())
await bus.publish("topic", Message(payload=data))

# Proposed
bus = MessageBus(
    adapter=InMemoryAdapter(),
    persistence=True,              # NEW: durable messages
    schema_registry=schemas,       # NEW: validation
    dlq_enabled=True               # NEW: dead letter queue
)

await bus.publish(
    "topic",
    Message(
        payload=data,
        priority=Priority.HIGH,    # NEW: prioritization
        ttl_seconds=300,           # NEW: expiration
        trace_id=uuid4()           # NEW: distributed tracing
    )
)
```

---

## 3. agent-tool-registry (atr) v0.1.0

### Current State
Decorator-based tool registration with `@atr.register()` and discovery via `atr._global_registry`.

### Critical Gaps

| ID | Feature | Priority | Justification |
|----|---------|----------|---------------|
| ATR-001 | **Public Registry API** | P0 | Using `atr._global_registry` (private API) is fragile |
| ATR-002 | **Tool Versioning** | P0 | No version control; breaking changes affect all agents |
| ATR-003 | **Async Tool Support** | P1 | All tools synchronous; blocks event loop |
| ATR-004 | **Tool Dependency Injection** | P1 | No way to inject config/credentials into tools |
| ATR-005 | **Tool Access Control** | P1 | Any agent can call any tool; need permissions |
| ATR-006 | **Tool Rate Limiting** | P1 | Sentinel API calls unlimited; risk of rate limiting |
| ATR-007 | **Tool Composition** | P2 | Can't chain tools declaratively |
| ATR-008 | **Tool Health Checks** | P2 | No way to verify external tools (APIs) are available |
| ATR-009 | **Tool Metrics** | P2 | No latency/error rate tracking |
| ATR-010 | **Tool Retry Policies** | P2 | No built-in retry with backoff |

### Proposed API Enhancement

```python
# Current (using private API)
tool = atr._global_registry.get_callable("pdf_parser")

# Proposed
@atr.register(
    name="pdf_parser",
    version="1.0.0",               # NEW: versioning
    async_=True,                   # NEW: async support
    rate_limit="10/minute",        # NEW: rate limiting
    permissions=["claims-agent"],  # NEW: access control
    retry_policy=RetryPolicy(      # NEW: retry
        max_attempts=3,
        backoff="exponential"
    )
)
async def pdf_parser(file_path: str, config: Config = inject()) -> dict:
    ...

# Public API for discovery
tool = atr.get_tool("pdf_parser", version=">=1.0.0")
await tool.call_async(file_path="doc.pdf")
```

---

## 4. agent-control-plane v0.2.0

### Current State
Agent runtime for lifecycle management (referenced but minimally integrated).

### Critical Gaps

| ID | Feature | Priority | Justification |
|----|---------|----------|---------------|
| ACP-001 | **Agent Health Checks** | P0 | No liveness/readiness probes; dead agents undetected |
| ACP-002 | **Agent Auto-Recovery** | P0 | Crashed agents not restarted automatically |
| ACP-003 | **Circuit Breaker** | P1 | Cascading failures when one agent fails |
| ACP-004 | **Agent Scaling** | P1 | No horizontal scaling for high-throughput verification |
| ACP-005 | **Distributed Coordination** | P1 | No leader election or consensus for stateful operations |
| ACP-006 | **Agent Dependency Graph** | P1 | Start order not enforced; race conditions |
| ACP-007 | **Graceful Shutdown** | P2 | In-flight verifications lost on shutdown |
| ACP-008 | **Resource Quotas** | P2 | No memory/CPU limits per agent |
| ACP-009 | **Agent Observability** | P2 | No built-in metrics/logging integration |
| ACP-010 | **Hot Reload** | P3 | Agent code changes require full restart |

### Proposed API Enhancement

```python
# Current (manual lifecycle)
agent = ClaimsAgent()
await agent.start()
# ... hope it doesn't crash ...
await agent.stop()

# Proposed
control_plane = AgentControlPlane(
    health_check_interval=30,      # NEW: health monitoring
    auto_recovery=True,            # NEW: auto-restart
    circuit_breaker=CircuitBreaker(# NEW: fault tolerance
        failure_threshold=5,
        recovery_timeout=60
    )
)

control_plane.register(
    ClaimsAgent,
    replicas=3,                    # NEW: scaling
    dependencies=["message-bus"],  # NEW: startup order
    resources=ResourceQuota(       # NEW: limits
        memory_mb=512,
        cpu_percent=25
    )
)

await control_plane.start_all()   # Manages entire swarm lifecycle
```

---

## 5. Cross-Cutting Concerns (All Packages)

### Missing Integrations

| ID | Feature | Packages Affected | Priority |
|----|---------|-------------------|----------|
| XC-001 | **OpenTelemetry Integration** | All | P1 |
| XC-002 | **Structured Logging (JSON)** | All | P1 |
| XC-003 | **Type Stub Files (.pyi)** | cmvk, atr | P2 |
| XC-004 | **Pydantic v2 Models** | All | P2 |
| XC-005 | **Async-First Design** | cmvk, atr | P2 |

---

## 6. Impact Assessment

### Workarounds Implemented

| Gap | Workaround | Technical Debt |
|-----|------------|----------------|
| CMVK-001 (Euclidean) | Custom `calculate_euclidean_drift()` in auditor_agent.py | Duplicated logic; diverges from cmvk updates |
| ATR-001 (Private API) | Using `atr._global_registry` directly | May break on minor version updates |
| AMB-004 (Tracing) | Manual logging with timestamps | No correlation across agent boundaries |

### Production Risk Matrix

| Dependency | Production Readiness | Blockers |
|------------|---------------------|----------|
| cmvk | ⚠️ Medium | Distance metrics, audit trail |
| amb-core | ⚠️ Medium | Persistence, DLQ |
| agent-tool-registry | ⛔ Low | Private API, no versioning |
| agent-control-plane | ⛔ Low | No health checks, no recovery |

---

## 7. Recommendations

### Immediate (Before Production)

1. **Fork cmvk** and add Euclidean distance support, or contribute upstream PR
2. **Abstract atr access** behind internal facade to isolate from private API
3. **Add message persistence** layer on top of amb-core for audit compliance
4. **Implement custom health check** loop for agent monitoring

### Medium-Term (Q2 2026)

1. Contribute PRs to all four packages for P0/P1 features
2. Evaluate alternative packages if upstream unresponsive
3. Build internal "audit-compliance" wrapper package

### Long-Term (Q3-Q4 2026)

1. Consider building unified "carbon-audit-sdk" that bundles dependencies
2. Work with maintainers on roadmap alignment

---

## 8. Appendix: Feature Request Templates

### cmvk - Euclidean Distance Support

**Title:** Add configurable distance metrics (Euclidean, Manhattan, etc.)

**Problem:** Cosine similarity normalizes vectors, making it unsuitable for detecting magnitude-based fraud. In carbon auditing, a project claiming NDVI=0.82 when satellite shows NDVI=0.32 should trigger high drift, but cosine similarity returns only 0.08 because the vectors point in similar directions.

**Proposed Solution:**
```python
class DistanceMetric(Enum):
    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    MANHATTAN = "manhattan"

def verify_embeddings(
    embedding_a: List[float],
    embedding_b: List[float],
    metric: DistanceMetric = DistanceMetric.COSINE
) -> VerificationScore:
    ...
```

**Acceptance Criteria:**
- [ ] Euclidean distance available as metric option
- [ ] Drift score properly scales with magnitude differences
- [ ] Backward compatible (cosine remains default)

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-25 | Carbon Auditor Team | Initial draft |
