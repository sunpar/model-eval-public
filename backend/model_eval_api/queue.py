from __future__ import annotations

import os
from typing import Any

from model_eval_api.deterministic_evaluators import run_deterministic_evaluators
from model_eval_api.execution_states import ExperimentStatus
from model_eval_api.executor import execute_experiment, execute_run
from model_eval_api.persistence.database import get_session_factory
from model_eval_api.persistence.models import Experiment


DEFAULT_QUEUE_NAME = "model-eval"


def get_queue(name: str = DEFAULT_QUEUE_NAME) -> Any:
    from redis import Redis
    from rq import Queue

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return Queue(name, connection=Redis.from_url(redis_url))


def enqueue_experiment_expansion(experiment_id: int, *, queue: Any | None = None) -> Any:
    return (queue or get_queue()).enqueue(expand_experiment_job, experiment_id)


def enqueue_run_execution(run_id: int, *, queue: Any | None = None) -> Any:
    return (queue or get_queue()).enqueue(execute_run_job, run_id)


def enqueue_deterministic_evaluators(
    experiment_id: int, *, queue: Any | None = None, depends_on: Any | None = None
) -> Any:
    enqueue_kwargs = {"depends_on": depends_on} if depends_on is not None else {}
    return (queue or get_queue()).enqueue(
        run_deterministic_evaluators_job, experiment_id, **enqueue_kwargs
    )


def enqueue_export_generation(experiment_id: int, *, queue: Any | None = None) -> Any:
    return (queue or get_queue()).enqueue(generate_export_job, experiment_id)


def enqueue_experiment_execution(experiment_id: int, *, queue: Any | None = None) -> list[Any]:
    target_queue = queue or get_queue()
    expansion_job = enqueue_experiment_expansion(experiment_id, queue=target_queue)
    execution_job = target_queue.enqueue(execute_experiment_job, experiment_id, depends_on=expansion_job)
    evaluator_job = enqueue_deterministic_evaluators(
        experiment_id, queue=target_queue, depends_on=execution_job
    )
    return [
        expansion_job,
        execution_job,
        evaluator_job,
        target_queue.enqueue(generate_export_job, experiment_id, depends_on=evaluator_job),
    ]


def expand_experiment_job(experiment_id: int) -> dict[str, Any]:
    return {"job": "experiment_expansion", "experiment_id": experiment_id, "status": "noop"}


def execute_experiment_job(experiment_id: int) -> dict[str, Any]:
    session_factory = get_session_factory()
    with session_factory() as session:
        experiment = execute_experiment(session, experiment_id)
        session.commit()
        return {
            "job": "experiment_execution",
            "experiment_id": experiment.id,
            "status": experiment.status,
        }


def execute_run_job(run_id: int) -> dict[str, Any]:
    session_factory = get_session_factory()
    with session_factory() as session:
        run = execute_run(session, run_id)
        session.commit()
        return {"job": "run_execution", "run_id": run.id, "status": run.status}


def run_deterministic_evaluators_job(experiment_id: int) -> dict[str, Any]:
    session_factory = get_session_factory()
    with session_factory() as session:
        experiment = session.get(Experiment, experiment_id)
        if experiment is None:
            raise ValueError(f"Experiment {experiment_id} was not found.")
        if experiment.status != ExperimentStatus.COMPLETE.value:
            return {
                "job": "deterministic_evaluators",
                "experiment_id": experiment.id,
                "attempts_evaluated": 0,
                "scores_recorded": 0,
                "status": experiment.status,
            }
        result = run_deterministic_evaluators(session, experiment_id)
        session.commit()
        return {"job": "deterministic_evaluators", **result, "status": "complete"}


def generate_export_job(experiment_id: int) -> dict[str, Any]:
    return {"job": "export_generation", "experiment_id": experiment_id, "status": "pending"}
