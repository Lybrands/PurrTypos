"""Application service for explicit source preview, import, and freezing."""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

from domains.novel_sources import (
    NovelSourceConflictError,
    apply_source_section_layout,
    parse_source_sections,
    validate_source_text,
)
from infrastructure.persistence.sqlite_novel_source_repository import (
    SqliteNovelSourceRepository,
)


class NovelSourceService:
    def __init__(self, db) -> None:
        self._repository = SqliteNovelSourceRepository(db)

    def preview_external_import(
        self,
        *,
        file_name: str,
        extension: str,
        content: str,
        import_kind: str = "file",
        document_count: int = 1,
        skipped_file_count: int = 0,
    ) -> dict[str, Any]:
        metadata = validate_source_text(
            file_name=file_name,
            extension=extension,
            content=content,
        )
        sections = parse_source_sections(content)
        return {
            **metadata,
            "importKind": import_kind,
            "documentCount": document_count,
            "skippedFileCount": skipped_file_count,
            "suggestedTitle": _title_from_file(metadata["fileName"]),
            "parserVersion": 1,
            "sectionCount": len(sections),
            "requiresSingleSectionConfirmation": len(sections) == 1,
            "sections": [{
                "ordinal": section.ordinal,
                "title": section.title,
                "characterCount": len(section.text),
                "preview": section.text.strip()[:240],
                "startCharacter": section.start_character,
                "endCharacter": section.end_character,
            } for section in sections],
            "estimatedAdditionalStorageBytes": metadata["byteCount"],
            "rightsNotice": "请只导入你有权使用的作品。",
            "modelDataBoundaryNotice": (
                "确认导入不会发送正文；启动分析后，仅会把当前分析单元所需片段"
                "发送给你配置的外部模型服务。"
            ),
        }

    async def confirm_external_import(
        self,
        *,
        title: str,
        file_name: str,
        extension: str,
        content: str,
        import_kind: str = "file",
        document_count: int = 1,
        skipped_file_count: int = 0,
        expected_content_digest: str,
        confirm_single_section: bool = False,
        rights_confirmed: bool = False,
        model_data_boundary_confirmed: bool = False,
        section_layout: list[dict[str, object]] | None = None,
        work_id: str | None = None,
    ) -> dict[str, Any]:
        preview = self.preview_external_import(
            file_name=file_name,
            extension=extension,
            content=content,
            import_kind=import_kind,
            document_count=document_count,
            skipped_file_count=skipped_file_count,
        )
        if preview["contentDigest"] != str(expected_content_digest or "").strip():
            raise NovelSourceConflictError("来源正文已变化，请重新预览后再确认")
        sections = (
            apply_source_section_layout(content, section_layout)
            if section_layout is not None
            else parse_source_sections(content)
        )
        return await self._repository.create_external_revision(
            title=str(title or "").strip() or preview["suggestedTitle"],
            sections=sections,
            content_digest=preview["contentDigest"],
            byte_count=preview["byteCount"],
            character_count=preview["characterCount"],
            source_metadata={
                "fileName": preview["fileName"],
                "extension": preview["extension"],
                "importKind": preview["importKind"],
                "documentCount": preview["documentCount"],
                "skippedFileCount": preview["skippedFileCount"],
                "rightsConfirmed": rights_confirmed,
                "modelDataBoundaryConfirmed": model_data_boundary_confirmed,
                "sectionLayout": "reviewed" if section_layout is not None else "detected",
            },
            work_id=work_id,
        )

    async def freeze_book(self, book_id: str):
        return await self._repository.freeze_book(book_id)

    async def list_works(self, *, include_archived: bool = False):
        return await self._repository.list_works(include_archived=include_archived)

    async def get_work(self, work_id: str):
        return await self._repository.get_work(work_id)

    async def get_revision(self, revision_id: str):
        return await self._repository.get_revision(revision_id)

    async def get_section(
        self,
        revision_id: str,
        section_id: str,
        *,
        start_character: int = 0,
        character_limit: int | None = None,
    ):
        return await self._repository.get_section(
            revision_id,
            section_id,
            start_character=start_character,
            character_limit=character_limit,
        )

    async def search_sections(self, revision_id: str, query: str, *, limit: int = 12):
        return await self._repository.search_sections(revision_id, query, limit=limit)

    async def archive_work(self, work_id: str):
        return await self._repository.archive_work(work_id)

    async def delete_work(self, work_id: str) -> None:
        await self._repository.delete_work(work_id)

    async def delete_revision(self, revision_id: str) -> None:
        await self._repository.delete_revision(revision_id)


def _title_from_file(file_name: str) -> str:
    name = PurePath(str(file_name or "source").replace("\\", "/")).name
    return name.rsplit(".", 1)[0].strip() or "未命名来源"


__all__ = ["NovelSourceService"]
