"""Route existing novel CRUD through the per-book file authority."""
from functools import wraps


def material_crud(kind, action):
    def decorate(function):
        @wraps(function)
        async def call(db, identity, *args, **kwargs):
            from application.creation_material_service import materials, TABLES
            svc = materials(db)
            expected = kwargs.pop('base_revision', None)
            async with db.transaction():
                by_book = action in ('list', 'create') or kind == 'background'
                if by_book:
                    book = str(identity)
                else:
                    table, key = TABLES[kind]
                    row = await db.fetch_one(f'SELECT book_id FROM {table} WHERE {key}=?', [identity])
                    book = str(row['book_id']) if row else ''
                binding = await svc.binding(book)
                if binding:
                    await svc.synchronize(book)
                    if action in ('create', 'update'):
                        if kind == 'background':
                            content = args[0] if args else kwargs['content']
                            normalized = await svc.normalize_links(book, {'content': content})
                            if args:
                                args = (normalized['content'], *args[1:])
                            else:
                                kwargs['content'] = normalized['content']
                        else:
                            data = args[0] if args else kwargs['data']
                            normalized = await svc.normalize_links(book, data)
                            if args:
                                args = (normalized, *args[1:])
                            else:
                                kwargs['data'] = normalized
                    if action in ('update', 'delete'):
                        data = (normalized if action == 'update' else {})
                        expected = data.get('baseRevision', expected)
                        await svc.update(book, kind, identity, data, expected, delete=action == 'delete')
                result = await function(db, identity, *args, **kwargs)
                if binding and action == 'create' and result:
                    await svc.add(book, kind, result[TABLES[kind][1]], result)
                if binding and result:
                    await svc.annotate(book, kind, result if isinstance(result, list) else [result])
                return result
        return call
    return decorate
