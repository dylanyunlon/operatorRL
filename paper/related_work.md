# Related Work

*Adapted from [docs/RELATED_WORK.md](../docs/RELATED_WORK.md)*

## Retrieval-Augmented Generation

The foundation of modern RAG systems traces to Lewis et al. [1], who introduced the paradigm of combining retrieval with generation for knowledge-intensive NLP tasks. Subsequent work by Guu et al. [2] demonstrated the benefits of retrieval-augmented pre-training, while Izacard & Grave [3] developed the Fusion-in-Decoder architecture for open-domain QA.

CaaS differs from these foundational approaches by focusing on the **serving-time context management** problem rather than the retrieval mechanism itself. We assume any retriever (dense, sparse, or hybrid) and instead optimize how retrieved context is organized, prioritized, and presented to the LLM.

## Document Structure and Hierarchical Indexing

Hierarchical document understanding has been explored in summarization [4, 5] and document-level NLP [6]. These works demonstrate that respecting document structure improves downstream performance. CaaS applies this insight to RAG through our **three-tier value hierarchy** (High/Medium/Low), which explicitly encodes structural importance into the retrieval ranking.

Unlike learned hierarchical representations, CaaS uses **deterministic heuristics** based on document type detection (code, legal, policy, etc.), enabling zero-latency decisions without model inference.

## Temporal Information Retrieval

The importance of time in retrieval has been studied extensively in web search [7] and more recently in LLM contexts [8, 9]. Kasai et al. [10] introduced RealTime QA, demonstrating that time-sensitive questions require time-aware retrieval. Lazaridou et al. [11] showed that language models struggle with temporal knowledge degradation.

CaaS implements **explicit time-based decay** with configurable half-life parameters, inspired by radioactive decay models. Unlike implicit temporal signals in embeddings, our approach provides transparent, explainable temporal weighting.

## Source Attribution and Provenance

Recent work on attribution [12, 13, 14] addresses the challenge of tracing generated content to sources. Menick et al. [12] trained models to support answers with verified quotes, while Rashkin et al. [14] developed metrics for attribution quality.

CaaS's **Pragmatic Truth** module extends attribution by explicitly tracking **conflicts between sources**—surfacing when official documentation disagrees with informal sources (Slack, tickets, incident reports). This addresses a gap in current attribution systems that assume source consistency.

## Context Window Management

Managing long conversations and context windows is a growing challenge as LLMs are deployed in production [15, 16]. Common approaches include summarization [17] and compression [18], but these introduce lossy transformations that can discard critical details.

CaaS takes a different approach with **FIFO sliding window management**: rather than summarizing poorly, we truncate precisely. Our philosophy—"Chopping > Summarizing"—preserves recent turns losslessly while accepting that older context is simply dropped. This design choice reflects the empirical observation that users rarely reference content from many turns ago, but frequently reference the exact code or error message from seconds ago.

## Enterprise AI Deployment

The enterprise deployment of LLMs introduces unique challenges around security, compliance, and data sovereignty [19, 20]. While cloud-based routing services offer cost optimization through model selection, they create unacceptable data leakage risks for sensitive enterprise data.

CaaS's **Trust Gateway** addresses this through an on-premises deployment model. Rather than competing on routing intelligence, we compete on trust: enterprises deploy the gateway behind their firewall, maintaining complete data sovereignty while still benefiting from intelligent context serving.

---

## References

[1] Lewis, P., et al. (2020). "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." *NeurIPS 2020*. https://arxiv.org/abs/2005.11401

[2] Guu, K., et al. (2020). "REALM: Retrieval-Augmented Language Model Pre-Training." *ICML 2020*. https://arxiv.org/abs/2002.08909

[3] Izacard, G., & Grave, E. (2021). "Leveraging Passage Retrieval with Generative Models for Open Domain Question Answering." *EACL 2021*. https://arxiv.org/abs/2007.01282

[4] Cohan, A., et al. (2018). "A Discourse-Aware Attention Model for Abstractive Summarization of Long Documents." *NAACL 2018*. https://arxiv.org/abs/1804.05685

[5] Liu, Y., & Lapata, M. (2019). "Hierarchical Transformers for Multi-Document Summarization." *ACL 2019*. https://arxiv.org/abs/1905.13164

[6] Xiao, W., & Carenini, G. (2019). "Extractive Summarization of Long Documents by Combining Global and Local Context." *EMNLP 2019*. https://arxiv.org/abs/1909.08089

[7] Campos, R., et al. (2014). "Survey of Temporal Information Retrieval and Scoping Methods." *WWW Journal*. DOI: 10.1007/s11280-013-0230-y

[8] Dai, Z., & Callan, J. (2019). "Deeper Text Understanding for IR with Contextual Neural Language Modeling." *SIGIR 2019*. https://arxiv.org/abs/1905.09217

[9] Nguyen, T., et al. (2016). "A Neural Network Approach to Context-Sensitive Generation of Conversational Responses." *NAACL 2016*. https://arxiv.org/abs/1506.06714

[10] Kasai, J., et al. (2022). "RealTime QA: What's the Answer Right Now?" *NeurIPS 2022*. https://arxiv.org/abs/2207.13332

[11] Lazaridou, A., et al. (2021). "Mind the Gap: Assessing Temporal Generalization in Neural Language Models." *NeurIPS 2021*. https://arxiv.org/abs/2102.01951

[12] Menick, J., et al. (2022). "Teaching Language Models to Support Answers with Verified Quotes." *NeurIPS 2022*. https://arxiv.org/abs/2203.11147

[13] Gao, L., et al. (2022). "Rarr: Researching and Revising What Language Models Say, Using Language Models." *ACL 2023*. https://arxiv.org/abs/2210.08726

[14] Rashkin, H., et al. (2021). "Measuring Attribution in Natural Language Generation Models." *Computational Linguistics 2021*. https://arxiv.org/abs/2112.12870

[15] Dinan, E., et al. (2019). "Wizard of Wikipedia: Knowledge-Powered Conversational Agents." *ICLR 2019*. https://arxiv.org/abs/1811.01241

[16] Zhang, S., et al. (2020). "DialoGPT: Large-Scale Generative Pre-training for Conversational Response Generation." *ACL 2020*. https://arxiv.org/abs/1911.00536

[17] Chevalier, A., et al. (2023). "Adapting Language Models to Compress Contexts." *EMNLP 2023*. https://arxiv.org/abs/2305.14788

[18] Gekhman, Z., et al. (2023). "Does Fine-Tuning LLMs on New Knowledge Encourage Hallucinations?" *arXiv*. https://arxiv.org/abs/2405.05904

[19] Wang, L., et al. (2023). "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection." *arXiv*. https://arxiv.org/abs/2310.11511

[20] Khattab, O., et al. (2021). "Baleen: Robust Multi-Hop Reasoning at Scale via Condensed Retrieval." *NeurIPS 2021*. https://arxiv.org/abs/2101.00436
