"""Recoverable file participants in the host SQLite commit boundary.

The displaced inode is retained. Publishing with link() never replaces a file
created by another editor during the rename/install window.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from exceptions import AppError


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def directory_fd(path):
    path = Path(path).absolute()
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


def ensure_directory(path, *, exclusive=False):
    path = Path(path)
    with directory_fd(path.parent) as fd:
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=fd)
            os.fsync(fd)
        except FileExistsError:
            if exclusive:
                raise
        child = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(child)


def read_at(fd, name):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
    try:
        with os.fdopen(handle, 'rb', closefd=False) as stream:
            return stream.read(1024 * 1024 + 1)
    finally:
        os.close(handle)


def write_at(fd, name, data):
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


class MaterialFileChange:
    def __init__(self, root, relative, before, after, *, operation_id=None):
        self.root = Path(root)
        self.relative = relative
        self.before = before
        self.after = after
        self.id = operation_id or uuid4().hex
        self.parent = self.root / Path(relative).parent
        self.name = Path(relative).name
        self.prefix = '.purr-' + self.id

    def prepare(self):
        with directory_fd(self.parent) as fd:
            try:
                current = read_at(fd, self.name)
            except FileNotFoundError:
                current = None
            if current != self.before:
                raise AppError('资料已被其他编辑器修改，请重新读取后再保存。', 409)
            intent = {'id': self.id, 'path': self.relative,
                      'before': revision(self.before) if self.before is not None else None,
                      'after': revision(self.after) if self.after is not None else None}
            write_at(fd, self.prefix + '.json', json.dumps(intent).encode())
            if self.before is not None:
                write_at(fd, self.prefix + '.base', self.before)
            if self.after is not None:
                write_at(fd, self.prefix + '.proposed', self.after)
                write_at(fd, self.prefix + '.new', self.after)
            if self.before is not None:
                os.rename(self.name, self.prefix + '.before', src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                if read_at(fd, self.prefix + '.before') != self.before:
                    raise AppError('资料在保存期间发生变化，原内容已保留，请重新读取。', 409)
            if self.after is not None:
                try:
                    os.link(self.prefix + '.new', self.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                except FileExistsError:
                    raise AppError('另一个编辑器正在保存，已保留双方内容，请重新读取。', 409) from None
                os.unlink(self.prefix + '.new', dir_fd=fd)
            os.fsync(fd)

    def rollback(self):
        self.recover(False)

    def committed(self):
        try:
            self.recover(True)
        except OSError:
            # The SQL receipt already proves commit; startup can finish marking.
            pass

    def recover(self, committed):
        with directory_fd(self.parent) as fd:
            try:
                intent = json.loads(read_at(fd, self.prefix + '.json'))
            except FileNotFoundError:
                return
            if not committed:
                try:
                    current = read_at(fd, self.name)
                except FileNotFoundError:
                    current = None
                if current is not None and revision(current) == intent['after']:
                    # Keep even a racing external revision on its displaced inode.
                    os.rename(self.name, self.prefix + '.rolled-back', src_dir_fd=fd, dst_dir_fd=fd)
                    current = None
                if current is None and intent['before'] is not None:
                    try:
                        write_at(fd, self.name, read_at(fd, self.prefix + '.before'))
                    except (FileExistsError, FileNotFoundError):
                        pass
            # The journal and displaced versions are retained for recovery/audit.
            done = self.prefix + ('.committed' if committed else '.aborted')
            try:
                write_at(fd, done, b'1')
            except FileExistsError:
                pass
            os.fsync(fd)


def pending(root):
    for path in Path(root).rglob('.purr-*.json'):
        prefix = path.with_suffix('')
        if prefix.with_suffix('.committed').exists() or prefix.with_suffix('.aborted').exists():
            continue
        with directory_fd(path.parent) as fd:
            intent = json.loads(read_at(fd, path.name))
        relative = intent['path']
        if Path(relative).is_absolute() or '..' in Path(relative).parts or (Path(root) / relative).parent != path.parent:
            raise AppError('资料恢复记录路径无效', 409)
        yield MaterialFileChange(root, relative, None, None, operation_id=intent['id'])
