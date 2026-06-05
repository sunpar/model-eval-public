from __future__ import annotations

from typing import Any


def copper_memo_seed_payload() -> dict[str, Any]:
    """Return local-only seed objects for the copper memo MVP demo."""

    payload = {
        "demo_id": "copper_memo_context_sensitivity",
        "mode": "local_only",
        "source_manifest": "examples/copper_memo_context_sensitivity.yaml",
        "cases": [
            {
                "id": "chile_copper_memo",
                "name": "Chile copper disruption investment memo",
                "domain": "commodities",
                "prompt": (
                    "Write me a proper investment memo on the Chilean copper disruption "
                    "scenario we discussed. Be as thorough and specific as you can. "
                    "Do not hold back."
                ),
                "tags": ["copper", "chile", "investment-memo", "context-sensitivity"],
                "version": 1,
                "version_history": [
                    {
                        "version": 1,
                        "note": "Initial MVP case from the copper memo context-sensitivity manifest.",
                    }
                ],
            }
        ],
        "system_prompts": [
            {
                "id": "expert_investment_analyst_v3",
                "name": "Expert investment analyst",
                "prompt": (
                    "You are a senior commodities investment analyst. Produce specific, "
                    "decision-oriented investment memos with clear mechanisms, risks, "
                    "trade expression, and caveats."
                ),
                "tags": ["investment", "commodities", "expert"],
                "version": 3,
                "version_history": [
                    {"version": 3, "note": "MVP analyst prompt snapshot for copper memo demo."}
                ],
            },
            {
                "id": "general_finance_assistant_v2",
                "name": "General finance assistant",
                "prompt": (
                    "You are a careful finance assistant. Explain investment situations "
                    "clearly, avoid unsupported certainty, and distinguish facts from "
                    "assumptions."
                ),
                "tags": ["finance", "general"],
                "version": 2,
                "version_history": [
                    {"version": 2, "note": "MVP general finance prompt snapshot."}
                ],
            },
        ],
        "warmers": [
            {
                "id": "none",
                "name": "No prior conversation",
                "domain": "commodities",
                "user_level": "none",
                "intent": "Hold the final task constant without prior conversation context.",
                "messages": [],
                "tags": ["baseline", "no-context"],
                "version": 1,
                "version_history": [{"version": 1, "note": "Baseline no-warmer condition."}],
            },
            {
                "id": "copper_expert_user_v2",
                "name": "Copper expert user",
                "domain": "commodities",
                "user_level": "expert",
                "intent": (
                    "Prime market-structure reasoning around Chile supply disruption, "
                    "inventory channels, treatment charges, and trade construction."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Assume I already know the basics of copper supply and demand. "
                            "I care about inventory transmission, smelter constraints, and "
                            "where the market may misprice a Chile disruption."
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "Understood. I will focus on disruption channels, refined versus "
                            "concentrate tightness, treatment charges, substitution limits, "
                            "and trade expression rather than generic copper background."
                        ),
                    },
                ],
                "tags": ["copper", "expert", "market-structure", "investment-memo"],
                "version": 2,
                "version_history": [
                    {"version": 1, "note": "Initial expert copper framing."},
                    {"version": 2, "note": "Added explicit inventory and trade-expression focus."},
                ],
            },
            {
                "id": "copper_low_knowledge_user_v1",
                "name": "Copper low-knowledge user",
                "domain": "commodities",
                "user_level": "beginner",
                "intent": (
                    "Prime the model with a user who needs copper market concepts explained "
                    "without losing memo specificity."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "I am not very familiar with copper markets. Please help me "
                            "understand what matters before writing anything investment-focused."
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "I will define the key moving pieces: mine supply, concentrates, "
                            "smelting, refined copper, inventories, demand, and how disruptions "
                            "can affect prices and equities."
                        ),
                    },
                ],
                "tags": ["copper", "beginner", "explanatory", "investment-memo"],
                "version": 1,
                "version_history": [
                    {"version": 1, "note": "Initial low-knowledge copper framing."}
                ],
            },
            {
                "id": "copper_adversarial_user_v1",
                "name": "Copper adversarial user",
                "domain": "commodities",
                "user_level": "advanced",
                "intent": (
                    "Prime skeptical review of consensus copper disruption claims and force "
                    "attention to false positives, crowded trades, and overconfidence."
                ),
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "I think most Chile copper disruption notes are lazy bullish takes. "
                            "Push back on the obvious thesis and look for ways it could be wrong."
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            "I will pressure-test the bullish framing, separate temporary supply "
                            "noise from durable tightness, and call out crowded or poorly hedged "
                            "trade expressions."
                        ),
                    },
                ],
                "tags": ["copper", "adversarial", "skeptical", "risk"],
                "version": 1,
                "version_history": [
                    {"version": 1, "note": "Initial adversarial copper framing."}
                ],
            },
        ],
        "model_configs": [
            {
                "id": "openai_gpt_high",
                "provider": "openai",
                "model": "gpt-5.5",
                "reasoning_level": "high",
                "temperature": 0.2,
                "raw_provider_params": {"reasoning_effort": "high", "temperature": 0.2},
                "version": 1,
            },
            {
                "id": "claude_high",
                "provider": "anthropic",
                "model": "claude-opus",
                "reasoning_level": "high",
                "temperature": 0.2,
                "raw_provider_params": {"thinking_budget": "high", "temperature": 0.2},
                "version": 1,
            },
        ],
        "evaluators": [
            {
                "id": "investment_memo_required_sections_v1",
                "name": "Investment memo required sections",
                "evaluator_type": "deterministic",
                "definition": {
                    "kind": "required_sections",
                    "criterion": "investment_memo_required_sections",
                    "required_sections": ["thesis", "variant view", "risks", "watch items"],
                },
                "version": 1,
            },
            {
                "id": "investment_memo_token_budget_v1",
                "name": "Investment memo token budget",
                "evaluator_type": "deterministic",
                "definition": {
                    "kind": "token_budget",
                    "criterion": "investment_memo_token_budget",
                    "max_output_tokens": 1200,
                },
                "version": 1,
            },
            {
                "id": "investment_memo_llm_judge_v2",
                "name": "Investment memo LLM judge",
                "evaluator_type": "llm_judge",
                "definition": {"criterion": "investment_memo_llm_judge"},
                "version": 2,
            },
            {
                "id": "hallucinated_numbers_check_v1",
                "name": "Hallucinated numbers placeholder",
                "evaluator_type": "deterministic",
                "definition": {
                    "kind": "citation_required",
                    "criterion": "citation_required",
                },
                "version": 1,
            },
        ],
        "experiment": {
            "id": "copper_memo_context_sensitivity_mvp",
            "name": "Copper memo context sensitivity MVP",
            "design": {
                "type": "full_factorial",
                "replicates": 2,
                "randomize_run_order": True,
                "logical_runs": 0,
                "run_attempts": 0,
            },
            "evaluation": {
                "blind_review": True,
                "human_pairwise": True,
                "evaluators": [
                    "investment_memo_required_sections_v1",
                    "investment_memo_token_budget_v1",
                    "investment_memo_llm_judge_v2",
                    "hallucinated_numbers_check_v1",
                ],
            },
        },
    }

    design = payload["experiment"]["design"]
    logical_runs = (
        len(payload["cases"])
        * len(payload["model_configs"])
        * len(payload["system_prompts"])
        * len(payload["warmers"])
    )
    design["logical_runs"] = logical_runs
    design["run_attempts"] = logical_runs * design["replicates"]
    return payload
