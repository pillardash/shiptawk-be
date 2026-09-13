from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.llm.services.llm_execution_service import LLMExecutionService
from app.modules.operator.models import (
    ActionEvent,
    ActionRiskReview,
    ApprovalRequest,
    MarketingPlan,
    OperatorRecommendation,
    Opportunity,
    PlanAction,
    PreparedAsset,
)
from app.modules.operator.policies.ai_output_policy import (
    EvidenceCandidate,
    output_type_for_action_family,
    validate_asset_claims,
    validate_evidence_selection,
    validate_output_compatibility,
)
from app.modules.operator.policies.risk_policy import evaluate_asset_safety, final_risk_outcome
from app.modules.operator.prompts.prepared_asset_prompt import build_prepared_asset_prompt
from app.modules.operator.prompts.recommendation_prompt import build_recommendation_prompt
from app.modules.operator.prompts.risk_review_prompt import build_risk_review_prompt
from app.modules.operator.repositories import ai_output_repository as repository
from app.modules.operator.schemas.ai_output_schema import (
    NoAsset,
    PreparedAssetOutput,
    Recommendation,
    RecommendationOutput,
    RiskReviewOutput,
)
from app.modules.products.models import Product

RECOMMENDATION_ADAPTER: TypeAdapter[RecommendationOutput] = TypeAdapter(RecommendationOutput)
ASSET_ADAPTER: TypeAdapter[PreparedAssetOutput] = TypeAdapter(PreparedAssetOutput)
RISK_ADAPTER: TypeAdapter[RiskReviewOutput] = TypeAdapter(RiskReviewOutput)


def _validator[T: BaseModel](adapter: TypeAdapter[T]) -> Callable[[object], dict[str, object]]:
    def validate(value: object) -> dict[str, object]:
        output = adapter.validate_python(value)
        dumped = output.model_dump(mode="json")
        return {str(key): item for key, item in dumped.items()}

    return validate


class OperatorAIService:
    """Owns operator intent, validation, and canonical persistence around central LLM execution."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        llm: LLMExecutionService,
        *,
        recommendation_route: str = "operator-recommendation",
        asset_routes: dict[str, str] | None = None,
        risk_route: str = "operator-risk-review",
    ) -> None:
        self._sessions = sessions
        self._llm = llm
        self._recommendation_route = recommendation_route
        self._asset_routes = asset_routes or {
            "seo_page_update": "operator-asset-seo",
            "blog_brief": "operator-asset-blog",
            "product_update": "operator-asset-product-update",
            "x_draft": "operator-asset-x",
        }
        self._risk_route = risk_route

    async def generate_recommendation(
        self,
        *,
        workspace_id: UUID,
        product_id: UUID,
        opportunity: Opportunity,
        idempotency_key: str,
        lease_owner: str,
    ) -> OperatorRecommendation:
        self._check_opportunity_scope(opportunity, workspace_id, product_id)
        async with self._sessions() as db:
            existing = await repository.get_recommendation_by_key(
                db, workspace_id, product_id, opportunity.id, idempotency_key
            )
            if existing is not None:
                return existing
            evidence = await repository.list_public_fresh_evidence(
                db, workspace_id, product_id, opportunity.id
            )
        prompt_evidence: list[dict[str, object]] = [
            {
                "id": str(item.id),
                "summary": item.public_safe_summary,
                "observed_at": item.observed_at.isoformat(),
            }
            for item in evidence[:20]
        ]
        action_family = opportunity.action_family or ""
        output_type = output_type_for_action_family(action_family)
        correlation_id = f"{opportunity.operator_run_id}:recommendation:{opportunity.id}"
        result = await self._llm.execute(
            workspace_id=workspace_id,
            product_id=product_id,
            use_case="operator.recommendation.v1",
            idempotency_key=f"recommendation:{opportunity.id}:{idempotency_key}",
            route_name=self._recommendation_route,
            envelope=build_recommendation_prompt(
                opportunity.title,
                str((opportunity.expected_metric or {}).get("name", "engagement")),
                action_family,
                output_type,
                prompt_evidence,
                correlation_id=correlation_id,
            ),
            validate_output=_validator(RECOMMENDATION_ADAPTER),
            sanitized_metadata={"opportunity_id": str(opportunity.id)},
            lease_owner=lease_owner,
        )
        output = RECOMMENDATION_ADAPTER.validate_python(result.validated_output)
        selected_ids = [UUID(value) for value in output.evidence_ids]
        candidates = [
            EvidenceCandidate(
                id=item.id,
                workspace_id=item.workspace_id,
                product_id=item.product_id,
                opportunity_id=item.opportunity_id,
                public_safe_summary=item.public_safe_summary,
                freshness_status=item.freshness_status,
                observed_at=item.observed_at,
            )
            for item in evidence
        ]
        validate_evidence_selection(
            selected_ids, candidates, workspace_id, product_id, opportunity.id
        )
        if isinstance(output, Recommendation):
            validate_output_compatibility(
                output.action_family, output.expected_metric, output.output_type
            )
        recommendation = OperatorRecommendation(
            workspace_id=workspace_id,
            product_id=product_id,
            opportunity_id=opportunity.id,
            operator_run_id=opportunity.operator_run_id,
            llm_execution_id=result.execution_id,
            kind=output.kind,
            output=output.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        async with self._sessions() as db:
            try:
                await repository.add_recommendation(db, recommendation, selected_ids)
                await db.commit()
            except IntegrityError:
                await db.rollback()
                canonical = await repository.get_recommendation_by_key(
                    db, workspace_id, product_id, opportunity.id, idempotency_key
                )
                if canonical is None:
                    raise
                return canonical
        return recommendation

    async def prepare_asset(
        self,
        *,
        recommendation: OperatorRecommendation,
        output_type: str,
        idempotency_key: str,
        lease_owner: str,
        action_id: UUID | None = None,
    ) -> PreparedAsset:
        async with self._sessions() as db:
            existing = await repository.get_asset_by_revision(
                db, recommendation.workspace_id, recommendation.product_id, recommendation.id, 1
            )
            if existing is not None:
                return existing
            evidence = await repository.list_recommendation_evidence(
                db, recommendation.workspace_id, recommendation.product_id, recommendation.id
            )
        result = await self._llm.execute(
            workspace_id=recommendation.workspace_id,
            product_id=recommendation.product_id,
            use_case="operator.prepared_asset.v1",
            idempotency_key=f"asset:{recommendation.id}:1:{idempotency_key}",
            route_name=self._asset_routes[output_type],
            envelope=build_prepared_asset_prompt(
                str(recommendation.output),
                output_type,
                [
                    {"id": str(item.id), "summary": item.public_safe_summary}
                    for item in evidence[:20]
                ],
                correlation_id=(f"{recommendation.operator_run_id}:asset:{recommendation.id}:1"),
            ),
            validate_output=_validator(ASSET_ADAPTER),
            sanitized_metadata={"recommendation_id": str(recommendation.id), "revision": 1},
            lease_owner=lease_owner,
        )
        output = ASSET_ADAPTER.validate_python(result.validated_output)
        if not isinstance(output, NoAsset):
            mappings = {item.claim: item.evidence_ids for item in output.claim_evidence}
            recommendation_claims = recommendation.output.get("claims", [])
            if not isinstance(recommendation_claims, list) or not all(
                isinstance(claim, str) for claim in recommendation_claims
            ):
                raise ValueError("invalid_recommendation_claims")
            validate_asset_claims(
                recommendation_claims,
                mappings,
                {str(item.id) for item in evidence},
                content=output.content.model_dump(mode="json"),
            )
        asset = PreparedAsset(
            workspace_id=recommendation.workspace_id,
            product_id=recommendation.product_id,
            recommendation_id=recommendation.id,
            action_id=action_id,
            llm_execution_id=result.execution_id,
            revision=1,
            kind=output.kind,
            structured_content=output.model_dump(mode="json"),
        )
        async with self._sessions() as db:
            try:
                await repository.add_asset(db, asset)
                await db.commit()
            except IntegrityError:
                await db.rollback()
                canonical = await repository.get_asset_by_revision(
                    db, asset.workspace_id, asset.product_id, recommendation.id, 1
                )
                if canonical is None:
                    raise
                return canonical
        return asset

    async def review_risk(
        self,
        *,
        asset: PreparedAsset,
        deterministic_outcome: str,
        idempotency_key: str,
        lease_owner: str,
    ) -> ActionRiskReview:
        async with self._sessions() as db:
            existing = await repository.get_risk_review(
                db, asset.workspace_id, asset.product_id, asset.id, asset.revision
            )
            if existing is not None:
                return existing
            evidence = await repository.list_recommendation_evidence(
                db, asset.workspace_id, asset.product_id, asset.recommendation_id
            )
        result = await self._llm.execute(
            workspace_id=asset.workspace_id,
            product_id=asset.product_id,
            use_case="operator.risk_review.v1",
            idempotency_key=f"risk:{asset.id}:{asset.revision}:{idempotency_key}",
            route_name=self._risk_route,
            envelope=build_risk_review_prompt(
                asset.kind,
                asset.structured_content,
                [
                    {"id": str(item.id), "summary": item.public_safe_summary}
                    for item in evidence[:20]
                ],
                correlation_id=f"{asset.recommendation_id}:risk:{asset.id}:{asset.revision}",
            ),
            validate_output=_validator(RISK_ADAPTER),
            sanitized_metadata={"asset_id": str(asset.id), "revision": asset.revision},
            lease_owner=lease_owner,
        )
        output = RISK_ADAPTER.validate_python(result.validated_output)
        review = ActionRiskReview(
            workspace_id=asset.workspace_id,
            product_id=asset.product_id,
            asset_id=asset.id,
            asset_revision=asset.revision,
            llm_execution_id=result.execution_id,
            deterministic_outcome=deterministic_outcome,
            ai_outcome=output.outcome,
            final_outcome=final_risk_outcome(deterministic_outcome, output.outcome),
            findings=[item.model_dump(mode="json") for item in output.findings],
        )
        async with self._sessions() as db:
            try:
                await repository.add_risk_review(db, review)
                await db.commit()
            except IntegrityError:
                await db.rollback()
                canonical = await repository.get_risk_review(
                    db, asset.workspace_id, asset.product_id, asset.id, asset.revision
                )
                if canonical is None:
                    raise
                return canonical
        return review

    async def process_accepted_recommendation(
        self,
        *,
        recommendation: OperatorRecommendation,
        output_type: str,
        action_id: UUID,
        idempotency_key: str,
        lease_owner: str,
    ) -> tuple[PreparedAsset, ActionRiskReview | None]:
        if recommendation.accepted_at is None or recommendation.kind != "recommendation":
            raise ValueError("accepted_recommendation_required")
        asset = await self.prepare_asset(
            recommendation=recommendation,
            output_type=output_type,
            action_id=action_id,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner,
        )
        if asset.kind == "no_asset":
            async with self._sessions() as db:
                action = await db.scalar(
                    select(PlanAction).where(
                        PlanAction.workspace_id == asset.workspace_id,
                        PlanAction.product_id == asset.product_id,
                        PlanAction.id == action_id,
                    )
                )
                if action is None:
                    raise ValueError("action_tenant_mismatch")
                actor_id = await db.scalar(
                    select(MarketingPlan.created_by_actor_id).where(
                        MarketingPlan.workspace_id == asset.workspace_id,
                        MarketingPlan.id == action.plan_id,
                    )
                )
                if actor_id is None:
                    raise ValueError("action_actor_missing")
                reason = str(asset.structured_content.get("reason", "no_asset"))
                previous = action.status
                action.status = "dismissed"
                action.approval_status = "not_required"
                action.dismissed_by_actor_id = actor_id
                action.dismissed_at = datetime.now(UTC)
                action.dismissal_reason = reason
                db.add(
                    ActionEvent(
                        workspace_id=asset.workspace_id,
                        product_id=asset.product_id,
                        action_id=action.id,
                        event_type="no_asset",
                        previous_status=previous,
                        new_status="dismissed",
                        actor_id=actor_id,
                        reason=reason[:500],
                        details={"assetId": str(asset.id), "revision": asset.revision},
                    )
                )
                await db.commit()
            return asset, None
        async with self._sessions() as db:
            product = await db.scalar(
                select(Product).where(
                    Product.workspace_id == asset.workspace_id,
                    Product.id == asset.product_id,
                )
            )
            if product is None:
                raise ValueError("product_tenant_mismatch")
            deterministic_risk_outcome = evaluate_asset_safety(
                asset.structured_content,
                product.blocked_terms,
                product.blocked_topics,
                product.safe_public_boundaries,
            )
        review = await self.review_risk(
            asset=asset,
            deterministic_outcome=deterministic_risk_outcome,
            idempotency_key=idempotency_key,
            lease_owner=lease_owner,
        )
        async with self._sessions() as db:
            action = await db.scalar(
                select(PlanAction).where(
                    PlanAction.workspace_id == asset.workspace_id,
                    PlanAction.product_id == asset.product_id,
                    PlanAction.id == action_id,
                )
            )
            if action is None:
                raise ValueError("action_tenant_mismatch")
            approval = await db.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.workspace_id == asset.workspace_id,
                    ApprovalRequest.action_id == action_id,
                )
            )
            if approval is None and review.final_outcome != "block" and asset.kind != "no_asset":
                requester = await db.scalar(
                    select(MarketingPlan.created_by_actor_id).where(
                        MarketingPlan.workspace_id == asset.workspace_id,
                        MarketingPlan.id == action.plan_id,
                    )
                )
                if requester is None:
                    raise ValueError("approval_requester_missing")
                db.add(
                    ApprovalRequest(
                        workspace_id=asset.workspace_id,
                        product_id=asset.product_id,
                        action_id=action.id,
                        asset_id=asset.id,
                        asset_revision=asset.revision,
                        status="pending",
                        requested_by_actor_id=requester,
                        provenance={
                            "source": "operator-risk-review",
                            "review_id": str(review.id),
                        },
                    )
                )
            action.approval_status = "rejected" if review.final_outcome == "block" else "pending"
            action.status = "dismissed" if review.final_outcome == "block" else "pending"
            await db.commit()
        return asset, review

    @staticmethod
    def _check_opportunity_scope(
        opportunity: Opportunity, workspace_id: UUID, product_id: UUID
    ) -> None:
        if opportunity.workspace_id != workspace_id or opportunity.product_id != product_id:
            raise ValueError("opportunity_tenant_mismatch")
        if opportunity.operator_run_id is None:
            raise ValueError("opportunity_operator_run_required")
