"""Single-client local Qdrant cache, explicitly opted-in Embedding, resumable jobs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from purra.cancellation import raise_if_stopped
from domains.writing.knowledge import KnowledgeError, digest
from infrastructure.memory.openai_embedding import OpenAIEmbeddingConfiguration, OpenAIEmbeddingGateway
from services.model_settings_service import get_setting_value


class KnowledgeVectorIndex:
    def __init__(self, db, directory, *, gateway_factory=OpenAIEmbeddingGateway):
        self.db, self.directory = db, directory
        self.gateway_factory = gateway_factory
        self.client = None
        self.gateway = None
        self.config_id = None
        self.lock = asyncio.Lock()

    async def config(self):
        from application.memory_component import parse_memory_embedding_configuration
        return parse_memory_embedding_configuration(await get_setting_value(self.db, 'memory_embedding_config'))

    async def configuration_id(self):
        config = await self.config()
        if not config:
            return None
        return digest([config.model, config.base_url, config.dimensions])

    async def status(self, binding):
        try:
            config = await self.config()
            current = await self.configuration_id()
        except Exception:
            return {'state': 'configuration_invalid'}
        rows = await self.db.fetch_all('''SELECT state,COUNT(*) AS count,SUM(calls) AS calls,SUM(tokens) AS tokens
            FROM novel_knowledge_index_jobs WHERE binding_id=? AND generation=? AND config=? GROUP BY state''',
            [binding['id'], binding['generation'], binding['semantic_config'] or ''])
        return {'state': 'disabled' if not binding['semantic_config'] else 'configuration_changed' if current != binding['semantic_config'] else 'enabled',
                'provider': config.base_url if config else None, 'model': config.model if config else None,
                'jobs': rows, 'remoteTextDisclosure': True}

    async def ready(self, binding):
        config = await self.config()
        config_id = await self.configuration_id()
        if not config or not binding['semantic_config'] or binding['semantic_config'] != config_id:
            raise KnowledgeError('semantic_configuration_changed')
        if self.gateway and self.config_id != config_id:
            await self.gateway.close()
            self.gateway = None
        if not self.gateway:
            self.gateway = self.gateway_factory(OpenAIEmbeddingConfiguration(
                model=config.model, api_key=config.api_key, base_url=config.base_url, dimensions=config.dimensions))
        self.config_id = config_id
        if self.client is None:
            from qdrant_client import QdrantClient
            self.client = QdrantClient(path=str(self.directory))
        name = 'novel_' + config_id[:24]
        if not self.client.collection_exists(name):
            from qdrant_client.models import Distance, VectorParams
            self.client.create_collection(name, vectors_config=VectorParams(size=config.dimensions, distance=Distance.COSINE))
        return name

    @staticmethod
    def point_id(chunk_id):
        return str(uuid.UUID(hashlib.md5(chunk_id.encode(), usedforsecurity=False).hexdigest()))

    async def index(self, binding, *, signal=None):
        async with self.lock:
            collection = await self.ready(binding)
            rows = await self.db.fetch_all('''SELECT c.* FROM novel_knowledge_chunks c
                JOIN novel_knowledge_documents d ON d.id=c.document_id AND d.revision=c.revision
                WHERE d.binding_id=? AND d.generation=? AND d.state='eligible' ''', [binding['id'], binding['generation']])
            current_ids = {self.point_id(r['id']) for r in rows}
            # Remove deleted/old-generation revisions; cache loss is repaired from immutable text.
            offset = None
            from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct
            filter_ = Filter(must=[FieldCondition(key='binding', match=MatchValue(value=binding['id']))])
            while True:
                points, offset = self.client.scroll(collection, scroll_filter=filter_, limit=256, offset=offset, with_payload=False)
                stale = [p.id for p in points if str(p.id) not in current_ids]
                if stale:
                    self.client.delete(collection, points_selector=stale)
                if offset is None:
                    break
            processed = 0
            for row in rows:
                raise_if_stopped(signal)
                job_id = digest([binding['id'], binding['generation'], row['id'], self.config_id])
                await self.db.execute('INSERT OR IGNORE INTO novel_knowledge_index_jobs(id,binding_id,generation,config,chunk_id,state) VALUES (?,?,?,?,?,?)',
                                      [job_id, binding['id'], binding['generation'], self.config_id, row['id'], 'pending'])
                job = await self.db.fetch_one('SELECT * FROM novel_knowledge_index_jobs WHERE id=?', [job_id])
                point_id = self.point_id(row['id'])
                if job['state'] == 'complete' and self.client.retrieve(collection, ids=[point_id], with_payload=False):
                    continue
                if len(row['text']) > 6000:
                    await self.db.execute("UPDATE novel_knowledge_index_jobs SET state='excluded',error='chunk_limit' WHERE id=?", [job_id])
                    continue
                if processed >= 40:
                    break
                processed += 1
                await self.db.execute("UPDATE novel_knowledge_index_jobs SET state='running',calls=calls+1,error=NULL WHERE id=?", [job_id])
                try:
                    result = await asyncio.wait_for(self.gateway.embed([row['text']], signal), timeout=30)
                    current = await self.db.fetch_one('''SELECT d.id FROM novel_knowledge_documents d
                        JOIN novel_knowledge_bindings b ON b.id=d.binding_id AND b.generation=d.generation
                        WHERE d.id=? AND d.revision=? AND d.state='eligible' AND b.state='active'
                        AND b.semantic_config=?''', [row['document_id'], row['revision'], self.config_id])
                    if not current:
                        raise KnowledgeError('index_source_changed')
                    self.client.upsert(collection, points=[PointStruct(id=point_id, vector=list(result.vectors[0]),
                        payload={'binding': binding['id'], 'generation': binding['generation'], 'chunk': row['id']})])
                    await self.db.execute("UPDATE novel_knowledge_index_jobs SET state='complete',tokens=? WHERE id=?", [result.input_tokens, job_id])
                except asyncio.CancelledError:
                    await self.db.execute("UPDATE novel_knowledge_index_jobs SET state='pending',error='cancelled' WHERE id=?", [job_id])
                    raise
                except Exception:
                    raise_if_stopped(signal)
                    await self.db.execute("UPDATE novel_knowledge_index_jobs SET state='failed',error='embedding_unavailable' WHERE id=?", [job_id])
                    break
            return {'processed': processed, **await self.status(binding)}

    async def query(self, binding, query, eligible_ids, signal):
        async with self.lock:
            collection = await self.ready(binding)
            raise_if_stopped(signal)
            result = await asyncio.wait_for(self.gateway.embed([query], signal), timeout=15)
            from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
            if not eligible_ids:
                return []
            # Query only current, admitted source IDs. Payload text is never returned.
            response = self.client.query_points(collection, query=list(result.vectors[0]), limit=40,
                query_filter=Filter(must=[FieldCondition(key='binding', match=MatchValue(value=binding['id'])),
                                         FieldCondition(key='chunk', match=MatchAny(any=eligible_ids))]))
            job_id = str(uuid.uuid4())
            await self.db.execute('INSERT INTO novel_knowledge_index_jobs(id,binding_id,generation,config,chunk_id,state,calls,tokens) VALUES (?,?,?,?,?,?,?,?)',
                [job_id, binding['id'], binding['generation'], self.config_id, 'query', 'query', 1, result.input_tokens])
            return [p.payload['chunk'] for p in response.points]

    async def close(self):
        async with self.lock:
            if self.gateway:
                await self.gateway.close()
            if self.client:
                self.client.close()
