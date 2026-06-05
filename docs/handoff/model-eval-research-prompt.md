# Model Eval Handoff Prompt

You are reviewing a private repository named `model-eval`.

The project is a model-evaluation workbench for measuring how prior conversational context changes model behavior on the same final task. It should not become a generic clone of existing prompt eval, tracing, or observability tools. The unique wedge is first-class conversation warmers and context-sensitivity analytics.

Review priorities:

1. Validate whether the completed V1/V2 scope still preserves the warmer-first product wedge.
2. Stress-test the data model, especially Case, Experiment, Run, RunAttempt, Score, and ConversationWarmer.
3. Identify missing provider-abstraction details for OpenAI, Anthropic, Gemini, local models, artifacts, tools, structured output, and live provider policy.
4. Evaluate whether the current CLI/API/UI surfaces are sufficient for reproducible headless experiments and local-only demos.
5. Recommend the smallest V3 slice that would add production value without turning the product into generic observability.

The V1 copper memo demo compares the same final investment memo prompt across:

- Two models.
- Two system prompts.
- Four warmers: none, expert user, low-knowledge user, adversarial user.
- Blind pairwise review.
- Warmer lift.
- Context sensitivity.
- Cost-quality frontier.

Do not recommend broad production observability, deployment workflows, or team administration until the context-sensitivity experiment loop is working end to end.

The V2 demo is local-only and synthetic. It exercises benchmark suites, artifact preprocessing,
replicated attempts, multi-reviewer queues, judge calibration, metric adapters, analytics, and
exports without provider keys. V3 backlog items remain out of scope unless explicitly promoted:
production trace ingestion, active sampling, synthetic case generation, team administration,
evaluator CI gates, prompt deployment, model release comparison, custom providers, local model
hosting, and scheduled drift monitoring.
