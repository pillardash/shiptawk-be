from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.operator.services.validation_service import (
    approved_evidence,
    validate_evidence_ids,
    validate_product,
)
from app.shared.exceptions import ConflictError, NotFoundError


@pytest.mark.asyncio
async def test_validate_product_accepts_global_scope_and_existing_product() -> None:
    db = AsyncMock()
    await validate_product(db, uuid4(), None)
    db.scalar.assert_not_awaited()

    db.scalar.return_value = uuid4()
    await validate_product(db, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_validate_product_rejects_missing_tenant_product() -> None:
    db = AsyncMock()
    db.scalar.return_value = None
    with pytest.raises(NotFoundError):
        await validate_product(db, uuid4(), uuid4())


@pytest.mark.asyncio
async def test_approved_evidence_returns_workspace_rows_with_optional_product_scope() -> None:
    evidence = [object(), object()]
    db = AsyncMock()
    db.scalars.return_value = evidence
    assert await approved_evidence(db, uuid4(), None) == evidence
    assert await approved_evidence(db, uuid4(), uuid4()) == evidence


@pytest.mark.asyncio
async def test_validate_evidence_ids_accepts_exact_set_and_rejects_missing_rows() -> None:
    evidence_ids = [uuid4(), uuid4()]
    db = AsyncMock()
    db.scalars.return_value = evidence_ids
    await validate_evidence_ids(db, uuid4(), evidence_ids)

    db.scalars.return_value = evidence_ids[:1]
    with pytest.raises(ConflictError):
        await validate_evidence_ids(db, uuid4(), evidence_ids)
