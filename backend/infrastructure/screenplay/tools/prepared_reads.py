"""Load authorized cached screenplay results without executing tools."""

import json

from purra.cancellation import raise_if_stopped
from domains.read_materials import ReadMaterial
from domains.screenplay_agent.adapter import ScreenplayExecutionStateFactory
from domains.screenplay_agent.contracts import SCREENPLAY_DELIVERABLE_ROLES
from database.screenplay_tool_cache_schema import SCREENPLAY_READ_DEPENDENCIES
from infrastructure.screenplay.tools.query import ScreenplayToolQuery
from infrastructure.screenplay.tools.read_cache import screenplay_cache_identity


class ScreenplayPreparedReads:
    def __init__(self, db, catalog):
        self._db = db
        self._catalog = catalog

    async def load(self, request, signal=None):
        if not request.tools_enabled:
            return ()
        scope = ScreenplayExecutionStateFactory().create(request).domain
        enabled = self._catalog.enabled_names(request) & SCREENPLAY_READ_DEPENDENCIES.keys()
        candidates = []
        episode = scope.get("boundEpisodeNumber")
        if episode:
            candidates.append(("getScreenplayEpisodeContext", {"episodeNumber": episode}))
        for role in sorted(SCREENPLAY_DELIVERABLE_ROLES):
            candidates.append(("readScreenplayDeliverable", {"role": role}))
            revision = (scope.get("deliverableRevisionScope") or {}).get(role)
            if revision:
                candidates.append(("readScreenplayDeliverable", {"role": role, "revisionId": revision}))
        for name in sorted(enabled):
            candidates.append((name, {}))
        known = {
            screenplay_cache_identity(name, scope, args)[0]: (name, args)
            for name, args in candidates if name in enabled
        }
        scope_keys = {
            screenplay_cache_identity(name, scope, {})[1] for name in enabled
        }
        if not scope_keys:
            return ()
        async with self._db.transaction():
            rows = await self._db.fetch_all(
                "SELECT cache_key, tool_name, content, scope_key, arguments_json FROM screenplay_tool_cache "
                f"WHERE scope_key IN ({','.join('?' for _ in scope_keys)}) "
                f"OR cache_key IN ({','.join('?' for _ in known)}) "
                "OR (tool_name = 'readSourceChapters' AND arguments_json IS NULL) ORDER BY id DESC",
                [*scope_keys, *known],
            )
        materials = []
        for row in rows:
            raise_if_stopped(signal)
            name = row["tool_name"]
            if name not in enabled:
                continue
            payload = json.loads(row["content"])
            if row["arguments_json"]:
                arguments = json.loads(row["arguments_json"])
            elif name == "readSourceChapters":
                chapters = payload.get("chapters", [])
                if not chapters or any(not part.get("chapterId") for part in chapters):
                    continue
                arguments = {"chapterIds": [part["chapterId"] for part in chapters]}
            elif row["cache_key"] in known:
                arguments = known[row["cache_key"]][1]
            else:
                continue
            key, _scope_key = screenplay_cache_identity(name, scope, arguments)
            if key != row["cache_key"]:
                continue
            if payload.get("available") is False:
                continue
            metadata = {"projectId": scope["projectId"]}
            if name == "getScreenplayEpisodeContext":
                metadata["episodeNumber"] = arguments.get("episodeNumber", episode)
            if name == "readScreenplayTaskDependencies":
                metadata["partKeys"] = [part["partKey"] for part in payload.get("dependencies", [])]
            metadata["sourceRefs"] = ScreenplayToolQuery.source_refs(name, payload)
            materials.append(ReadMaterial(key, name, arguments, row["content"], metadata))
        materials.sort(key=lambda item: (
            item.tool_name != "getScreenplayEpisodeContext",
            item.metadata.get("episodeNumber") != episode,
            item.tool_name != "readScreenplayTaskDependencies",
        ))
        return tuple(materials)
