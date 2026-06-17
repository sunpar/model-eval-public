from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base, ReviewItem, Score
from model_eval_api.persistence.repositories import (
    DEFAULT_COPPER_FAILURE_TAGS,
    create_experiment_from_manifest,
    create_project,
    create_review_set_from_completed_experiment,
    create_workspace,
    pairwise_aggregation_inputs,
    record_review_decision,
)


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        yield db


def test_completed_experiment_creates_blind_randomized_pairwise_review_items(
    session: Session,
) -> None:
    experiment = _completed_experiment(session)

    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="phase-8-review",
        name="Phase 8 review",
        random_seed=1,
    )
    session.commit()

    assert review_set.metadata_json["failure_tags"] == DEFAULT_COPPER_FAILURE_TAGS
    assert len(review_set.items) == 1
    item = review_set.items[0]
    assert item.item_key == "pair_0001"
    assert item.metadata_json["group"] == {
        "case_slug": "case",
        "system_prompt_slug": "system",
        "warmer_slug": "warmer",
        "replicate_index": 0,
        "pair_index": 1,
    }
    answer_attempt_ids = [answer["run_attempt_id"] for answer in item.answer_snapshot["answers"]]
    natural_attempt_ids = sorted(answer_attempt_ids)

    assert answer_attempt_ids == list(reversed(natural_attempt_ids))
    assert item.metadata_json["answer_order"] == answer_attempt_ids
    assert item.metadata_json["blind"] is True
    assert item.metadata_json["reveal_metadata"]["answers"][0]["model_config_slug"]
    assert item.answer_snapshot["answers"][0] == {
        "label": "A",
        "run_attempt_id": answer_attempt_ids[0],
        "text": "answer-1",
    }
    assert "model_config_slug" not in item.answer_snapshot["answers"][0]
    assert "cost_usd" not in item.answer_snapshot["answers"][0]


def test_review_items_extract_nested_provider_output_text(session: Session) -> None:
    experiment = _completed_experiment(session)
    runs = sorted(experiment.runs, key=lambda item: item.model_config_slug)
    runs[0].attempts[0].response_payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "OpenAI "},
                    {"type": "text", "text": "memo"},
                ]
            }
        ]
    }
    runs[1].attempts[0].response_payload = {
        "content": [{"type": "text", "text": "Claude memo"}]
    }

    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="nested-output-review",
        name="Nested Output Review",
        random_seed=1,
    )
    session.commit()

    answer_texts = {answer["text"] for answer in review_set.items[0].answer_snapshot["answers"]}

    assert answer_texts == {"OpenAI memo", "Claude memo"}


def test_review_items_fall_back_past_blank_choice_output_text(session: Session) -> None:
    experiment = _completed_experiment(session)
    runs = sorted(experiment.runs, key=lambda item: item.model_config_slug)
    runs[0].attempts[0].response_payload = {
        "choices": [
            {"message": {"content": "   "}},
            {"message": {"content": "Choice memo"}},
        ]
    }
    runs[1].attempts[0].response_payload = {"text": "Claude memo"}

    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="choice-output-review",
        name="Choice Output Review",
        random_seed=1,
    )
    session.commit()

    answer_texts = {answer["text"] for answer in review_set.items[0].answer_snapshot["answers"]}

    assert answer_texts == {"Choice memo", "Claude memo"}


def test_completed_experiment_creates_pair_for_each_replicate(
    session: Session,
) -> None:
    experiment = _completed_experiment(session, replicates=2)

    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="replicate-review",
        name="Replicate Review",
        random_seed=1,
    )
    session.commit()

    assert len(review_set.items) == 2
    assert sorted(item.metadata_json["group"]["replicate_index"] for item in review_set.items) == [
        0,
        1,
    ]
    assert all(len(item.answer_snapshot["answers"]) == 2 for item in review_set.items)


def test_review_set_api_hides_metadata_until_reveal(session: Session) -> None:
    experiment = _completed_experiment(session)

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        created = client.post(
            f"/projects/review/experiments/{experiment.id}/review-sets",
            json={"slug": "api-review", "name": "API Review", "random_seed": 1},
        )
        assert created.status_code == 201
        review_set_id = created.json()["id"]
        repeated = client.post(
            f"/projects/review/experiments/{experiment.id}/review-sets",
            json={"slug": "api-review", "name": "API Review", "random_seed": 1},
        )
        assert repeated.status_code == 201
        assert repeated.json()["id"] == review_set_id
        mismatched = client.post(
            f"/projects/review/experiments/{experiment.id}/review-sets",
            json={"slug": "api-review", "name": "Different Review", "random_seed": 1},
        )
        assert mismatched.status_code == 409
        assert (
            mismatched.json()["detail"]
            == "Review set already exists with different parameters."
        )

        blind = client.get(f"/review-sets/{review_set_id}")
        assert blind.status_code == 200
        assert "project_id" not in blind.json()
        assert "experiment_id" not in blind.json()
        assert "source_experiment_id" not in blind.json()["metadata"]
        blind_item = blind.json()["items"][0]
        assert blind_item["item_key"] == f"review-item-{blind_item['id']}"
        assert blind_item["answers"][0]["label"] == "A"
        assert "run_attempt_id" not in blind_item["answers"][0]
        assert "model_config_slug" not in blind_item["answers"][0]
        assert "reveal_metadata" not in blind_item

        decision = client.post(
            f"/review-items/{blind_item['id']}/decision",
            json={
                "reviewer_id": "human-reviewer",
                "winner": "A",
                "pass_fail": {"A": True, "B": False},
                "failure_tags": {"B": ["too generic"]},
                "rubric_notes": {},
            },
        )
        assert decision.status_code == 200
        assert "reveal_metadata" not in decision.json()
        assert decision.json()["reviewer_decision"] == {}

        blind_after_decision = client.get(f"/review-sets/{review_set_id}")
        assert blind_after_decision.status_code == 200
        assert blind_after_decision.json()["items"][0]["reviewer_decision"] == {}

        revealed = client.get(f"/review-sets/{review_set_id}?reveal_metadata=true")
        assert revealed.status_code == 200
        assert revealed.json()["metadata"]["source_experiment_id"] == experiment.id
        revealed_item = revealed.json()["items"][0]
        assert revealed_item["item_key"] == "pair_0001"
        assert "run_attempt_id" in revealed_item["answers"][0]
        assert revealed_item["reveal_metadata"]["answers"][0]["model_config_slug"]
        assert "system_prompt_slug" in revealed_item["reveal_metadata"]["answers"][0]
        assert "warmer_slug" in revealed_item["reveal_metadata"]["answers"][0]
        assert "cost_usd" in revealed_item["reveal_metadata"]["answers"][0]
    finally:
        api_module.app.dependency_overrides.clear()


def test_review_set_api_lists_existing_set_by_experiment_and_slug(session: Session) -> None:
    experiment = _completed_experiment(session)

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        created = client.post(
            f"/projects/review/experiments/{experiment.id}/review-sets",
            json={"slug": "api-review", "name": "API Review"},
        )
        assert created.status_code == 201

        existing = client.get(
            f"/projects/review/experiments/{experiment.id}/review-sets?slug=api-review"
        )

        assert existing.status_code == 200
        assert [item["id"] for item in existing.json()] == [created.json()["id"]]
        assert existing.json()[0]["slug"] == "api-review"
    finally:
        api_module.app.dependency_overrides.clear()


def test_review_decision_persists_typed_scores_for_each_answer(session: Session) -> None:
    experiment = _completed_experiment(session)
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="decision-review",
        name="Decision Review",
        random_seed=1,
    )
    session.flush()
    item = review_set.items[0]
    answer_ids = [answer["run_attempt_id"] for answer in item.answer_snapshot["answers"]]

    record_review_decision(
        session,
        review_item=item,
        reviewer_id="human-reviewer",
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic", "spot/futures confusion"]},
        rubric_notes={"A": "usable", "B": "thin"},
        notes="A is stronger for this blind pair.",
        confidence=0.75,
    )
    session.commit()

    persisted = session.get(ReviewItem, item.id)
    assert persisted is not None
    assert persisted.reviewer_decision["winner"] == "A"
    assert persisted.reviewer_decision["answer_order"] == answer_ids

    scores = session.scalars(select(Score).order_by(Score.id)).all()
    assert [score.type for score in scores] == [
        "pairwise_preference",
        "pairwise_preference",
        "pass_fail",
        "pass_fail",
        "failure_tags",
        "rubric_notes",
        "rubric_notes",
        "freeform_notes",
        "freeform_notes",
    ]
    assert scores[0].run_attempt_id == answer_ids[0]
    assert scores[0].value == {
        "label": "A",
        "outcome": "winner",
        "winner": "A",
        "review_item_id": item.id,
        "reviewer_id": "human-reviewer",
    }
    assert scores[1].run_attempt_id == answer_ids[1]
    assert scores[1].value == {
        "label": "B",
        "outcome": "loser",
        "winner": "A",
        "review_item_id": item.id,
        "reviewer_id": "human-reviewer",
    }
    assert [score.criterion for score in scores if score.type == "failure_tags"] == [
        "blind_pairwise_failure_tags"
    ]
    assert scores[4].value == {
        "label": "B",
        "tags": ["too generic", "spot/futures confusion"],
        "review_item_id": item.id,
        "reviewer_id": "human-reviewer",
    }


def test_review_decision_rejects_unknown_answer_labels(session: Session) -> None:
    experiment = _completed_experiment(session)
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="unknown-label-review",
        name="Unknown Label Review",
        random_seed=1,
    )
    session.flush()

    with pytest.raises(ValueError, match="unknown answer labels: C"):
        record_review_decision(
            session,
            review_item=review_set.items[0],
            reviewer_id="human-reviewer",
            winner="A",
            pass_fail={"A": True},
            failure_tags={"C": ["too generic"]},
            rubric_notes={},
            notes=None,
        )


def test_review_decision_rejects_unknown_failure_tags(session: Session) -> None:
    experiment = _completed_experiment(session)
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="unknown-tag-review",
        name="Unknown Tag Review",
        random_seed=1,
    )
    session.flush()

    with pytest.raises(ValueError, match="unknown failure tags: invented tag"):
        record_review_decision(
            session,
            review_item=review_set.items[0],
            reviewer_id="human-reviewer",
            winner="A",
            pass_fail={"A": True, "B": False},
            failure_tags={"B": ["invented tag"]},
            rubric_notes={},
            notes=None,
        )


def test_pairwise_aggregation_inputs_are_score_records_with_attempt_context(
    session: Session,
) -> None:
    experiment = _completed_experiment(session)
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="aggregation-review",
        name="Aggregation Review",
        random_seed=1,
    )
    session.flush()
    record_review_decision(
        session,
        review_item=review_set.items[0],
        reviewer_id="human-reviewer",
        winner="tie",
        pass_fail={"A": True, "B": True},
        failure_tags={},
        rubric_notes={},
        notes="Both acceptable.",
    )
    session.commit()

    inputs = pairwise_aggregation_inputs(session, review_set=review_set)

    assert len(inputs) == 2
    assert {item["score_type"] for item in inputs} == {"pairwise_preference"}
    assert {item["outcome"] for item in inputs} == {"tie"}
    assert all(item["run_attempt_id"] for item in inputs)
    assert all(item["model_config_slug"] for item in inputs)
    assert all(item["system_prompt_slug"] == "system" for item in inputs)
    assert all(item["warmer_slug"] == "warmer" for item in inputs)


def test_revised_review_decision_replaces_prior_scores(session: Session) -> None:
    experiment = _completed_experiment(session)
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="revised-review",
        name="Revised Review",
        random_seed=1,
    )
    session.flush()

    record_review_decision(
        session,
        review_item=review_set.items[0],
        reviewer_id="human-reviewer",
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
        notes="First pass.",
    )
    session.flush()
    record_review_decision(
        session,
        review_item=review_set.items[0],
        reviewer_id="human-reviewer",
        winner="B",
        pass_fail={"A": False, "B": True},
        failure_tags={"A": ["weak risks"]},
        rubric_notes={},
        notes="Revised pass.",
    )
    session.commit()

    scores = session.scalars(select(Score).order_by(Score.id)).all()

    assert [score.type for score in scores] == [
        "pairwise_preference",
        "pairwise_preference",
        "pass_fail",
        "pass_fail",
        "failure_tags",
        "freeform_notes",
        "freeform_notes",
    ]
    assert {score.value["winner"] for score in scores if score.type == "pairwise_preference"} == {
        "B"
    }
    assert [score.value["tags"] for score in scores if score.type == "failure_tags"] == [
        ["weak risks"]
    ]
    assert all("First pass." not in str(score.value) for score in scores)


def _completed_experiment(session: Session, *, replicates: int = 1):
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="review", name="Review")
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": "review_exp",
                "name": "Review experiment",
                "cases": [{"id": "case", "prompt": "case"}],
                "models": [
                    {"id": "model_a", "provider": "openai", "model": "a"},
                    {"id": "model_b", "provider": "anthropic", "model": "b"},
                ],
                "system_prompts": [{"id": "system", "prompt": "system"}],
                "warmers": [{"id": "warmer", "messages": []}],
                "design": {"replicates": replicates},
                "controls": {"local_only": True},
                "evaluation": {"evaluators": []},
            }
        ),
    )
    experiment.status = "complete"
    for index, run in enumerate(sorted(experiment.runs, key=lambda item: item.model_config_slug)):
        run.status = "complete"
        for attempt in sorted(run.attempts, key=lambda item: item.replicate_index):
            attempt.status = "succeeded"
            text = (
                f"answer-{index}"
                if replicates == 1
                else f"answer-{index}-r{attempt.replicate_index}"
            )
            attempt.response_payload = {"output_text": text}
            attempt.cost_usd = 0.01 + index
            attempt.input_tokens = 10 + index
            attempt.output_tokens = 20 + index
    session.commit()
    return experiment
