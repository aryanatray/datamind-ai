# Evaluation

The fixture generator produces fictional customers, orders, invoices, tickets, contracts and labeled benchmark questions. It does not use external customer data.

```bash
python datasets/generate.py
python -m datamind.evaluation --output docs/benchmark-results.json
```

The default evaluation runs reference SQL against fixture data and checks parser anchors. It measures supplied query correctness, not natural-language query generation.

To execute actual local embedding, sparse retrieval, fusion and reranking with Qdrant:

```bash
python -m datamind.evaluation --retrieval --output docs/retrieval-results.json
```

This creates an isolated evaluation collection and removes it afterward. It reports recall@k, reciprocal rank, nDCG, latency and hardware context. Model downloads and local CPU inference may be required. A small synthetic corpus does not establish performance at production scale.

`infrastructure/scripts/live_evaluation.py` and `score_evaluation.py` support a separate provider-backed phase and grading. Read their help first. Provider requests require explicit opt-in, a configured key and a controlled budget. Do not run them as ordinary CI tests or publish prompts/results containing private data.

Judge answer correctness, metric interpretation, join behavior, abstention, claim-level citation support, repair rate, latency and token consumption separately. Retrieval rank or citation existence alone does not establish a correct answer.
