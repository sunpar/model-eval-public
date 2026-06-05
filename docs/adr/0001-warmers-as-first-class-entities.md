# ADR 0001: Conversation Warmers Are First-Class Entities

## Status

Accepted

## Context

Model Eval specializes in path-dependence and context-sensitivity analysis. The MVP holds the final task, model, system prompt, and evaluation workflow constant while varying prior conversation context. If that prior context is stored as pasted prompt text, experiments cannot reliably compare, version, tag, audit, or reuse it.

## Decision

Conversation warmers are structured, versioned library entities. A warmer stores domain, user level, intent, ordered messages, tags, version, and version history. Experiments snapshot the selected warmer version just like they snapshot cases, model configs, and system prompts.

## Consequences

- Warmers can be varied as an explicit experimental factor.
- Warmer changes are auditable and do not mutate historical experiments.
- Context-sensitivity metrics can group by warmer metadata such as user level or intent.
- The MVP stays focused on context sensitivity instead of becoming a broad prompt-observability system.
- The CLI seed command must emit structured warmer objects, not flattened prompt text.
