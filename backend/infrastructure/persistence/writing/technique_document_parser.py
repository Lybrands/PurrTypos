"""Safe YAML and CommonMark interpretation for author-file storage."""

import re
import unicodedata
from pathlib import PurePosixPath
from typing import Mapping
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from domains.writing.techniques import TechniqueError, TechniqueLimits, author_path, canonical_bytes, digest


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise TechniqueError("invalid_entry", "入口元信息的键必须唯一且为字符串")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def entry_metadata(content: str) -> dict:
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.S)
    if not match:
        raise TechniqueError("invalid_entry", "SKILL.md 需要 YAML 头部中的 name 和 description")
    try:
        value = yaml.load(match.group(1), Loader=_UniqueSafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise TechniqueError("invalid_entry", "入口元信息不是有效的安全 YAML") from exc
    if not isinstance(value, dict):
        raise TechniqueError("invalid_entry", "入口元信息必须为对象")
    for key, maximum in (("name", 120), ("description", 1000)):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > maximum:
            raise TechniqueError("invalid_entry", f"入口 {key} 必须为非空字符串，最多 {maximum} 字符")
    if "tags" in value and (not isinstance(value["tags"], list) or any(not isinstance(v, str) for v in value["tags"])):
        raise TechniqueError("invalid_entry", "入口 tags 必须为字符串列表")
    return {key: value[key] for key in ("name", "description", "tags") if key in value}


def local_link(source: str, target: str) -> str | None:
    """External links are inert; callers may only read returned package paths."""
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    if path.startswith("/") or "\\" in path:
        raise TechniqueError("invalid_reference", f"{source} 引用了包外路径：{target}")
    parts = list(PurePosixPath(source).parent.parts)
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise TechniqueError("invalid_reference", f"{source} 引用了包外路径：{target}")
            parts.pop()
        else:
            parts.append(part)
    return author_path("/".join(parts))


def file_manifest(files: Mapping[str, str], *, validate: bool = True,
                  limits: TechniqueLimits = TechniqueLimits()) -> dict:
    if len(files) > limits.file_count:
        raise TechniqueError("file_exceeds_budget", "技法文件数量超过限制")
    entries, folded, total = [], set(), 0
    for path, text in sorted(files.items()):
        author_path(path, limits)
        if not isinstance(text, str):
            raise TechniqueError("invalid_entry", f"{path} 必须为 UTF-8 文本")
        normalized = unicodedata.normalize("NFC", path).casefold()
        if normalized in folded:
            raise TechniqueError("invalid_reference", f"文件路径在大小写或 Unicode 归一后冲突：{path}")
        folded.add(normalized)
        try:
            raw = text.encode("utf-8")
        except UnicodeError as exc:
            raise TechniqueError("invalid_entry", f"{path} 不是有效的 UTF-8 文本") from exc
        total += len(raw)
        if len(raw) > limits.file_bytes or total > limits.package_bytes:
            raise TechniqueError("file_exceeds_budget", f"技法文件超过大小限制：{path}")
        entries.append({"path": path, "size": len(raw), "sha256": digest(raw)})
    for path in folded:
        if any(str(parent) in folded for parent in PurePosixPath(path).parents):
            raise TechniqueError("invalid_reference", f"文件与目录重名：{path}")
    metadata = None
    if not validate and "SKILL.md" in files:
        try:
            metadata = entry_metadata(files["SKILL.md"])
        except TechniqueError:
            pass
    if validate:
        if "SKILL.md" not in files:
            raise TechniqueError("invalid_entry", "技法缺少根目录入口 SKILL.md")
        metadata = entry_metadata(files["SKILL.md"])
        parser = MarkdownIt("commonmark")
        for path, text in files.items():
            if not path.lower().endswith(".md"):
                continue
            for block in parser.parse(text):
                for token in block.children or []:
                    target = token.attrGet("href") if token.type == "link_open" else token.attrGet("src") if token.type == "image" else None
                    if target is not None:
                        resolved = local_link(path, target)
                        if resolved is not None and resolved not in files:
                            raise TechniqueError("invalid_reference", f"{path} 引用的文件不存在：{resolved}")
    tree = {"formatVersion": 1, "files": [{"path": e["path"], "sha256": e["sha256"]} for e in entries]}
    return {"formatVersion": 1, "versionId": digest(canonical_bytes(tree)), "files": entries, "metadata": metadata}


