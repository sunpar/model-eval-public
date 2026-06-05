# Copper Memo Demo

The copper memo demo is the MVP proof path for Model Eval: one final investment memo task, two models, two system prompts, and four structured conversation warmers. It is local-only and synthetic by default so the workflow is safe to rerun without provider credentials or live API cost.

## Run The Demo

Preview the manifest matrix:

```bash
python -m model_eval_cli.main preview examples/copper_memo_context_sensitivity.yaml
```

Expected shape:

- 1 case: `chile_copper_memo`
- 2 model configs: OpenAI high reasoning and Anthropic high reasoning
- 2 system prompts: expert investment analyst and general finance assistant
- 4 warmers: none, expert user, low-knowledge user, adversarial user
- 16 logical runs and 32 attempts with two replicates

Build the complete local demo and write export files:

```bash
python -m model_eval_cli.main demo copper-memo --export-dir .model-eval-demo-exports
```

The command creates or reuses the `copper-memo-demo` project, persists the copper memo library records, creates the full experiment, fills all attempts with synthetic local outputs, records deterministic scores, creates a blind pairwise review set, completes sample human reviews, and writes Markdown, CSV, and JSON exports.

## Review In The UI

Start the API and frontend:

```bash
make api
make frontend
```

Open the app, go to **Run Monitor**, refresh if needed, and use the review/results action on the copper demo experiment. The app opens **Comparison Workspace** with the persisted experiment selected, so the blind pairwise review set can be created or inspected without rebuilding a manifest in the browser.

Use **Reveal metadata** only after blind review is complete. Before reveal, model, system prompt, warmer, run attempt IDs, cost, and token metadata remain hidden from the reviewer.

## Results To Inspect

The **Results** screen should show:

- Warmer lift rows comparing each warmer against `none`.
- Context sensitivity rows by case, model, and system prompt across warmers.
- Failure tag frequency from sample blind human reviews.
- Cost, token, and latency summaries from synthetic attempts.
- Cost-quality frontier points.

The generated export directory contains:

- `copper_memo_demo_report.md`
- `copper_memo_demo_report.csv`
- `copper_memo_demo_report.json`

The exports are demo artifacts and should not be committed unless a future task explicitly asks for checked-in sample output.
