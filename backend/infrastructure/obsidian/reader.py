from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from urllib.parse import unquote

import yaml

from domains.writing.knowledge import KnowledgeError, digest

MAX_BYTES = 1024 * 1024
MAX_DOCUMENTS = 2000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
SPLITTER_VERSION = 'paragraph-v1'


def checked_path(root: Path, relative: str) -> Path:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            raise KnowledgeError('symlink_not_allowed', 403)
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(
        p in ('..', '') or p.startswith('.') for p in parts
    ):
        raise KnowledgeError('path_outside_binding', 403)
    candidate = root
    for part in parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise KnowledgeError('symlink_not_allowed', 403)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise KnowledgeError('path_outside_binding', 403)
    return resolved


def read_stable(root: Path, relative: str) -> bytes:
    file = checked_path(root, relative)
    if file.suffix.lower() not in ('.md', '.markdown'):
        raise KnowledgeError('unsupported_file', 400)
    # O_NOFOLLOW also closes the final-component symlink race.
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    if os.open in os.supports_dir_fd:
        parent_fd = os.open(file.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in file.parts[1:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            fd = os.open(file.name, flags, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
    else:
        fd = os.open(file, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise KnowledgeError('file_limit', 413)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            content = stream.read(MAX_BYTES + 1)
        after = os.fstat(fd)
        current = checked_path(root, relative).stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns
        ) or (after.st_ino, after.st_mtime_ns) != (current.st_ino, current.st_mtime_ns):
            raise KnowledgeError('file_changing')
        if len(content) > MAX_BYTES:
            raise KnowledgeError('file_limit', 413)
        return content
    finally:
        os.close(fd)


def scan(root: Path) -> tuple[dict[str, bytes | str], dict[str, int]]:
    if root.is_symlink() or not root.is_dir():
        raise KnowledgeError('vault_unavailable', 503)
    files, skipped, total = {}, {}, 0
    def failed(error):
        raise KnowledgeError('vault_unavailable', 503) from error
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=failed):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            if name.startswith('.'):
                continue
            relative = (Path(directory) / name).relative_to(root).as_posix()
            ext = Path(name).suffix.lower()
            if ext not in ('.md', '.markdown'):
                skipped[ext or '(none)'] = skipped.get(ext or '(none)', 0) + 1
                continue
            if len(files) >= MAX_DOCUMENTS:
                raise KnowledgeError('document_limit', 413)
            try:
                value = read_stable(root, relative)
                total += len(value)
                if total > MAX_TOTAL_BYTES:
                    raise KnowledgeError('vault_size_limit', 413)
                files[relative] = value
            except (OSError, KnowledgeError) as error:
                if getattr(error, 'code', '') == 'vault_size_limit':
                    raise
                files[relative] = getattr(error, 'code', 'file_unavailable')
    return files, skipped


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError('yaml_alias_unsupported')
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError('yaml_duplicate_or_invalid_key')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse(content: bytes, path: str) -> dict:
    text = content.decode('utf-8-sig', errors='strict')
    meta, body = {}, text
    if text.startswith('---\n') or text.startswith('---\r\n'):
        match = re.match(r'^---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
        if not match or len(match[1]) > 32000:
            raise ValueError('yaml_invalid')
        meta = yaml.load(match[1], Loader=StrictLoader) or {}
        if not isinstance(meta, dict):
            raise ValueError('yaml_mapping_required')
        body = text[match.end():]
    for key, value in meta.items():
        if not isinstance(value, (str, int, float, bool, list, type(None))):
            raise ValueError('yaml_flat_properties_required')
        if isinstance(value, list) and any(not isinstance(v, str) for v in value):
            raise ValueError('yaml_string_list_required')
    for key in ('aliases', 'known_to', 'entity_refs'):
        if key in meta and not isinstance(meta[key], list):
            raise ValueError(f'{key}_list_required')
    for key in ('purr_id', 'type', 'status', 'temporal_scope', 'valid_from_chapter',
                'valid_to_chapter', 'knowledge_from_chapter', 'purr_entity'):
        if key in meta and not isinstance(meta[key], str):
            raise ValueError(f'{key}_string_required')
    title = meta.get('title') or next(iter(re.findall(r'^#\s+(.+)$', body, re.M)), Path(path).stem)
    if not isinstance(title, str):
        raise ValueError('title_string_required')
    # Parsing links never follows them. Both syntaxes preserve heading/block anchors.
    targets = re.findall(r'!?\[\[([^\]\n]+)\]\]', body)
    targets += re.findall(r'!?\[[^\]\n]*\]\(([^)\n]+)\)', body)
    links = []
    for target in targets[:500]:
        target = unquote(target.split('|', 1)[0].strip().strip('<>'))
        remote = bool(re.match(r'^[a-zA-Z][\w+.-]*:', target))
        note, _, anchor = target.partition('#')
        links.append({'target': note, 'anchor': anchor, 'state': 'external' if remote else 'unresolved'})
    chunks, heading, start, lines = [], '', 1, []
    def flush():
        nonlocal lines
        value = '\n'.join(lines).strip()
        if value:
            chunks.append({'text': value, 'heading': heading, 'line': start,
                           'blocks': re.findall(r'\^([\w-]+)\s*$', value, re.M)})
        lines = []
    for number, line in enumerate(body.splitlines(), 1):
        if re.match(r'^#{1,6}\s+', line):
            flush()
            heading = re.sub(r'^#+\s+', '', line)
            start = number
        elif not line.strip() and sum(map(len, lines)) >= 1000:
            flush()
            start = number + 1
        lines.append(line)
    flush()
    return {'title': title, 'metadata': meta, 'body': body, 'revision': digest(content),
            'chunks': chunks, 'links': links, 'splitter': SPLITTER_VERSION}
