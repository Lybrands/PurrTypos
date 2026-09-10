"""Resolve explicit material references; never infer a relationship from prose."""
import re
from collections import defaultdict

# Code examples, inline code and comments must remain literal.
TOKENS = re.compile(r'(?P<literal>```[^\n]*\n.*?(?:```|\Z)|~~~[^\n]*\n.*?(?:~~~|\Z)|`+[^`\n]*`+|<!--.*?-->)|(?P<link>(?<!\\)\[\[(?P<value>[^\]\n]+)\]\])', re.S)


def material_link(path, title):
    target = '资料/' + path.removesuffix('.md')
    # These delimiters cannot be represented reliably in Obsidian wikilinks.
    if any(c in target for c in '#|[]\n\r'):
        raise ValueError('资料文件名包含链接分隔符，请先在 Obsidian 中重命名')
    label = re.sub(r'[\[\]|\r\n]', ' ', title)
    return f'[[{target}|{label}]]'


def resolve_links(body, entries):
    by_name = defaultdict(list)
    paths = {}
    for entry in entries:
        by_name[entry['title']].append(entry)
        target = '资料/' + entry['path'].removesuffix('.md')
        paths[target] = entry
        paths[target + '.md'] = entry
    def replace(match):
        if match.group('literal'):
            return match.group(0)
        value = match.group('value')
        destination, sep, label = value.partition('|')
        name, anchor_sep, anchor = destination.partition('#')
        if name in paths:
            entry = paths[name]
        else:
            matches = by_name.get(name, [])
            if len(matches) > 1:
                raise ValueError(f'资料「{name}」存在同名项，请使用读取结果中的 materialLink 指定目标')
            if not matches:
                return match.group(0)
            entry = matches[0]
        link = material_link(entry['path'], entry['title'])
        target, _, default_label = link[2:-2].partition('|')
        return '[[' + target + (('#' + anchor) if anchor_sep else '') + '|' + (label if sep else default_label) + ']]'
    return TOKENS.sub(replace, body)


def resolve_source_links(body, entries):
    """Only persist source links with a unique material target at this fork."""
    def replace(match):
        if match.group('literal'):
            return match.group(0)
        destination, _, label = match.group('value').partition('|')
        name = destination.partition('#')[0]
        matches = [entry for entry in entries if name in {
            entry['title'], '资料/' + entry['path'], '资料/' + entry['path'].removesuffix('.md')}]
        if len(matches) != 1:
            return label or name
        return resolve_links(match.group(0), matches)
    return TOKENS.sub(replace, body)
