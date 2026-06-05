# Product Brief

Model Eval is a reproducible experiment lab for testing how model, prompt, reasoning level, artifacts, and prior conversation context interact.

The app should not be positioned as a smaller Promptfoo, Braintrust, Langfuse, Phoenix, or Weave. Those tools already cover broad eval, trace, prompt, dataset, and observability workflows. Model Eval should specialize in path-dependence analysis: how the same final request changes when the model enters it with different conversation histories.

## Core Wedge

Conversation warmers are first-class experimental variables.

A warmer is not pasted chat text. It is a structured, versioned object with domain, user level, intent, messages, tags, and version history. Warmers can represent expert, beginner, adversarial, misleading, high-context, low-context, or no-prior-context conversations.

The product should make it easy to compare one final task across warmers while holding other factors constant.

## Product Modes

Playground is fast and disposable. It is for testing a prompt, swapping a model, trying a warmer, or comparing two outputs.

Experiment is immutable and reproducible. It stores exact input messages, system prompt snapshots, warmer snapshots, artifacts, model configs, request payloads, response payloads, timing, tokens, costs, scores, and review notes.

Benchmark Suite is a reusable regression pack. It reruns locked cases whenever a prompt, model, warmer, provider, artifact pipeline, or evaluator changes.

## Signature Analytics

- Context Sensitivity Score: how much quality or content changes when only the warmer changes.
- Warmer Lift: score with warmer minus score without warmer.
- Warmer Distortion: whether the warmer causes overfitting, unsupported assumptions, or echoing prior framing.
- Framing Divergence: how conclusions, structure, tone, and claims differ across warmers.
- Conversation Carryover Audit: which warmup details were reused, ignored, or hallucinated.

## MVP Demo

The highest-value first demo is:

- One investment memo final prompt.
- Two models.
- Two system prompts.
- Four warmers: none, expert user, low-knowledge user, adversarial user.
- Sixteen logical outputs.
- Blind pairwise review.
- Warmer lift chart.
- Context sensitivity chart.
- Cost-quality frontier.
- Exported memo comparing winners and failure modes.
