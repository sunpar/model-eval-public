import json

from typer.testing import CliRunner

from model_eval_cli.main import app


def test_seed_copper_memo_outputs_structured_local_demo_objects() -> None:
    result = CliRunner().invoke(app, ["seed", "copper-memo", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)

    assert payload["demo_id"] == "copper_memo_context_sensitivity"
    assert payload["mode"] == "local_only"
    assert payload["source_manifest"] == "examples/copper_memo_context_sensitivity.yaml"
    assert [case["id"] for case in payload["cases"]] == ["chile_copper_memo"]
    assert [prompt["id"] for prompt in payload["system_prompts"]] == [
        "expert_investment_analyst_v3",
        "general_finance_assistant_v2",
    ]
    assert [warmer["id"] for warmer in payload["warmers"]] == [
        "none",
        "copper_expert_user_v2",
        "copper_low_knowledge_user_v1",
        "copper_adversarial_user_v1",
    ]
    assert [evaluator["id"] for evaluator in payload["evaluators"]] == [
        "investment_memo_required_sections_v1",
        "investment_memo_token_budget_v1",
        "investment_memo_llm_judge_v2",
        "hallucinated_numbers_check_v1",
    ]
    assert payload["evaluators"][0]["definition"]["required_sections"] == [
        "thesis",
        "variant view",
        "risks",
        "watch items",
    ]
    assert payload["evaluators"][1]["definition"]["max_output_tokens"] == 1200
    logical_runs = (
        len(payload["cases"])
        * len(payload["model_configs"])
        * len(payload["system_prompts"])
        * len(payload["warmers"])
    )
    assert payload["experiment"]["design"]["logical_runs"] == logical_runs
    assert payload["experiment"]["design"]["run_attempts"] == (
        logical_runs * payload["experiment"]["design"]["replicates"]
    )

    expert_warmer = payload["warmers"][1]
    assert expert_warmer["domain"] == "commodities"
    assert expert_warmer["user_level"] == "expert"
    assert expert_warmer["intent"]
    assert len(expert_warmer["messages"]) >= 2
    assert expert_warmer["version"] == 2
    assert expert_warmer["version_history"]
