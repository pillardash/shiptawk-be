from fastapi.testclient import TestClient

from app.main import create_app


def test_product_scoped_operator_api_exposes_phase_five_resources() -> None:
    app = create_app()
    paths = app.openapi()["paths"]
    prefix = "/api/v1/workspaces/{workspace_id}/products/{product_id}"
    assert set(paths[f"{prefix}/operator/runs"]) == {"get", "post"}
    assert set(paths[f"{prefix}/operator/runs/{{run_id}}/evaluations"]) == {"get"}
    assert set(paths[f"{prefix}/opportunities"]) == {"get"}
    assert set(paths[f"{prefix}/opportunities/{{opportunity_id}}/evidence"]) == {"get"}
    assert set(paths[f"{prefix}/opportunities/{{opportunity_id}}/dismiss"]) == {"post"}
    assert set(paths[f"{prefix}/opportunities/{{opportunity_id}}/feedback"]) == {"post"}

    assert f"{prefix}/operator/today" in paths
    assert f"{prefix}/operator/plans" in paths
    assert f"{prefix}/operator/actions" in paths
    assert f"{prefix}/operator/assets/{{asset_id}}" in paths
    assert "/api/v1/workspaces/{workspace_id}/operator/runs" not in paths
    assert "/api/v1/workspaces/{workspace_id}/opportunities" not in paths


def test_removed_operator_routes_return_not_found() -> None:
    with TestClient(create_app()) as client:
        assert (
            client.get(
                "/api/v1/workspaces/00000000-0000-0000-0000-000000000000/operator/runs"
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/v1/workspaces/00000000-0000-0000-0000-000000000000/opportunities"
            ).status_code
            == 404
        )


def test_public_operator_schemas_exclude_internal_detection_data() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    serialized = str(
        {
            name: schema
            for name, schema in schemas.items()
            if name.startswith(("OperatorRun", "Opportunity"))
        }
    )
    for private_name in (
        "candidateSnapshot",
        "diagnostics",
        "inputFingerprint",
        "inputPayload",
        "provenance",
        "cooldownDedupKey",
    ):
        assert private_name not in serialized
