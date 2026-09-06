"""Complete, credential-free backup archives for host and memory data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import sqlite3
import tempfile
from typing import Any, Mapping
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo


BACKUP_FORMAT = "purrtypos.full-backup/v1"
BACKUP_EXTENSION = ".purrbackup"
MAX_ARCHIVE_ENTRIES = 100_000
MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
_MANIFEST_NAME = "backup-manifest.json"
_DATABASE_NAME = "purrtypos.db"
_MEMORY_PREFIX = "memory-component-v1/"


class ProjectBackupError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ExtractedProjectBackup:
    database_path: Path
    component_path: Path | None
    manifest: Mapping[str, Any]


def build_project_backup(database_bytes: bytes, component_root: Path) -> bytes:
    database = _redact_database_credentials(database_bytes)
    with tempfile.TemporaryDirectory(prefix="purrtypos-backup-build-") as raw:
        database_path = Path(raw) / _DATABASE_NAME
        database_path.write_bytes(database)
        _validate_database(database_path)
        files: dict[str, bytes] = {_DATABASE_NAME: database}
        component_present = component_root.is_dir()
        if component_present:
            for path in sorted(component_root.rglob("*")):
                if path.is_symlink():
                    raise ProjectBackupError("backup_component_symlink_unsupported")
                if not path.is_file():
                    continue
                relative = path.relative_to(component_root).as_posix()
                files[f"{_MEMORY_PREFIX}{relative}"] = path.read_bytes()
        component_dimensions = _validate_component_files(files, component_present)
        database_dimensions = _database_embedding_dimensions(database_path)
        if component_present and database_dimensions != component_dimensions:
            raise ProjectBackupError("backup_component_configuration_mismatch")

    file_manifest = {
        name: {"size": len(content), "sha256": _sha256(content)}
        for name, content in files.items()
    }
    manifest = {
        "format": BACKUP_FORMAT,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "componentPresent": component_present,
        "embeddingDimensions": component_dimensions,
        "credentialsIncluded": False,
        "files": file_manifest,
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(
            _MANIFEST_NAME,
            json.dumps(manifest, ensure_ascii=False, sort_keys=True),
        )
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def extract_project_backup(
    payload: bytes,
    destination: Path,
) -> ExtractedProjectBackup:
    if not payload:
        raise ProjectBackupError("backup_empty")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        archive = ZipFile(BytesIO(payload), "r")
    except BadZipFile as error:
        raise ProjectBackupError("backup_archive_invalid") from error
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ProjectBackupError("backup_too_many_files")
        names: list[str] = []
        total = 0
        for info in infos:
            name = _safe_archive_name(info)
            if name in names:
                raise ProjectBackupError("backup_duplicate_file")
            names.append(name)
            total += int(info.file_size)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise ProjectBackupError("backup_uncompressed_size_exceeded")
        if _MANIFEST_NAME not in names:
            raise ProjectBackupError("backup_manifest_missing")
        try:
            manifest = json.loads(archive.read(_MANIFEST_NAME))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProjectBackupError("backup_manifest_invalid") from error
        expected = _validate_backup_manifest(manifest)
        actual = set(names) - {_MANIFEST_NAME}
        if actual != set(expected):
            raise ProjectBackupError("backup_file_set_mismatch")
        for name, metadata in expected.items():
            content = archive.read(name)
            if len(content) != metadata["size"] or _sha256(content) != metadata["sha256"]:
                raise ProjectBackupError("backup_file_integrity_failed")
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    database_path = destination / _DATABASE_NAME
    _validate_database(database_path)
    component_present = bool(manifest["componentPresent"])
    component_dimensions = _validate_component_files(
        {
            name: (destination / name).read_bytes()
            for name in expected
        },
        component_present,
    )
    if component_dimensions != manifest["embeddingDimensions"]:
        raise ProjectBackupError("backup_component_manifest_mismatch")
    if component_present and (
        _database_embedding_dimensions(database_path) != component_dimensions
    ):
        raise ProjectBackupError("backup_component_configuration_mismatch")
    return ExtractedProjectBackup(
        database_path=database_path,
        component_path=(destination / "memory-component-v1") if component_present else None,
        manifest=manifest,
    )


def merge_local_credentials(
    database_path: Path,
    local_settings: Mapping[str, Any],
) -> None:
    connection = sqlite3.connect(database_path)
    try:
        backup_models = _setting_json(connection, "ai_model_configs")
        local_models = local_settings.get("ai_model_configs")
        local_keys = {
            str(item.get("id") or ""): str(item.get("apiKey") or "")
            for item in local_models
            if isinstance(local_models, list) and isinstance(item, dict)
        } if isinstance(local_models, list) else {}
        if isinstance(backup_models, list):
            for item in backup_models:
                if isinstance(item, dict):
                    item["apiKey"] = local_keys.get(str(item.get("id") or ""), "")
            _set_setting_json(connection, "ai_model_configs", backup_models)

        backup_embedding = _setting_json(connection, "memory_embedding_config")
        local_embedding = local_settings.get("memory_embedding_config")
        if isinstance(backup_embedding, dict):
            backup_embedding["apiKey"] = (
                str(local_embedding.get("apiKey") or "")
                if isinstance(local_embedding, dict)
                and _embedding_identity(local_embedding)
                == _embedding_identity(backup_embedding)
                else ""
            )
            _set_setting_json(
                connection,
                "memory_embedding_config",
                backup_embedding,
            )
        connection.commit()
    finally:
        connection.close()


def database_stats(database_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(database_path)
    try:
        return {
            "books": _count(connection, "books"),
            "outlineChapters": _count(connection, "outline_chapters"),
            "articles": int(connection.execute(
                "SELECT COUNT(*) FROM articles "
                "WHERE content IS NOT NULL AND content != ''"
            ).fetchone()[0]),
        }
    finally:
        connection.close()


def _redact_database_credentials(payload: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="purrtypos-backup-db-") as raw:
        path = Path(raw) / _DATABASE_NAME
        path.write_bytes(payload)
        _validate_database(path)
        connection = sqlite3.connect(path)
        try:
            models = _setting_json(connection, "ai_model_configs")
            if isinstance(models, list):
                for item in models:
                    if isinstance(item, dict):
                        item["apiKey"] = ""
                _set_setting_json(connection, "ai_model_configs", models)
            embedding = _setting_json(connection, "memory_embedding_config")
            if isinstance(embedding, dict):
                embedding["apiKey"] = ""
                _set_setting_json(connection, "memory_embedding_config", embedding)
            connection.commit()
        finally:
            connection.close()
        return path.read_bytes()


def _validate_database(path: Path) -> None:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise ProjectBackupError("backup_database_integrity_failed")
            required = {"books", "settings", "memory_source_heads"}
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not required.issubset(tables):
                raise ProjectBackupError("backup_database_invalid")
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise ProjectBackupError("backup_database_invalid") from error


def _validate_component_files(
    files: Mapping[str, bytes],
    component_present: bool,
) -> int | None:
    manifest_name = f"{_MEMORY_PREFIX}manifest.json"
    component_names = {name for name in files if name.startswith(_MEMORY_PREFIX)}
    if not component_present:
        if component_names:
            raise ProjectBackupError("backup_component_unexpected")
        return None
    if manifest_name not in component_names:
        raise ProjectBackupError("backup_component_manifest_missing")
    try:
        manifest = json.loads(files[manifest_name])
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ProjectBackupError("backup_component_manifest_invalid") from error
    if not isinstance(manifest, dict):
        raise ProjectBackupError("backup_component_manifest_invalid")
    dimensions = manifest.get("embeddingDimensions")
    if manifest.get("format") != 1 or type(dimensions) is not int:
        raise ProjectBackupError("backup_component_manifest_invalid")
    history_name = f"{_MEMORY_PREFIX}history.sqlite3"
    journal_name = f"{_MEMORY_PREFIX}journal.sqlite3"
    if history_name not in component_names:
        raise ProjectBackupError("backup_component_incomplete")
    if not any(name.startswith(f"{_MEMORY_PREFIX}vectors/") for name in component_names):
        raise ProjectBackupError("backup_component_incomplete")
    _validate_component_database(files[history_name])
    if journal_name in component_names:
        _validate_component_database(files[journal_name])
    return dimensions


def _validate_component_database(payload: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="purrtypos-component-db-") as raw:
        path = Path(raw) / "component.sqlite3"
        path.write_bytes(payload)
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise ProjectBackupError("backup_component_integrity_failed")
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            raise ProjectBackupError("backup_component_integrity_failed") from error


def _validate_backup_manifest(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or value.get("format") != BACKUP_FORMAT:
        raise ProjectBackupError("backup_format_unsupported")
    if value.get("credentialsIncluded") is not False:
        raise ProjectBackupError("backup_credentials_policy_invalid")
    if not isinstance(value.get("componentPresent"), bool):
        raise ProjectBackupError("backup_manifest_invalid")
    dimensions = value.get("embeddingDimensions")
    if dimensions is not None and type(dimensions) is not int:
        raise ProjectBackupError("backup_manifest_invalid")
    files = value.get("files")
    if not isinstance(files, dict) or _DATABASE_NAME not in files:
        raise ProjectBackupError("backup_manifest_invalid")
    normalized: dict[str, dict[str, Any]] = {}
    for name, metadata in files.items():
        _safe_relative_name(name)
        if not isinstance(metadata, dict):
            raise ProjectBackupError("backup_manifest_invalid")
        size = metadata.get("size")
        digest = metadata.get("sha256")
        if type(size) is not int or size < 0:
            raise ProjectBackupError("backup_manifest_invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ProjectBackupError("backup_manifest_invalid")
        normalized[name] = {"size": size, "sha256": digest}
    return normalized


def _safe_archive_name(info: ZipInfo) -> str:
    if info.flag_bits & 0x1:
        raise ProjectBackupError("backup_encrypted_file_unsupported")
    if info.is_dir():
        raise ProjectBackupError("backup_directory_entry_unsupported")
    return _safe_relative_name(info.filename)


def _safe_relative_name(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProjectBackupError("backup_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProjectBackupError("backup_path_invalid")
    if value != _MANIFEST_NAME and value != _DATABASE_NAME and not value.startswith(_MEMORY_PREFIX):
        raise ProjectBackupError("backup_file_unsupported")
    return value


def _database_embedding_dimensions(path: Path) -> int | None:
    connection = sqlite3.connect(path)
    try:
        value = _setting_json(connection, "memory_embedding_config")
    finally:
        connection.close()
    dimensions = value.get("dimensions") if isinstance(value, dict) else None
    return dimensions if type(dimensions) is int else None


def _setting_json(connection: sqlite3.Connection, key: str) -> Any:
    row = connection.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def _set_setting_json(connection: sqlite3.Connection, key: str, value: Any) -> None:
    connection.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def _embedding_identity(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("apiProvider"),
        value.get("model"),
        value.get("baseUrl"),
        value.get("dimensions"),
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


__all__ = [
    "BACKUP_EXTENSION",
    "BACKUP_FORMAT",
    "ExtractedProjectBackup",
    "ProjectBackupError",
    "build_project_backup",
    "database_stats",
    "extract_project_backup",
    "merge_local_credentials",
]
