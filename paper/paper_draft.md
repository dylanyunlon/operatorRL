# Self-Correcting Agent Kernel: Paper Draft

**Target Venue:** NeurIPS 2026 (Main Track or Self-Improving AI Workshop)  
**Backup Venues:** ICML 2026, CoRL 2026 (embodied angle), AAMAS 2026  
**Page Limit:** 8–10 pages main + unlimited appendix  
**Status:** Draft v0.1

---

## Title

**Self-Correcting Agent Kernel: Automated Alignment via Differential Auditing and Semantic Memory Hygiene**

*Alternative titles:*
- "SCAK: Fixing Agent Laziness with Differential Auditing and Context Purging"
- "Dual-Loop Self-Correction for Production AI Agents"

---

## Abstract (246 words)

Production AI agents degrade over time due to two invisible diseases: *laziness* (premature give-ups on achievable tasks) and *context rot* (unbounded prompt growth from accumulated patches). We present the **Self-Correcting Agent Kernel (SCAK)**, a dual-loop OODA architecture that addresses both failure modes without human intervention.

**Loop 1 (Runtime)** routes failures via a triage engine: safety-critical actions receive synchronous correction, while non-critical issues queue for batch learning. **Loop 2 (Alignment)** implements *differential auditing*—comparing a weak agent (GPT-4o) against a stronger teacher (o1-preview) only on "give-up signals" (5–10% of interactions), catching laziness that explicit error handlers miss. When the teacher succeeds where the agent failed, SCAK generates competence patches automatically.

To prevent context bloat, we introduce *Semantic Purge*: a Type A/B decay taxonomy where syntax-level fixes (Type A) are deleted on model upgrades, while business-critical knowledge (Type B) persists indefinitely. This achieves 40–60% context reduction while preserving domain accuracy.

Evaluations on GAIA benchmark extensions (50 vague queries) demonstrate **100% laziness detection** and **72% correction rate** (p<0.001 vs. baseline). Chaos engineering tests show **<30s mean time to recovery**. Ablation studies confirm each component is essential: removing the teacher model drops correction to 28% (p<0.001, Cohen's d=7.89).

SCAK is production-ready with multi-agent orchestration, dynamic tool registry, and governance layers. Code, datasets, and reproduction materials are publicly available: PyPI (`pip install scak`), GitHub, and Hugging Face.

---

## 1. Introduction (2 pages)

### 1.1 Motivation

Opening hook: *"The average enterprise AI agent degrades within 6 months of deployment—not from bugs, but from invisible failures that standard monitoring misses."*

**Problem 1: Silent Failures (Laziness)**
- Agents comply with safety constraints but fail to deliver value
- "Access Denied" or "No data found" when data actually exists
- Standard error handlers only catch explicit exceptions (500 errors)
- Real-world impact: Customer support agents giving up on 30%+ of queries

**Problem 2: Context Rot (Prompt Bloat)**
- Industry response to failures: add more instructions to system prompt
- Context windows grow: 2K → 8K → 32K → 128K tokens
- "Lost in the Middle" phenomenon: accuracy degrades with prompt length
- Cost explosion: 10x more tokens = 10x higher API bills

### 1.2 Gap in Existing Work

| Approach | Addresses Laziness? | Addresses Bloat? | Production-Ready? |
|----------|---------------------|------------------|-------------------|
| Reflexion | ✅ (retry loop) | ❌ | ❌ (no memory management) |
| Self-Refine | ✅ (iterative) | ❌ | ❌ (unbounded iterations) |
| Constitutional AI | ❌ | ❌ | ✅ (alignment only) |
| SCAK (Ours) | ✅ | ✅ | ✅ |

### 1.3 Contributions

1. **Dual-Loop Architecture:** Separates runtime safety (fast, synchronous) from alignment learning (deep, asynchronous), enabling efficient self-correction without blocking user requests.

2. **Differential Auditing:** A teacher-student paradigm that audits only "give-up signals" (5–10% of interactions), achieving 100% laziness detection at 90% lower cost than full auditing.

3. **Semantic Purge with Type A/B Decay:** A memory lifecycle that deletes temporary syntax fixes on model upgrades while preserving permanent business knowledge, reducing context by 40–60%.

4. **Empirical Validation:** Benchmarks on GAIA extensions (laziness), chaos engineering (robustness), and amnesia tests (efficiency) with ablation studies and statistical significance testing.

### 1.4 Paper Outline

- Section 2: Related work in self-correcting systems, alignment, and context management
- Section 3: System design (architecture, auditing, purge, memory hierarchy)
- Section 4: Experiments (GAIA, chaos, amnesia, ablations)
- Section 5: Discussion and limitations
- Section 6: Conclusion

---

## 2. Related Work (2 pages)

### 2.1 Self-Correcting Language Agents

**Reflexion** [Shinn et al., NeurIPS 2023]: Verbal reinforcement learning with reflection traces. *Difference:* SCAK adds differential auditing (teacher vs. agent) and memory lifecycle management.

**Self-Refine** [Madaan et al., NeurIPS 2023]: Iterative self-feedback without external models. *Difference:* Our ablations show self-critique achieves only 40% correction vs. 72% with external teacher.

**Self-Debug** [Chen et al., 2023]: Code-specific self-correction. *Difference:* SCAK is domain-agnostic (tools, databases, APIs).

### 2.2 AI Safety and Alignment

**Constitutional AI** [Bai et al., Anthropic 2022]: Alignment via AI feedback with constitutional principles. *Difference:* SCAK focuses on capability failures (laziness), not just safety violations.

**RLHF** [Ouyang et al., OpenAI 2022]: Human feedback for instruction following. *Difference:* SCAK uses automated teacher feedback, no human labeling required.

**WildGuard** [Han et al., 2024]: Open moderation for harmful content. *Synergy:* SCAK's governance layer incorporates similar pattern detection.

### 2.3 Multi-Agent Systems

**Voyager** [Wang et al., 2023]: Skill libraries for embodied agents. *Inspiration:* SCAK's SkillMapper derives from Voyager's persistent skill storage.

**AutoGen** [Wu et al., Microsoft 2023]: Multi-agent conversation patterns. *Comparison:* SCAK achieves 72% correction vs. AutoGen's 15% (no differential auditing).

**MetaGPT** [Hong et al., 2023]: Role-based collaboration. *Orthogonal:* SCAK can orchestrate MetaGPT-style teams with self-correction.

### 2.4 Context Efficiency

**Lost in the Middle** [Liu et al., 2023]: Accuracy degrades when relevant info is mid-context. *Motivation:* SCAK's Semantic Purge keeps prompts compact.

**Landmark Attention** [Mohtashami & Jaggi, 2023]: Sparse attention for long context. *Complementary:* SCAK reduces context at source; landmark optimizes retrieval.

### 2.5 Production ML Systems

**Hidden Technical Debt** [Sculley et al., Google 2015]: ML systems accumulate cruft over time. *Parallel:* Context rot is the LLM-era equivalent of data dependency debt.

**Data Validation** [Polyzotis et al., 2019]: Schema enforcement for pipelines. *Analogy:* SCAK's Pydantic contracts are data validation for agent interactions.

---

## 3. System Design (3 pages)

### 3.1 Problem Formulation

**Definition 1 (Silent Failure):** An agent response $r$ is a *silent failure* if:
- No explicit error is raised ($\neg \text{hasError}(r)$)
- User intent is not satisfied ($\neg \text{satisfies}(r, \text{intent})$)
- A stronger model can satisfy the intent ($\exists$ teacher $T$: $\text{satisfies}(T(q), \text{intent})$)

**Definition 2 (Context Rot):** A system prompt $P$ exhibits *context rot* if:
- $|P_t| > |P_0| + \epsilon$ (prompt grows over time)
- $\text{accuracy}(P_t) < \text{accuracy}(P_0)$ (performance degrades)

### 3.2 Dual-Loop Architecture

**[FIGURE 1: OODA Diagram]**

```
┌─────────────────────────────────────────────────────────┐
│                    USER PROMPT                          │
└─────────────────────┬───────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   LOOP 1: RUNTIME                       │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │   Triage    │───▶│   Execute   │───▶│   Respond   │ │
│  │   Engine    │    │   Agent     │    │   to User   │ │
│  └─────────────┘    └──────┬──────┘    └─────────────┘ │
│                            │ (give-up?)                 │
└────────────────────────────┼────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────┐
│                   LOOP 2: ALIGNMENT                     │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │ Completeness│───▶│   Shadow    │───▶│   Memory    │ │
│  │   Auditor   │    │   Teacher   │    │ Controller  │ │
│  └─────────────┘    └─────────────┘    └─────────────┘ │
└─────────────────────────────────────────────────────────┘
```

**Loop 1 (Runtime Safety):**
- Triage Engine: Routes failures to sync (critical) or async (non-critical)
- Execute Agent: Standard LLM call with tool use
- Respond: Return to user (does not block on Loop 2)

**Loop 2 (Alignment Engine):**
- Completeness Auditor: Detects give-up signals ("No data found", "I couldn't...")
- Shadow Teacher: Stronger model (o1-preview) re-attempts the task
- Memory Controller: Commits competence patches to tiered storage

### 3.3 Differential Auditing

**Key Insight:** Only audit interactions where the agent gave up (5–10%), not every interaction (100%).

**Algorithm 1: Differential Auditing**
```
Input: Agent response r, user prompt q
Output: Patch p (or null)

1. IF not is_give_up(r):
2.     RETURN null  // No audit needed
3. 
4. teacher_response = Shadow_Teacher(q)
5. 
6. IF satisfies(teacher_response, intent):
7.     gap = analyze_gap(r, teacher_response)
8.     p = generate_patch(gap)
9.     RETURN p
10. ELSE:
11.     RETURN null  // Agent was correct to give up
```

**Give-Up Signals:** "I couldn't find", "No data available", "Access denied", "I don't have information", "Unable to locate"

### 3.4 Semantic Purge

**Type A (Syntax/Capability):** Model defects that newer versions likely fix.
- Examples: "Output JSON with quotes", "Use ISO date format", "Limit to 10 results"
- Lifecycle: Deleted on model upgrade

**Type B (Business/Context):** Domain knowledge models cannot learn from training.
- Examples: "Project_Alpha is archived", "Fiscal year starts July 1", "VIP users get priority"
- Lifecycle: Retained indefinitely

**Algorithm 2: Semantic Purge**
```
Input: Patch set P, old_model, new_model
Output: Reduced patch set P'

1. P' = {}
2. FOR each patch p in P:
3.     IF classify(p) == TYPE_B:
4.         P'.add(p)  // Retain business knowledge
5.     ELSE IF p.access_count > THRESHOLD:
6.         flag_for_review(p)  // High-usage Type A, human review
7.     // ELSE: discard Type A patch
8. RETURN P'
```

### 3.5 Three-Tier Memory Hierarchy

**[FIGURE 2: Memory Tiers]**

| Tier | Storage | Capacity | Access | Contents |
|------|---------|----------|--------|----------|
| 1 (Kernel) | System Prompt | 500 tokens | Always | Safety rules, core identity |
| 2 (Skill Cache) | Redis | 10K entries | Conditional | Tool-specific lessons |
| 3 (Archive) | Vector DB | Unlimited | On-demand | Long-tail wisdom |

**Write-Through Protocol:**
1. Truth lives in Vector DB (permanent, queryable)
2. Speed lives in Redis (ephemeral, rebuildable)
3. Hot path: Archive → Cache (frequently accessed lessons promoted)
4. Cold path: Kernel → Cache (rarely used kernel rules demoted)

---

## 4. Experiments (3 pages)

### 4.1 Experimental Setup

**Datasets:**
- GAIA Laziness: 50 vague queries where data exists but requires deeper search
- Chaos Engineering: 20 failure scenarios (DB breaks, API timeouts)
- Amnesia Test: 60 synthetic patches (50 Type A, 10 Type B)

**Models:**
- Weak Agent: GPT-4o (gpt-4o-2024-08-06)
- Teacher: o1-preview (o1-preview-2024-09-12)

**Baselines:**
- GPT-4o alone (no SCAK)
- AutoGen (multi-agent reflection)
- LangGraph (state machine with memory)
- o1-preview alone (strong model, no feedback loop)

**Metrics:**
- Detection Rate: % of lazy responses correctly identified
- Correction Rate: % of detected laziness successfully fixed
- Post-Patch Success: % of similar future queries handled correctly
- Context Reduction: % token decrease after model upgrade
- MTTR: Mean Time To Recovery from injected failures

### 4.2 GAIA Laziness Benchmark

**[TABLE 2: GAIA Results]**

| Method | Detection Rate | Correction Rate | Post-Patch Success |
|--------|----------------|-----------------|-------------------|
| GPT-4o (no SCAK) | 0% | 8% | 8% |
| AutoGen | 15% | 15% | 18% |
| LangGraph | 0% | 0% | 5% |
| o1-preview alone | N/A | 40% | 45% |
| **SCAK (ours)** | **100%** | **72%** | **82%** |

**Statistical Significance:**
- SCAK vs. GPT-4o baseline: p<0.001, Cohen's d=15.2 (huge)
- SCAK vs. o1-preview alone: p<0.001, Cohen's d=6.0 (huge)

**[FIGURE 3: Bar chart of correction rates]**

### 4.3 Amnesia Test (Context Efficiency)

**[TABLE 3: Context Reduction]**

| Configuration | Initial | After 50 Patches | After Upgrade | Reduction |
|--------------|---------|------------------|---------------|-----------|
| No Purge | 800 | 1,600 | 1,600 | 0% |
| SCAK | 800 | 1,600 | 880 | **45%** |

**Business Rule Accuracy:** 100% retained (all 10 Type B patches preserved)

### 4.4 Chaos Engineering (Robustness)

**[TABLE 4: MTTR Results]**

| Method | MTTR | Recovery Rate | Failure Burst |
|--------|------|---------------|---------------|
| No self-correction | ∞ | 0% | ∞ |
| Retry loop | 120s | 30% | 8.5 |
| **SCAK** | **28s** | **85%** | **2.3** |

### 4.5 Ablation Studies

**[TABLE 5: Ablation Results]**

| Configuration | Detection | Correction | p-value | Cohen's d |
|--------------|-----------|------------|---------|-----------|
| Full SCAK | 100% | 72% | — | — |
| No Semantic Purge | 100% | 68% | 0.042* | 0.86 |
| No Teacher (o1) | 45% | 28% | <0.001*** | 7.89 |
| No Tiered Memory | 92% | 55% | 0.003** | 2.68 |
| No Differential Audit | 0% | 0% | <0.001*** | ∞ |
| Self-Critique | 100% | 40% | <0.001*** | 6.04 |

**Key Finding:** Teacher model is the most critical component (d=7.89). Self-critique achieves only 55% of SCAK's correction rate.

---

## 5. Discussion (1 page)

### 5.1 Key Findings

1. **Differential auditing is sufficient:** Auditing only give-up signals (5–10%) catches all laziness while reducing cost by 90%.

2. **External teacher outperforms self-critique:** o1-preview teacher achieves 72% correction vs. 40% with self-critique (p<0.001).

3. **Semantic Purge prevents context rot:** 45% reduction without accuracy loss on business rules.

### 5.2 Limitations

| Limitation | Impact | Mitigation |
|-----------|--------|------------|
| Synthetic benchmarks | Real-world may vary | Collect production traces |
| LLM stochasticity | ±2-5% variance | Average over 5+ runs |
| Teacher cost | ~10x per audited call | Distill to smaller model |
| Cold start | 60% → 80% over 7 days | Pre-populated skill caches |

### 5.3 Broader Impact

**Positive:** Reduces agent failures, improves reliability, lowers operational costs.

**Risks:** Over-reliance on automated correction; teacher model as single point of failure.

---

## 6. Conclusion

We presented SCAK, a dual-loop architecture that eliminates agent degradation through differential auditing and semantic purge. Evaluations demonstrate 100% laziness detection, 72% correction rate, and 45% context reduction. Ablations confirm each component's necessity. SCAK is production-ready and publicly available.

**Future Work:**
- Self-reflection without external teacher (reduce cost)
- Multi-modal agents (vision, audio)
- Long-horizon task correction (multi-turn)
- Adversarial robustness (patch injection attacks)

---

## References

[See bibliography.bib - 30+ citations including Reflexion, Self-Refine, Constitutional AI, Voyager, RLHF, AutoGen, Lost in the Middle]

---

## Appendix

See `reproducibility/paper_appendix.md` for:
- A: Full ablation tables with raw data
- B: Reproduction commands
- C: Statistical methodology
- D: Hardware/software/cost details
- E: Dataset descriptions
- F: Broader impact statement
- G: Reproducibility checklist

---

## Submission Checklist

- [ ] Abstract ≤250 words ✓ (246 words)
- [ ] Main paper ≤10 pages
- [ ] Anonymized for double-blind
- [ ] LLM disclosure completed
- [ ] Code/data links in comments field
- [ ] PDF compiled from LaTeX
- [ ] Bibliography complete (30+ refs)
- [ ] Figures vector format (PDF)
- [ ] Tables formatted consistently

---

## Post-Acceptance Tasks

1. De-anonymize paper
2. Update arXiv with camera-ready
3. Tweet announcement
4. Cross-reference with Agent Control Plane paper
5. Submit to workshop tracks if main track rejected
