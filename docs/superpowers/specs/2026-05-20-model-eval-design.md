# Model Eval Design

Status note, 2026-05-26: this is the original design brief. The current implementation has
advanced beyond the first-slice plan through V2 Phase 25. Current state is tracked in
`../../implementation-task-list.md`, `../../v2-implementation-task-list.md`,
`../../v2-demo.md`, and `../../../FEATURE_INVENTORY.md`.

## Purpose

Model Eval is a workbench for designing, running, reviewing, and comparing LLM experiments where prior conversation state is an explicit experimental variable.

The product should answer controlled questions, not just generate large matrices of outputs. The first durable question is: how does a model's final answer change when the final task is identical but the conversation leading into it differs?

## Scope

The initial repository should establish a narrow vertical slice:

- Structured libraries for cases, system prompts, conversation warmers, model configs, and evaluators.
- Experiment manifests that describe the study design.
- Full-factorial run preview and generation.
- Provider adapter boundaries that preserve normalized and raw config fields.
- Immutable run attempt snapshots.
- Human-first review workflows with blind pairwise comparison.
- Cost, token, and latency capture.
- Exports for Markdown, CSV, and JSON.

Out of scope for the first slice:

- Production trace ingestion.
- Team permissions.
- Prompt deployment.
- CI gating.
- Advanced statistical modeling beyond simple win and failure rates.

## Abstractions

Case is the task being tested.

Experiment is the controlled study design.

Run is one logical configuration on one case.

RunAttempt is one actual provider call.

ConversationWarmer is a structured, versioned conversation history used before the final task.

Score is a typed evaluation record from code, human review, or an LLM judge.

ReviewSet is a queue of outputs or output pairs prepared for blind human review.

## Experiment Designs

The app should support these designs over time:

- Full factorial.
- One-factor-at-a-time.
- Paired comparison.
- Fractional factorial.
- Replicated runs.
- Benchmark suite rerun.

The initial scaffold only previews full-factorial counts.

## Evaluation Layers

Deterministic checks should run first where possible: schema validity, required sections, citation requirements, token limits, expected values, and tool-call argument checks.

Human review should be the initial source of truth for subjective tasks. The review surface should support blind review, pairwise winner selection, pass/fail, notes, failure tags, and rubric annotations.

LLM-as-judge should come after human examples exist. It should randomize answer order, run position-swapped judging, control for verbosity bias, store judge prompts and explanations, and compare judge decisions against human labels.

Conversation-level scoring should evaluate final answer only, assistant turns, entire traces, artifact steps, and tool or retrieval steps.

## Signature Analytics

The product should make context sensitivity visible:

- Semantic divergence.
- Section-structure divergence.
- Claim divergence.
- Conclusion divergence.
- Confidence divergence.
- Token-length divergence.
- Score divergence.
- Failure-mode divergence.

The output should distinguish genuine quality lift from distortion or contamination.

## First Implementation Plan

1. Keep this repo buildable but modest.
2. Implement manifest parsing and full-factorial run generation.
3. Add persistent SQLAlchemy models and migrations.
4. Add provider adapter interfaces before real provider calls.
5. Add a local executor that stores run attempts.
6. Build the comparison workspace for blind pairwise review.
7. Add score capture and simple aggregation.
8. Add cost-quality and context-sensitivity result views.
