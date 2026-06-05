from __future__ import annotations

from collections.abc import Generator
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from model_eval_api import main as api_module
from model_eval_api.manifest import parse_manifest
from model_eval_api.persistence.models import Base, ReviewAssignment, Score
from model_eval_api.persistence.repositories import (
    create_experiment_from_manifest,
    create_failure_taxonomy,
    create_project,
    create_review_assignments,
    create_review_set_from_completed_experiment,
    create_reviewer,
    create_workspace,
    get_reviewer_queue,
    record_assignment_decision,
)
from model_eval_api.results_analytics import aggregate_experiment_results


def test_multi_reviewer_assignments_preserve_blind_queues_and_taxonomy_snapshots(
    session: Session,
) -> None:
    experiment = _completed_experiment(session)
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    create_reviewer(session, project=experiment.project, slug="bob", name="Bob")
    taxonomy = create_failure_taxonomy(
        session,
        project=experiment.project,
        slug="memo-taxonomy",
        name="Memo taxonomy",
        tags=["too generic", "weak risks"],
        version=2,
    )

    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="phase-16-review",
        name="Phase 16 review",
        random_seed=1,
        reviewer_slugs=["alice", "bob"],
        failure_taxonomy_slug=taxonomy.slug,
    )
    session.commit()

    assert review_set.metadata_json["failure_tags"] == ["too generic", "weak risks"]
    assert review_set.metadata_json["failure_taxonomy"] == {
        "slug": "memo-taxonomy",
        "name": "Memo taxonomy",
        "version": 2,
        "tags": ["too generic", "weak risks"],
    }

    assignments = session.scalars(
        select(ReviewAssignment).order_by(ReviewAssignment.reviewer_id)
    ).all()
    assert len(assignments) == 2
    assert {assignment.status for assignment in assignments} == {"pending"}
    assert {assignment.taxonomy_snapshot["version"] for assignment in assignments} == {2}

    alice_queue = get_reviewer_queue(session, review_set=review_set, reviewer_slug="alice")
    encoded_queue = json.dumps(alice_queue, sort_keys=True)
    assert alice_queue["progress"] == {"assigned": 1, "submitted": 0, "pending": 1}
    assert alice_queue["items"][0]["assignment_id"]
    assert "run_attempt_id" not in encoded_queue
    assert "model_config_slug" not in encoded_queue

    alice_assignment = next(
        assignment for assignment in assignments if assignment.reviewer.slug == "alice"
    )
    bob_assignment = next(assignment for assignment in assignments if assignment.reviewer.slug == "bob")
    record_assignment_decision(
        session,
        assignment=alice_assignment,
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
        confidence=0.8,
    )
    record_assignment_decision(
        session,
        assignment=bob_assignment,
        winner="B",
        pass_fail={"A": False, "B": True},
        failure_tags={"A": ["weak risks"]},
        rubric_notes={},
        confidence=0.7,
    )
    session.commit()

    assert alice_assignment.status == "submitted"
    assert bob_assignment.status == "submitted"
    assert alice_assignment.decision_snapshot["winner"] == "A"
    assert bob_assignment.decision_snapshot["winner"] == "B"

    scores = session.scalars(select(Score).order_by(Score.id)).all()
    assert {score.value.get("reviewer_id") for score in scores} == {"alice", "bob"}
    assert {score.value.get("assignment_id") for score in scores if score.type == "pass_fail"} == {
        alice_assignment.id,
        bob_assignment.id,
    }

    analytics = aggregate_experiment_results(session, experiment_id=experiment.id)
    assert analytics["reviewer_coverage"][0]["coverage_rate"] == 1.0
    assert analytics["reviewer_coverage"][0]["reviewer_count"] == 2
    assert analytics["reviewer_disagreement"][0]["pairwise_disagreement"] is True
    assert analytics["reviewer_disagreement"][0]["pass_fail_disagreement_count"] == 2
    taxonomy_tags = {row["tag"]: row for row in analytics["failure_taxonomy_rollup"]}
    assert taxonomy_tags["too generic"]["taxonomy_version"] == 2
    assert taxonomy_tags["weak risks"]["count"] == 1


def test_review_assignment_api_uses_reviewer_identity_and_taxonomy_queue(
    session: Session,
) -> None:
    experiment = _completed_experiment(session)

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        reviewer = client.post(
            "/projects/review/reviewers",
            json={"slug": "alice", "name": "Alice"},
        )
        assert reviewer.status_code == 201
        taxonomy = client.post(
            "/projects/review/failure-taxonomies",
            json={
                "slug": "memo-taxonomy",
                "name": "Memo taxonomy",
                "tags": ["too generic"],
                "version": 1,
            },
        )
        assert taxonomy.status_code == 201
        duplicate_taxonomy = client.post(
            "/projects/review/failure-taxonomies",
            json={
                "slug": "memo-taxonomy",
                "name": "Mutated memo taxonomy",
                "tags": ["changed"],
                "version": 1,
            },
        )
        assert duplicate_taxonomy.status_code == 409
        created = client.post(
            f"/projects/review/experiments/{experiment.id}/review-sets",
            json={
                "slug": "api-phase-16-review",
                "name": "API Phase 16 Review",
                "random_seed": 1,
                "reviewer_slugs": ["alice"],
                "failure_taxonomy_slug": "memo-taxonomy",
            },
        )
        assert created.status_code == 201
        review_set_id = created.json()["id"]
        assert created.json()["assignment_progress"] == {
            "assigned": 1,
            "submitted": 0,
            "pending": 1,
        }

        queue = client.get(f"/review-sets/{review_set_id}/reviewers/alice/queue")
        assert queue.status_code == 200
        assignment_id = queue.json()["items"][0]["assignment_id"]
        assert queue.json()["failure_taxonomy"]["tags"] == ["too generic"]
        assert "run_attempt_id" not in json.dumps(queue.json())

        submitted = client.post(
            f"/review-assignments/{assignment_id}/decision",
            json={
                "winner": "A",
                "pass_fail": {"A": True, "B": False},
                "failure_tags": {"B": ["too generic"]},
                "rubric_notes": {},
            },
        )
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "submitted"
        assert submitted.json()["reviewer"]["slug"] == "alice"
        assert submitted.json()["decision_snapshot"]["winner"] == "A"

        generic_review_set = client.get(f"/review-sets/{review_set_id}")
        assert generic_review_set.status_code == 200
        assert "decision_snapshot" not in generic_review_set.json()["assignments"][0]
        assert generic_review_set.json()["items"][0]["reviewer_decision"] == {}

        reviewer_queue = client.get(f"/review-sets/{review_set_id}/reviewers/alice/queue")
        assert reviewer_queue.status_code == 200
        assert reviewer_queue.json()["items"][0]["reviewer_decision"]["winner"] == "A"
    finally:
        api_module.app.dependency_overrides.clear()


def test_review_set_payload_eager_loads_assignment_reviewers(session: Session) -> None:
    experiment = _completed_experiment(session)
    reviewer_slugs = ["alice", "bob", "chris"]
    for reviewer_slug in reviewer_slugs:
        create_reviewer(
            session,
            project=experiment.project,
            slug=reviewer_slug,
            name=reviewer_slug.title(),
        )
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="eager-review-set",
        name="Eager Review Set",
        random_seed=1,
        reviewer_slugs=reviewer_slugs,
    )
    session.commit()
    session.expire_all()

    statements: list[str] = []

    def capture_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)

    event.listen(session.bind, "before_cursor_execute", capture_selects)

    def override_session() -> Generator[Session, None, None]:
        yield session

    api_module.app.dependency_overrides[api_module.get_session] = override_session
    try:
        client = TestClient(api_module.app)
        response = client.get(f"/review-sets/{review_set.id}")
    finally:
        api_module.app.dependency_overrides.clear()
        event.remove(session.bind, "before_cursor_execute", capture_selects)

    assert response.status_code == 200
    assert response.json()["assignment_progress"] == {
        "assigned": len(reviewer_slugs),
        "submitted": 0,
        "pending": len(reviewer_slugs),
    }
    reviewer_selects = [
        statement for statement in statements if "from reviewers" in statement.lower()
    ]
    assert len(reviewer_selects) <= 1


def test_assignment_decision_rejects_unknown_failure_tags(session: Session) -> None:
    experiment = _completed_experiment(session)
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    create_failure_taxonomy(
        session,
        project=experiment.project,
        slug="strict-taxonomy",
        name="Strict taxonomy",
        tags=["too generic"],
        version=1,
    )
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="strict-phase-16-review",
        name="Strict Phase 16 Review",
        random_seed=1,
        reviewer_slugs=["alice"],
        failure_taxonomy_slug="strict-taxonomy",
    )
    session.flush()

    with pytest.raises(ValueError, match="unknown failure tags: invented tag"):
        record_assignment_decision(
            session,
            assignment=review_set.assignments[0],
            winner="A",
            pass_fail={"A": True, "B": False},
            failure_tags={"B": ["invented tag"]},
            rubric_notes={},
        )


def test_assignments_for_legacy_review_sets_use_failure_tag_metadata(
    session: Session,
) -> None:
    experiment = _completed_experiment(session)
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="legacy-phase-16-review",
        name="Legacy Phase 16 Review",
        random_seed=1,
    )
    metadata = dict(review_set.metadata_json or {})
    metadata.pop("failure_taxonomy", None)
    metadata["failure_tags"] = ["too generic"]
    review_set.metadata_json = metadata
    session.flush()

    assignments = create_review_assignments(
        session,
        review_set=review_set,
        reviewer_slugs=["alice"],
    )
    assert assignments[0].taxonomy_snapshot == {"tags": ["too generic"]}

    record_assignment_decision(
        session,
        assignment=assignments[0],
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
    )
    session.commit()

    assert assignments[0].status == "submitted"
    assert assignments[0].decision_snapshot["failure_tags"] == {"B": ["too generic"]}


def test_assignment_decision_stamps_scores_when_autoflush_is_disabled(
    session: Session,
) -> None:
    session.autoflush = False
    experiment = _completed_experiment(session)
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="autoflush-phase-16-review",
        name="Autoflush Phase 16 Review",
        random_seed=1,
        reviewer_slugs=["alice"],
    )
    session.flush()
    assignment = review_set.assignments[0]

    record_assignment_decision(
        session,
        assignment=assignment,
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
    )
    session.commit()

    scores = session.scalars(select(Score).order_by(Score.id)).all()
    assert scores
    assert {score.value.get("assignment_id") for score in scores} == {assignment.id}
    assert {score.value.get("taxonomy_version") for score in scores} == {1}


def test_create_reviewer_preserves_existing_profile(session: Session) -> None:
    experiment = _completed_experiment(session)
    reviewer = create_reviewer(
        session,
        project=experiment.project,
        slug="alice",
        name="Alice Smith",
        email="alice@example.com",
    )
    session.flush()

    same_reviewer = create_reviewer(
        session,
        project=experiment.project,
        slug="alice",
        name="alice",
        email=None,
    )
    session.commit()

    assert same_reviewer.id == reviewer.id
    assert reviewer.name == "Alice Smith"
    assert reviewer.email == "alice@example.com"


def test_revised_assignment_decision_replaces_prior_scores(session: Session) -> None:
    experiment = _completed_experiment(session)
    create_reviewer(session, project=experiment.project, slug="alice", name="Alice")
    review_set = create_review_set_from_completed_experiment(
        session,
        project=experiment.project,
        experiment=experiment,
        slug="revised-phase-16-review",
        name="Revised Phase 16 Review",
        random_seed=1,
        reviewer_slugs=["alice"],
    )
    session.flush()
    assignment = review_set.assignments[0]

    record_assignment_decision(
        session,
        assignment=assignment,
        winner="A",
        pass_fail={"A": True, "B": False},
        failure_tags={"B": ["too generic"]},
        rubric_notes={},
    )
    session.flush()
    assert len(session.scalars(select(Score)).all()) == 5

    record_assignment_decision(
        session,
        assignment=assignment,
        winner="tie",
        pass_fail={"A": True, "B": True},
        failure_tags={},
        rubric_notes={},
    )
    session.commit()

    scores = session.scalars(select(Score).order_by(Score.id)).all()
    assert len(scores) == 4
    assert {score.value["assignment_id"] for score in scores} == {assignment.id}
    assert {score.value.get("winner") for score in scores if score.type == "pairwise_preference"} == {
        "tie"
    }
    assert {score.type for score in scores} == {"pairwise_preference", "pass_fail"}
    assert assignment.status == "submitted"
    assert assignment.decision_snapshot["winner"] == "tie"


def _completed_experiment(session: Session):
    workspace = create_workspace(session, slug="default", name="Default")
    project = create_project(session, workspace=workspace, slug="review", name="Review")
    experiment = create_experiment_from_manifest(
        session,
        project=project,
        manifest=parse_manifest(
            {
                "id": "phase16_review_exp",
                "name": "Phase 16 Review Experiment",
                "cases": [{"id": "case", "prompt": "case"}],
                "models": [
                    {"id": "model_a", "provider": "openai", "model": "a"},
                    {"id": "model_b", "provider": "anthropic", "model": "b"},
                ],
                "system_prompts": [{"id": "system", "prompt": "system"}],
                "warmers": [{"id": "warmer", "messages": []}],
                "design": {"replicates": 1},
                "controls": {"local_only": True},
                "evaluation": {"evaluators": []},
            }
        ),
    )
    experiment.status = "complete"
    for index, run in enumerate(sorted(experiment.runs, key=lambda item: item.model_config_slug)):
        run.status = "complete"
        for attempt in run.attempts:
            attempt.status = "succeeded"
            attempt.response_payload = {"output_text": f"answer-{index}"}
    session.commit()
    return experiment


def _session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    with _session_factory()() as db:
        yield db
