# Production Deployment Checklist

Use this checklist when deploying IATP to production.

## Pre-Deployment

### [ ] Define Capability Manifest

Create an accurate capability manifest for your agent:

```python
manifest = CapabilityManifest(
    agent_id="production-agent-v1",
    trust_level=TrustLevel.TRUSTED,  # Choose appropriate level
    capabilities=AgentCapabilities(
        reversibility=ReversibilityLevel.FULL,  # Can you undo?
        idempotency=True,  # Can handle duplicates?
        concurrency_limit=100,  # Max concurrent requests
        sla_latency_ms=2000  # P95 latency target
    ),
    privacy_contract=PrivacyContract(
        retention=RetentionPolicy.EPHEMERAL,  # How long do you keep data?
        human_in_loop=False,  # Do humans review data?
        training_consent=False  # Use data for ML training?
    )
)
```

### [ ] Security Review

- [ ] Review sensitive data patterns (credit cards, SSNs)
- [ ] Verify Luhn validation is working
- [ ] Test privacy policy enforcement
- [ ] Ensure sensitive data scrubbing in logs

### [ ] Performance Testing

Python Sidecar:
- [ ] Test with 100 concurrent requests
- [ ] Measure latency overhead (<10ms expected)
- [ ] Check memory usage (~50MB expected)

Go Sidecar (Recommended for Production):
- [ ] Test with 10,000 concurrent requests
- [ ] Measure latency overhead (<1ms expected)
- [ ] Check memory usage (~10MB expected)

### [ ] Integration Testing

- [ ] Test health check endpoint: `GET /health`
- [ ] Test capability manifest: `GET /capabilities`
- [ ] Test proxy endpoint: `POST /proxy`
- [ ] Test trace retrieval: `GET /trace/{trace_id}`
- [ ] Test with actual agent workload

## Deployment

### [ ] Choose Deployment Method

**Option 1: Docker Compose (Simplest)**
- [ ] Build Docker images
- [ ] Configure environment variables
- [ ] Test locally with `docker-compose up`
- [ ] Deploy to production

**Option 2: Kubernetes (Scalable)**
- [ ] Create sidecar container in pod
- [ ] Configure resource limits
- [ ] Set up service mesh integration (optional)
- [ ] Deploy and verify

**Option 3: Standalone Binary (Go Sidecar)**
- [ ] Build Go binary: `go build -o iatp-sidecar`
- [ ] Configure systemd service
- [ ] Set environment variables
- [ ] Start service and verify

### [ ] Environment Configuration

Required environment variables:

```bash
# Agent connection
IATP_AGENT_URL=http://localhost:8000
IATP_PORT=8001

# Agent identity
IATP_AGENT_ID=production-agent-v1

# Trust configuration
IATP_TRUST_LEVEL=trusted
IATP_REVERSIBILITY=full
IATP_RETENTION=ephemeral

# Privacy settings (optional)
IATP_HUMAN_IN_LOOP=false
IATP_TRAINING_CONSENT=false
```

### [ ] Monitoring Setup

- [ ] Set up health check monitoring
- [ ] Configure alerting for sidecar downtime
- [ ] Monitor request rate and latency
- [ ] Track trust score distribution
- [ ] Monitor blocked/warned request rate

### [ ] Logging Configuration

- [ ] Configure log aggregation (e.g., ELK, Splunk)
- [ ] Set up log retention policy
- [ ] Verify sensitive data scrubbing
- [ ] Enable trace ID correlation

## Post-Deployment

### [ ] Verification

- [ ] Health check returns 200 OK
- [ ] Capability manifest accessible
- [ ] Requests flow through sidecar correctly
- [ ] Trust scoring works as expected
- [ ] Sensitive data detection working
- [ ] Audit logs being written

### [ ] Load Testing

- [ ] Run production-level load test
- [ ] Verify latency under load
- [ ] Check memory/CPU usage
- [ ] Verify no dropped requests
- [ ] Test failover scenarios

### [ ] Security Audit

- [ ] Review blocked request logs
- [ ] Verify warning mechanisms
- [ ] Test user override flow
- [ ] Check quarantine decisions
- [ ] Audit trace logs for completeness

## Scaling Considerations

### Horizontal Scaling

Each agent instance needs its own sidecar:

```yaml
# Kubernetes example
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agent-deployment
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: agent
        image: my-agent:latest
      - name: iatp-sidecar
        image: iatp-sidecar:latest
        env:
        - name: IATP_AGENT_URL
          value: "http://localhost:8000"
```

### Resource Limits

Python Sidecar:
```yaml
resources:
  limits:
    memory: "128Mi"
    cpu: "200m"
  requests:
    memory: "64Mi"
    cpu: "100m"
```

Go Sidecar:
```yaml
resources:
  limits:
    memory: "32Mi"
    cpu: "100m"
  requests:
    memory: "16Mi"
    cpu: "50m"
```

## Troubleshooting

### Sidecar Won't Start

- [ ] Check agent URL is accessible
- [ ] Verify port not already in use
- [ ] Check environment variables
- [ ] Review sidecar logs

### High Latency

- [ ] Switch to Go sidecar if using Python
- [ ] Check network latency to agent
- [ ] Verify agent performance
- [ ] Review trust score calculation overhead

### Requests Being Blocked

- [ ] Review manifest configuration
- [ ] Check trust score calculation
- [ ] Verify privacy policy settings
- [ ] Test with user override

### Missing Logs

- [ ] Verify flight recorder is enabled
- [ ] Check log destination
- [ ] Review scrubbing configuration
- [ ] Ensure trace IDs are present

## Performance Optimization

### Python Sidecar

- [ ] Use `uvicorn` with workers: `--workers 4`
- [ ] Enable HTTP/2 if supported
- [ ] Configure connection pooling
- [ ] Use async/await properly

### Go Sidecar

- [ ] Already optimized (use this for production)
- [ ] Adjust `concurrency_limit` if needed
- [ ] Enable connection reuse
- [ ] Monitor goroutine count

## Security Hardening

### Network Security

- [ ] Use HTTPS between sidecar and external callers
- [ ] Keep sidecar <-> agent on localhost
- [ ] Implement mTLS for inter-agent communication
- [ ] Use network policies (Kubernetes)

### Authentication

- [ ] Add API key validation
- [ ] Implement OAuth2/OIDC
- [ ] Use service account tokens (Kubernetes)
- [ ] Rate limit per client

### Data Protection

- [ ] Encrypt logs at rest
- [ ] Use secret management for sensitive config
- [ ] Rotate credentials regularly
- [ ] Audit access to trace data

## Maintenance

### Regular Tasks

- [ ] Review trust score distribution (weekly)
- [ ] Analyze blocked requests (weekly)
- [ ] Update capability manifest as agent evolves
- [ ] Review and update privacy policies
- [ ] Monitor sidecar version for updates

### Upgrades

- [ ] Test new version in staging
- [ ] Review CHANGELOG.md for breaking changes
- [ ] Rolling deployment (zero downtime)
- [ ] Verify manifest compatibility
- [ ] Monitor for issues post-upgrade

## Disaster Recovery

### Sidecar Failure

- [ ] Document failover procedure
- [ ] Test agent behavior without sidecar
- [ ] Configure health check retry logic
- [ ] Have rollback plan ready

### Data Loss

- [ ] Flight recorder logs backed up
- [ ] Trace IDs enable request replay
- [ ] Document data retention policy
- [ ] Test recovery procedures

## Compliance

### Data Privacy

- [ ] Document data retention policies
- [ ] GDPR compliance review (if applicable)
- [ ] CCPA compliance review (if applicable)
- [ ] Audit trail completeness

### Security

- [ ] Document trust score methodology
- [ ] Record policy enforcement decisions
- [ ] Maintain blocked request logs
- [ ] Regular security audits

## Success Metrics

Track these metrics to measure IATP effectiveness:

### Security Metrics
- Requests blocked per day
- Sensitive data detected per day
- Trust score distribution
- User override rate

### Performance Metrics
- P50/P95/P99 latency
- Request throughput
- Sidecar CPU/memory usage
- Error rate

### Business Metrics
- Agent reliability improvement
- Reduced security incidents
- Time to detect issues
- Cost per request

## Final Checklist

Before going live:

- [ ] All tests passing
- [ ] Security review complete
- [ ] Performance benchmarks met
- [ ] Monitoring configured
- [ ] Alerting set up
- [ ] Documentation updated
- [ ] Team trained on IATP
- [ ] Runbook documented
- [ ] Rollback plan ready
- [ ] Stakeholders notified

## Support

- **GitHub Issues**: https://github.com/imran-siddique/inter-agent-trust-protocol/issues
- **Documentation**: See repository README
- **Community**: See RFC_SUBMISSION.md for community channels

---

**Remember: IATP is infrastructure, not a silver bullet. It augments your security and governance, but doesn't replace careful agent design and testing.**
