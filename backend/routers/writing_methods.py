"""HTTP API for the writing method library and book bindings."""

from __future__ import annotations

from fastapi import APIRouter, Query

from application.writing_method_service import WritingMethodService
from application.writing_method_candidates import WritingMethodCandidateService
from dependencies import get_db
from domains.writing.methods import WritingMethodError
from exceptions import AppError
from schemas.writing_methods import (
    BatchPublishRequest,
    CreateAnalysisCandidatesRequest,
    CreateBookWritingMethodBindingRequest,
    MethodDraftRequest,
    ReorderBookWritingMethodBindingsRequest,
    SchemeDraftRequest,
    StatusRequest,
    UpdateMethodDraftRequest,
    UpdateSchemeDraftRequest,
    UpgradeBookWritingMethodBindingRequest,
    PublishAnalysisCandidatesRequest,
)

router = APIRouter(tags=["writing-methods"])


def _service() -> WritingMethodService:
    return WritingMethodService(get_db())


def _candidates() -> WritingMethodCandidateService:
    return WritingMethodCandidateService(get_db())


async def _call(awaitable):
    try:
        return await awaitable
    except WritingMethodError as error:
        raise AppError(str(error), error.status_code) from error


def _ok(data=None):
    return {"success": True, "data": data}


@router.get("/writing-methods")
async def list_methods(includeArchived: bool = Query(default=False)):
    return _ok(await _call(_service().list_methods(include_archived=includeArchived)))


@router.post("/writing-methods")
async def create_method(body: MethodDraftRequest):
    return _ok(await _call(_service().create_method(
        name=body.name, description=body.description, method_type=body.methodType,
        tags=body.tags, markdown=body.markdown, metadata=body.metadata,
    )))


@router.post("/writing-methods/publish-batch")
async def publish_batch(body: BatchPublishRequest):
    return _ok(await _call(_service().publish_batch(
        method_ids=body.methodIds, scheme_ids=body.schemeIds,
    )))


@router.post("/novel-analyses/{analysis_id}/writing-method-candidates")
async def create_analysis_candidates(
    analysis_id: str, body: CreateAnalysisCandidatesRequest
):
    return _ok(await _call(_candidates().create_from_analysis(
        analysis_id, craft_card_ids=body.craftCardIds,
    )))


@router.get("/writing-method-candidate-batches/{scheme_id}")
async def get_analysis_candidates(scheme_id: str):
    return _ok(await _call(_candidates().get_batch(scheme_id)))


@router.post("/writing-method-candidate-batches/{scheme_id}/publish")
async def publish_analysis_candidates(
    scheme_id: str, body: PublishAnalysisCandidatesRequest
):
    return _ok(await _call(_candidates().publish_batch(
        scheme_id, method_ids=body.methodIds,
    )))


@router.get("/writing-methods/{method_id}")
async def get_method(method_id: str):
    return _ok(await _call(_service().get_method(method_id)))


@router.put("/writing-methods/{method_id}/draft")
async def update_method(method_id: str, body: UpdateMethodDraftRequest):
    return _ok(await _call(_service().update_method(
        method_id, expected_draft_revision=body.expectedDraftRevision,
        name=body.name, description=body.description, method_type=body.methodType,
        tags=body.tags, markdown=body.markdown, metadata=body.metadata,
    )))


@router.post("/writing-methods/{method_id}/publish")
async def publish_method(method_id: str):
    return _ok(await _call(_service().publish_method(method_id)))


@router.post("/writing-methods/{method_id}/copy")
async def copy_method(method_id: str):
    return _ok(await _call(_service().copy_method(method_id)))


@router.put("/writing-methods/{method_id}/status")
async def set_method_status(method_id: str, body: StatusRequest):
    return _ok(await _call(_service().set_method_status(method_id, body.status)))


@router.delete("/writing-methods/{method_id}")
async def delete_method(method_id: str):
    await _call(_service().delete_method(method_id))
    return _ok()


@router.get("/writing-schemes")
async def list_schemes(includeArchived: bool = Query(default=False)):
    return _ok(await _call(_service().list_schemes(include_archived=includeArchived)))


@router.post("/writing-schemes")
async def create_scheme(body: SchemeDraftRequest):
    return _ok(await _call(_service().create_scheme(
        name=body.name, description=body.description,
        member_revision_ids=body.memberRevisionIds,
    )))


@router.get("/writing-schemes/{scheme_id}")
async def get_scheme(scheme_id: str):
    return _ok(await _call(_service().get_scheme(scheme_id)))


@router.put("/writing-schemes/{scheme_id}/draft")
async def update_scheme(scheme_id: str, body: UpdateSchemeDraftRequest):
    return _ok(await _call(_service().update_scheme(
        scheme_id, expected_draft_revision=body.expectedDraftRevision,
        name=body.name, description=body.description,
        member_revision_ids=body.memberRevisionIds,
    )))


@router.post("/writing-schemes/{scheme_id}/publish")
async def publish_scheme(scheme_id: str):
    return _ok(await _call(_service().publish_scheme(scheme_id)))


@router.post("/writing-schemes/{scheme_id}/copy")
async def copy_scheme(scheme_id: str):
    return _ok(await _call(_service().copy_scheme(scheme_id)))


@router.put("/writing-schemes/{scheme_id}/status")
async def set_scheme_status(scheme_id: str, body: StatusRequest):
    return _ok(await _call(_service().set_scheme_status(scheme_id, body.status)))


@router.delete("/writing-schemes/{scheme_id}")
async def delete_scheme(scheme_id: str):
    await _call(_service().delete_scheme(scheme_id))
    return _ok()


@router.get("/books/{book_id}/writing-method-bindings")
async def list_book_bindings(book_id: str):
    return _ok(await _call(_service().list_book_bindings(book_id)))


@router.post("/books/{book_id}/writing-method-bindings")
async def bind_book_revision(book_id: str, body: CreateBookWritingMethodBindingRequest):
    return _ok(await _call(_service().bind_book_revision(
        book_id=book_id, binding_type=body.bindingType, revision_id=body.revisionId,
    )))


@router.put("/books/{book_id}/writing-method-bindings/reorder")
async def reorder_book_bindings(book_id: str, body: ReorderBookWritingMethodBindingsRequest):
    return _ok(await _call(_service().reorder_book_bindings(book_id, body.bindingIds)))


@router.put("/books/{book_id}/writing-method-bindings/{binding_id}/upgrade")
async def upgrade_book_binding(
    book_id: str, binding_id: str, body: UpgradeBookWritingMethodBindingRequest
):
    return _ok(await _call(_service().upgrade_book_binding(
        book_id, binding_id, body.revisionId,
    )))


@router.delete("/books/{book_id}/writing-method-bindings/{binding_id}")
async def unbind_book_revision(book_id: str, binding_id: str):
    await _call(_service().unbind_book_revision(book_id, binding_id))
    return _ok()
