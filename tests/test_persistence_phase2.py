from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from model_eval_api.manifest import load_manifest_file, parse_manifest
from model_eval_api.persistence.database import create_database_engine
from model_eval_api.persistence.models import (
    Artifact,
    Base,
    Case,
    ConversationWarmer,
    Evaluator,
    ModelConfig,
    ReviewItem,
    Run,
    RunAttempt,
    Score,
    SystemPrompt,
)
from model_eval_api.persistence.repositories import (
    create_artifact,
    create_case,
    create_conversation_warmer,
    create_evaluator,
    create_experiment_from_manifest,
    create_model_config,
    create_project,
    create_review_item,
    create_review_set,
    create_system_prompt,
    create_workspace,
    record_score,
    record_run_attempt,
)
from model_eval_api.persistence.snapshots import build_model_input_snapshot


EXAMPLE_MANIFEST = Path("examples/copper_memo_context_sensitivity.yaml")


@pytest.fixture()
def session():
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        yield db


def seed_copper_libraries(session, project) -> None:
    create_case(
        session, project=project, slug="chile_copper_memo", name="Chile", prompt="Original case"
    )
    create_system_prompt(
        session,
        project=project,
        slug="expert_investment_analyst_v3",
        name="Expert analyst",
        prompt="Original prompt",
    )
    create_system_prompt(
        session,
        project=project,
        slug="general_finance_assistant_v2",
        name="General finance",
        prompt="General prompt",
    )
    create_model_config(
        session,
        project=project,
        slug="openai_gpt_high",
        name="OpenAI high",
        provider="openai",
        model="gpt-5.5",
        raw_provider_params={"reasoning_effort": "high", "temperature": 0.2},
    )
    create_model_config(
        session,
        project=project,
        slug="claude_high",
        name="Claude high",
        provider="anthropic",
        model="claude-opus",
        raw_provider_params={"thinking_budget": "high", "temperature": 0.2},
    )
    for warmer_slug in [
        "none",
        "copper_expert_user_v2",
        "copper_low_knowledge_user_v1",
        "copper_adversarial_user_v1",
    ]:
        create_conversation_warmer(
            session,
            project=project,
            slug=warmer_slug,
            name=warmer_slug,
            messages=[{"role": "user", "content": f"Original {warmer_slug}"}],
            domain="commodities",
            user_level="expert",
            intent="memo",
            tags=["copper"],
        )
    for evaluator_slug in [
        "investment_memo_required_sections_v1",
        "investment_memo_token_budget_v1",
        "investment_memo_llm_judge_v2",
        "hallucinated_numbers_check_v1",
    ]:
        create_evaluator(
            session,
            project=project,
            slug=evaluator_slug,
            name=evaluator_slug,
            evaluator_type="deterministic",
            definition={"criterion": evaluator_slug},
        )


def test_alembic_upgrade_head_runs_against_temp_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "migration.sqlite"
    monkeypatch.setenv("MODEL_EVAL_DATABASE_URL", f"sqlite+pysqlite:///{db_path}")

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as connection:
        table_names = {
            row[0]
            for row in connection.exec_driver_sql(
                "select name from sqlite_master where type = 'table'"
            )
        }

    assert {
        "workspaces",
        "projects",
        "cases",
        "artifacts",
        "system_prompts",
        "conversation_warmers",
        "model_configs",
        "evaluators",
        "experiments",
        "runs",
        "run_attempts",
        "scores",
        "review_sets",
        "review_items",
    }.issubset(table_names)


def test_project_scoped_slugs_are_unique(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    create_case(session, project=project, slug="memo", name="Memo", prompt="Write memo")
    session.commit()

    create_case(
        session,
        project=project,
        slug="memo",
        name="Memo v2",
        prompt="Write another memo",
        version=2,
    )
    session.commit()

    create_case(session, project=project, slug="memo", name="Memo duplicate", prompt="Duplicate")
    with pytest.raises(IntegrityError):
        session.commit()


def test_experiment_snapshots_do_not_change_when_library_objects_are_versioned(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)

    experiment = create_experiment_from_manifest(
        session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )
    original_case_snapshot = experiment.case_snapshots["chile_copper_memo"]
    original_warmer_snapshot = experiment.warmer_snapshots["copper_expert_user_v2"]
    original_prompt_snapshot = experiment.system_prompt_snapshots["expert_investment_analyst_v3"]
    original_model_snapshot = experiment.model_config_snapshots["openai_gpt_high"]
    original_evaluator_snapshot = experiment.evaluator_snapshots[
        "investment_memo_required_sections_v1"
    ]

    case = session.scalar(select(Case).where(Case.slug == "chile_copper_memo"))
    warmer = session.scalar(
        select(ConversationWarmer).where(ConversationWarmer.slug == "copper_expert_user_v2")
    )
    system_prompt = session.scalar(
        select(SystemPrompt).where(SystemPrompt.slug == "expert_investment_analyst_v3")
    )
    model_config = session.scalar(select(ModelConfig).where(ModelConfig.slug == "openai_gpt_high"))
    evaluator = session.scalar(
        select(Evaluator).where(Evaluator.slug == "investment_memo_required_sections_v1")
    )
    assert case is not None
    assert warmer is not None
    assert system_prompt is not None
    assert model_config is not None
    assert evaluator is not None
    case.prompt = "Edited case"
    case.version = 2
    warmer.messages = [{"role": "user", "content": "Edited warmer"}]
    warmer.version = 3
    system_prompt.prompt = "Edited prompt"
    system_prompt.version = 4
    model_config.raw_provider_params = {"reasoning_effort": "low", "temperature": 0.9}
    model_config.version = 5
    evaluator.definition = {"criterion": "edited"}
    evaluator.version = 6
    session.commit()
    session.refresh(experiment)

    assert experiment.case_snapshots["chile_copper_memo"] == original_case_snapshot
    assert experiment.case_snapshots["chile_copper_memo"]["prompt"] == "Original case"
    assert experiment.warmer_snapshots["copper_expert_user_v2"] == original_warmer_snapshot
    assert experiment.warmer_snapshots["copper_expert_user_v2"]["messages"] == [
        {"role": "user", "content": "Original copper_expert_user_v2"}
    ]
    assert (
        experiment.system_prompt_snapshots["expert_investment_analyst_v3"]
        == original_prompt_snapshot
    )
    assert (
        experiment.system_prompt_snapshots["expert_investment_analyst_v3"]["prompt"]
        == "Original prompt"
    )
    assert experiment.model_config_snapshots["openai_gpt_high"] == original_model_snapshot
    assert (
        experiment.model_config_snapshots["openai_gpt_high"]["raw_provider_params"][
            "reasoning_effort"
        ]
        == "high"
    )
    assert (
        experiment.evaluator_snapshots["investment_memo_required_sections_v1"]
        == original_evaluator_snapshot
    )
    assert experiment.evaluator_snapshots["investment_memo_required_sections_v1"][
        "definition"
    ] == {"criterion": "investment_memo_required_sections_v1"}
    assert case.snapshot["prompt"] == "Edited case"
    assert warmer.snapshot["messages"] == [{"role": "user", "content": "Edited warmer"}]
    assert system_prompt.snapshot["prompt"] == "Edited prompt"
    assert model_config.snapshot["raw_provider_params"] == {
        "reasoning_effort": "low",
        "temperature": 0.9,
    }
    assert evaluator.snapshot["definition"] == {"criterion": "edited"}


def test_model_input_snapshot_preserves_prompt_ref_system_messages() -> None:
    snapshot = build_model_input_snapshot(
        case_snapshot={
            "id": "case",
            "prompt": "Write the memo.",
            "prompt_ref": None,
        },
        system_prompt_snapshot={
            "id": "system",
            "prompt": None,
            "prompt_ref": "prompts/system.md",
            "messages": [],
        },
        warmer_snapshot={"id": "none", "intent": None, "messages": []},
        artifact_snapshots={},
    )

    assert snapshot["final_messages"] == [
        {"role": "system", "content_ref": "prompts/system.md"},
        {"role": "user", "content": "Write the memo."},
    ]


def test_id_only_manifest_references_must_resolve_to_library_objects(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")

    with pytest.raises(
        ValueError, match="System prompt reference 'expert_investment_analyst_v3'"
    ):
        create_experiment_from_manifest(
            session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
        )


def test_version_only_warmer_and_artifact_references_must_resolve(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    payload = load_manifest_file(EXAMPLE_MANIFEST).model_dump(mode="json")

    payload["warmers"].append({"id": "missing_warmer", "version": 2})
    with pytest.raises(ValueError, match="Conversation warmer reference 'missing_warmer'"):
        create_experiment_from_manifest(session, project=project, manifest=parse_manifest(payload))

    payload = load_manifest_file(EXAMPLE_MANIFEST).model_dump(mode="json")
    payload["artifacts"] = [{"id": "missing_artifact", "version": 2}]
    with pytest.raises(ValueError, match="Artifact reference 'missing_artifact'"):
        create_experiment_from_manifest(session, project=project, manifest=parse_manifest(payload))


def test_manifest_version_refs_snapshot_requested_library_version(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    create_conversation_warmer(
        session,
        project=project,
        slug="copper_expert_user_v2",
        name="Expert v2",
        messages=[{"role": "user", "content": "Version 2 warmer"}],
        version=2,
    )
    create_artifact(
        session,
        project=project,
        slug="source_excerpt",
        name="Source excerpt v1",
        uri="file://artifacts/source_excerpt_v1.txt",
        version=1,
    )
    create_artifact(
        session,
        project=project,
        slug="source_excerpt",
        name="Source excerpt v2",
        uri="file://artifacts/source_excerpt_v2.txt",
        version=2,
    )
    payload = load_manifest_file(EXAMPLE_MANIFEST).model_dump(mode="json")
    payload["warmers"][1]["version"] = 1
    payload["artifacts"] = [{"id": "source_excerpt", "version": 1}]

    experiment = create_experiment_from_manifest(
        session, project=project, manifest=parse_manifest(payload)
    )

    assert experiment.warmer_snapshots["copper_expert_user_v2"]["version"] == 1
    assert experiment.warmer_snapshots["copper_expert_user_v2"]["messages"] == [
        {"role": "user", "content": "Original copper_expert_user_v2"}
    ]
    assert experiment.artifact_snapshots["source_excerpt"]["version"] == 1
    assert (
        experiment.artifact_snapshots["source_excerpt"]["uri"]
        == "file://artifacts/source_excerpt_v1.txt"
    )


def test_artifact_snapshots_do_not_change_when_library_objects_are_versioned(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    create_artifact(
        session,
        project=project,
        slug="source_excerpt",
        name="Source excerpt",
        artifact_type="document",
        uri="file://artifacts/source_excerpt_v1.txt",
        metadata={"sha256": "original"},
    )
    payload = load_manifest_file(EXAMPLE_MANIFEST).model_dump(mode="json")
    payload["artifacts"] = ["source_excerpt"]
    experiment = create_experiment_from_manifest(
        session, project=project, manifest=parse_manifest(payload)
    )
    original_artifact_snapshot = experiment.artifact_snapshots["source_excerpt"]

    artifact = session.scalar(select(Artifact).where(Artifact.slug == "source_excerpt"))
    assert artifact is not None
    artifact.uri = "file://artifacts/source_excerpt_v2.txt"
    artifact.metadata_json = {"sha256": "edited"}
    artifact.version = 2
    session.commit()
    session.refresh(experiment)

    assert experiment.artifact_snapshots["source_excerpt"] == original_artifact_snapshot
    assert experiment.artifact_snapshots["source_excerpt"]["metadata"] == {"sha256": "original"}


def test_provider_params_are_redacted_in_persistence_snapshots(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")

    model_config = create_model_config(
        session,
        project=project,
        slug="openai_secret",
        name="OpenAI secret",
        provider="openai",
        model="gpt-5.5",
        raw_provider_params={
            "temperature": 0.2,
            "api_key": "secret",
            "nested": {"authorization": "Bearer secret", "safe": "kept"},
            "headers": {"x-api-key": "secret"},
        },
    )
    session.commit()

    persisted = session.scalar(select(ModelConfig).where(ModelConfig.slug == "openai_secret"))
    assert persisted is not None
    assert persisted.raw_provider_params["temperature"] == 0.2
    assert persisted.raw_provider_params["api_key"] == "[redacted]"
    assert persisted.raw_provider_params["nested"] == {
        "authorization": "[redacted]",
        "safe": "kept",
    }
    assert persisted.raw_provider_params["headers"] == "[redacted]"
    assert model_config.snapshot["raw_provider_params"]["api_key"] == "[redacted]"

    persisted.raw_provider_params = {"temperature": 0.4, "api_key": "new-secret"}
    session.commit()
    session.refresh(persisted)

    assert persisted.raw_provider_params == {"temperature": 0.4, "api_key": "[redacted]"}
    assert persisted.snapshot["raw_provider_params"] == {
        "temperature": 0.4,
        "api_key": "[redacted]",
    }


def test_manifest_snapshots_redact_inline_provider_params(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    payload = load_manifest_file(EXAMPLE_MANIFEST).model_dump(mode="json")
    payload["models"][0]["params"]["api_key"] = "secret"
    payload["models"][0]["authorization"] = "Bearer secret"

    experiment = create_experiment_from_manifest(
        session, project=project, manifest=parse_manifest(payload)
    )

    assert experiment.manifest_snapshot["models"][0]["params"]["api_key"] == "[redacted]"
    assert experiment.manifest_snapshot["models"][0]["authorization"] == "[redacted]"


def test_experiment_design_snapshot_stores_effective_random_seed(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)

    experiment = create_experiment_from_manifest(
        session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )

    assert experiment.design_snapshot["randomize_run_order"] is True
    assert isinstance(experiment.design_snapshot["random_seed"], int)


def test_sqlite_foreign_keys_are_enforced() -> None:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as db:
        db.add(RunAttempt(run_id=999, attempt_id="orphan", replicate_index=0))
        with pytest.raises(IntegrityError):
            db.commit()


def test_runs_and_attempts_are_separate_unique_records(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    experiment = create_experiment_from_manifest(
        session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )
    session.commit()

    runs = session.scalars(select(Run).where(Run.experiment_id == experiment.id)).all()
    attempts = session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
    ).all()

    assert len(runs) == 16
    assert len(attempts) == 32
    assert {attempt.run_id for attempt in attempts}.issubset({run.id for run in runs})
    assert len({run.run_id for run in runs}) == 16
    assert len({attempt.attempt_id for attempt in attempts}) == 32
    assert all(len(run.attempts) == 2 for run in runs)

    first_attempt = attempts[0]
    retry_attempt = record_run_attempt(
        session,
        run=first_attempt.run,
        attempt_id="retry-attempt",
        replicate_index=first_attempt.replicate_index,
    )
    session.commit()
    assert retry_attempt.id is not None

    record_run_attempt(
        session,
        run=first_attempt.run,
        attempt_id=first_attempt.attempt_id,
        replicate_index=first_attempt.replicate_index,
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_scores_and_review_items_persist_against_attempts(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    experiment = create_experiment_from_manifest(
        session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )
    session.commit()

    attempt = session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == experiment.id)
    ).first()
    assert attempt is not None

    record_score(
        session,
        run_attempt=attempt,
        type="pass_fail",
        evaluator_type="human",
        criterion="memo_quality",
        value={"passed": True},
        explanation="Sufficient for baseline review.",
        confidence=0.8,
        evaluator_version=1,
    )
    review_set = create_review_set(
        session,
        project=project,
        slug="copper-review",
        name="Copper Review",
        experiment=experiment,
    )
    create_review_item(
        session,
        review_set=review_set,
        item_key="item-1",
        run_attempt=attempt,
        prompt_snapshot={"case_id": "chile_copper_memo"},
        answer_snapshot={"text": "memo"},
        metadata={"blind": True},
    )
    session.commit()

    score = session.scalar(select(Score).where(Score.run_attempt_id == attempt.id))
    review_item = session.scalar(select(ReviewItem).where(ReviewItem.item_key == "item-1"))

    assert score is not None
    assert score.value == {"passed": True}
    assert score.evaluator_version == 1
    assert review_item is not None
    assert review_item.run_attempt_id == attempt.id
    assert review_item.metadata_json == {"blind": True}


def test_run_attempt_payloads_are_redacted(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    experiment = create_experiment_from_manifest(
        session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )
    run = session.scalars(select(Run).where(Run.experiment_id == experiment.id)).first()
    assert run is not None

    attempt = record_run_attempt(
        session,
        run=run,
        attempt_id="payload-attempt",
        replicate_index=100,
        request_payload={"headers": {"authorization": "Bearer secret"}, "prompt": "safe"},
        response_payload={"api_key": "secret", "text": "safe"},
    )
    session.commit()
    session.refresh(attempt)

    assert attempt.request_payload == {"headers": "[redacted]", "prompt": "safe"}
    assert attempt.response_payload == {"api_key": "[redacted]", "text": "safe"}


def test_inline_evaluator_requires_substantive_definition(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    payload = load_manifest_file(EXAMPLE_MANIFEST).model_dump(mode="json")
    payload["evaluation"]["evaluators"].append({"id": "missing_evaluator", "version": 2})

    with pytest.raises(ValueError, match="Evaluator reference 'missing_evaluator'"):
        create_experiment_from_manifest(session, project=project, manifest=parse_manifest(payload))


def test_review_items_link_unflushed_attempts_and_enforce_experiment_scope(session) -> None:
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="copper", name="Copper")
    seed_copper_libraries(session, project)
    experiment = create_experiment_from_manifest(
        session, project=project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )
    run = session.scalars(select(Run).where(Run.experiment_id == experiment.id)).first()
    assert run is not None

    review_set = create_review_set(
        session,
        project=project,
        slug="manual-review",
        name="Manual Review",
        experiment=experiment,
    )
    manual_attempt = record_run_attempt(
        session,
        run=run,
        attempt_id="manual-attempt",
        replicate_index=99,
    )
    review_item = create_review_item(
        session,
        review_set=review_set,
        item_key="manual-item",
        run_attempt=manual_attempt,
        prompt_snapshot={"case_id": run.case_slug},
        answer_snapshot={"text": "memo"},
    )
    session.commit()

    assert review_item.run_attempt_id == manual_attempt.id

    other_project = create_project(session, workspace=workspace, slug="other", name="Other")
    seed_copper_libraries(session, other_project)
    other_experiment = create_experiment_from_manifest(
        session, project=other_project, manifest=load_manifest_file(EXAMPLE_MANIFEST)
    )
    other_attempt = session.scalars(
        select(RunAttempt).join(Run).where(Run.experiment_id == other_experiment.id)
    ).first()
    assert other_attempt is not None

    with pytest.raises(ValueError, match="Review set experiment must belong"):
        create_review_set(
            session,
            project=other_project,
            slug="bad-review",
            name="Bad Review",
            experiment=experiment,
        )
    with pytest.raises(ValueError, match="Review item attempt must belong"):
        create_review_item(
            session,
            review_set=review_set,
            item_key="wrong-experiment",
            run_attempt=other_attempt,
        )
