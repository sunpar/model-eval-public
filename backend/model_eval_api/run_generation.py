from model_eval_api.schemas import CountManifestPreviewResponse, ManifestPreviewRequest


def estimate_full_factorial_runs(request: ManifestPreviewRequest) -> CountManifestPreviewResponse:
    """Estimate logical run and attempt counts for the initial full-factorial design."""

    replicates = max(request.replicates, 1)
    logical_runs = (
        max(request.case_count, 0)
        * max(request.model_count, 0)
        * max(request.system_prompt_count, 0)
        * max(request.warmer_count, 0)
    )
    return CountManifestPreviewResponse(
        design_type=request.design_type,
        logical_runs=logical_runs,
        run_attempts=logical_runs * replicates,
        replicates=replicates,
    )
