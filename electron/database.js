const path = require('path')
const fs = require('fs')
const { app } = require('electron')

// 使用 sql.js（纯 JS，无需 C++ 编译），兼容 Node 22 且无需 Visual Studio
let db = null
let dbPath = null

function persist() {
  if (!db || !dbPath) return
  try {
    const data = db.export()
    fs.writeFileSync(dbPath, Buffer.from(data))
  } catch (err) {
    console.error('数据库持久化失败:', err)
  }
}

function run(sql, params = []) {
  db.run(sql, params)
  persist()
}

function runAndGetId(sql, params = []) {
  db.run(sql, params)
  const row = get('SELECT last_insert_rowid() as id', [])
  persist()
  return row != null && row.id != null ? Number(row.id) : null
}

function get(sql, params = []) {
  const stmt = db.prepare(sql)
  stmt.bind(params)
  const obj = stmt.step() ? stmt.getAsObject() : null
  stmt.free()
  return obj
}

function all(sql, params = []) {
  const stmt = db.prepare(sql)
  stmt.bind(params)
  const results = []
  while (stmt.step()) results.push(stmt.getAsObject())
  stmt.free()
  return results
}

function getDbPath() {
  return dbPath
}

function exportToBuffer() {
  if (!db) return null
  persist()
  return Buffer.from(db.export())
}

function closeDatabase() {
  if (db) {
    try { db.close() } catch (_) {}
    db = null
  }
}

async function initDatabase() {
  if (db) return db

  const initSqlJs = require('sql.js')
  const SQL = await initSqlJs()
  const userDataPath = app.getPath('userData')
  dbPath = path.join(userDataPath, 'purrtypos.db')

  let buffer = null
  try {
    buffer = fs.readFileSync(dbPath)
  } catch {
    // 文件不存在，新建
  }

  db = new SQL.Database(buffer)

  db.run(`CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    cover_color TEXT DEFAULT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS outlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    type TEXT DEFAULT 'chapter',
    sort INTEGER DEFAULT 0,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  try { db.run(`ALTER TABLE outlines ADD COLUMN type TEXT DEFAULT 'chapter'`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN sort INTEGER DEFAULT 0`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN xmind_data TEXT DEFAULT NULL`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN file_path TEXT DEFAULT NULL`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN book_id INTEGER DEFAULT NULL`) } catch (_) {}
  try { db.run(`ALTER TABLE books ADD COLUMN enable_volume INTEGER DEFAULT 0`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN parent_outline_id INTEGER DEFAULT NULL`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN writing_chapter_id INTEGER DEFAULT NULL`) } catch (_) {}
  try { db.run(`ALTER TABLE outlines ADD COLUMN markdown_content TEXT DEFAULT NULL`) } catch (_) {}
  try { db.run(`UPDATE outlines SET type = 'chapter' WHERE type IS NULL`) } catch (_) {}
  db.run(`CREATE TABLE IF NOT EXISTS outline_chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outline_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    level INTEGER DEFAULT 1,
    progress TEXT DEFAULT 'todo',
    sort INTEGER DEFAULT 0,
    parent_id INTEGER DEFAULT NULL
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER NOT NULL UNIQUE,
    content TEXT DEFAULT '',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS ai_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id INTEGER,
    title TEXT DEFAULT '新对话',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  try { db.exec('ALTER TABLE ai_sessions ADD COLUMN closed INTEGER DEFAULT 0') } catch (_) {}
  try { db.exec('ALTER TABLE ai_sessions ADD COLUMN book_id INTEGER') } catch (_) {}
  db.run(`CREATE TABLE IF NOT EXISTS ai_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    chapter_id INTEGER,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  // 迁移：为没有 session_id 的历史对话自动创建"历史对话"分组
  try { db.exec('ALTER TABLE ai_conversations ADD COLUMN session_id INTEGER') } catch (_) {}
  // 迁移：新增 model 列记录实际使用的模型名
  try { db.exec('ALTER TABLE ai_conversations ADD COLUMN model TEXT DEFAULT NULL') } catch (_) {}
  // 迁移：新增 thinking 列记录思考过程
  try { db.exec('ALTER TABLE ai_conversations ADD COLUMN thinking TEXT DEFAULT NULL') } catch (_) {}
  try { db.exec('ALTER TABLE ai_conversations ADD COLUMN tool_call_segments TEXT DEFAULT NULL') } catch (_) {}
  try { db.exec('ALTER TABLE ai_conversations ADD COLUMN thinking_blocks TEXT DEFAULT NULL') } catch (_) {}
  db.run(`CREATE TABLE IF NOT EXISTS ai_favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    session_title TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  try { db.exec('ALTER TABLE ai_favorites ADD COLUMN prompt TEXT DEFAULT ""') } catch (_) {}
  db.run(`CREATE TABLE IF NOT EXISTS ai_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    layer TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    chapter_id INTEGER DEFAULT NULL,
    character_id INTEGER DEFAULT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS ai_foreshadowing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    chapter_id INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT '悬念',
    expected_chapter_id INTEGER DEFAULT NULL,
    status TEXT NOT NULL DEFAULT '未回收',
    resolved_chapter_id INTEGER DEFAULT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  const orphanedChapters = all(
    'SELECT DISTINCT chapter_id FROM ai_conversations WHERE session_id IS NULL AND chapter_id IS NOT NULL'
  )
  for (const { chapter_id } of orphanedChapters) {
    const sessionId = runAndGetId(
      "INSERT INTO ai_sessions (chapter_id, title, create_time) VALUES (?, '历史对话', datetime('now'))",
      [chapter_id]
    )
    run(
      'UPDATE ai_conversations SET session_id = ? WHERE chapter_id = ? AND session_id IS NULL',
      [sessionId, chapter_id]
    )
  }
  db.run(`CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS story_background (
    book_id INTEGER NOT NULL PRIMARY KEY,
    content TEXT DEFAULT '',
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS story_background_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    gender TEXT DEFAULT '',
    age TEXT DEFAULT '',
    height TEXT DEFAULT '',
    occupation TEXT DEFAULT '',
    appearance TEXT DEFAULT '',
    origin TEXT DEFAULT '',
    personality TEXT DEFAULT '',
    background TEXT DEFAULT '',
    biography TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
  )`)
  db.run(`CREATE TABLE IF NOT EXISTS character_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    value TEXT NOT NULL,
    sort INTEGER DEFAULT 0
  )`)
  try { db.run(`ALTER TABLE characters ADD COLUMN gender TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN age TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN height TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN occupation TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN appearance TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN origin TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN personality TEXT DEFAULT ''`) } catch (_) {}
  try { db.run(`ALTER TABLE characters ADD COLUMN remark TEXT DEFAULT ''`) } catch (_) {}

  // 初始化 character_options 默认值
  const personalitySeed = ['开朗', '内敛', '沉稳', '冲动', '善良', '冷酷', '腹黑', '正义']
  const tagSeed = ['主角', '配角', '反派', '导师', '神秘人物', '幕后黑手']
  for (const [i, v] of personalitySeed.entries()) {
    const exists = get('SELECT id FROM character_options WHERE category = ? AND value = ?', ['personality', v])
    if (!exists) run('INSERT INTO character_options (category, value, sort) VALUES (?, ?, ?)', ['personality', v, i])
  }
  for (const [i, v] of tagSeed.entries()) {
    const exists = get('SELECT id FROM character_options WHERE category = ? AND value = ?', ['tag', v])
    if (!exists) run('INSERT INTO character_options (category, value, sort) VALUES (?, ?, ?)', ['tag', v, i])
  }

  // 迁移：若有旧数据无书籍，自动创建默认书籍并关联
  const bookCount = get('SELECT COUNT(*) as c FROM books', [])
  if (!bookCount || Number(bookCount.c) === 0) {
    const outlineCount = get('SELECT COUNT(*) as c FROM outlines', [])
    if (outlineCount && Number(outlineCount.c) > 0) {
      run('INSERT INTO books (title, cover_color) VALUES (?, ?)', ['我的作品', '#4A90D9'])
      const defaultBook = get('SELECT id FROM books ORDER BY id ASC LIMIT 1', [])
      if (defaultBook) {
        run('UPDATE outlines SET book_id = ? WHERE book_id IS NULL', [defaultBook.id])
      }
    }
  }

  persist()
  console.log('数据库初始化成功:', dbPath)
  return db
}

function getDb() {
  if (!db) throw new Error('数据库未初始化，请先调用 initDatabase()')
  return db
}

// ─── 封装为与 main.js 兼容的 API ───────────────────────────────

class Database {
  constructor() {
    throw new Error('请使用 initDatabase() 初始化')
  }
}

Database.initDatabase = initDatabase
Database.getDb = getDb

// ─── 书籍 CRUD ─────────────────────────────────────────────────

const BOOK_COLORS = ['#4A90D9','#E67E22','#27AE60','#8E44AD','#C0392B','#16A085','#D35400','#2C3E50','#1ABC9C','#E74C3C']

Database.getBooks = function () {
  getDb()
  return all('SELECT * FROM books ORDER BY create_time ASC', [])
}

Database.createBook = function (title, enableVolume) {
  getDb()
  const cnt = get('SELECT COUNT(*) as c FROM books', [])
  const color = BOOK_COLORS[Number(cnt?.c ?? 0) % BOOK_COLORS.length]
  const vol = enableVolume ? 1 : 0
  run('INSERT INTO books (title, cover_color, enable_volume) VALUES (?, ?, ?)', [title, color, vol])
  const row = get('SELECT * FROM books ORDER BY id DESC LIMIT 1', [])
  // 自动为新书创建 writing outline
  run('INSERT INTO outlines (title, type, sort, book_id) VALUES (?, ?, ?, ?)', [title, 'writing', 0, Number(row.id)])
  return row
}

Database.deleteBook = function (bookId) {
  getDb()
  // 删除所有关联章节内容
  const outlines = all('SELECT id FROM outlines WHERE book_id = ?', [bookId])
  for (const o of outlines) {
    const chapters = all('SELECT id FROM outline_chapters WHERE outline_id = ?', [o.id])
    for (const ch of chapters) {
      run('DELETE FROM articles WHERE chapter_id = ?', [ch.id])
      run('DELETE FROM ai_conversations WHERE chapter_id = ?', [ch.id])
    }
    run('DELETE FROM outline_chapters WHERE outline_id = ?', [o.id])
    run('DELETE FROM outlines WHERE id = ?', [o.id])
  }
  run('DELETE FROM characters WHERE book_id = ?', [bookId])
  run('DELETE FROM story_background WHERE book_id = ?', [bookId])
  const attachments = all('SELECT stored_path FROM story_background_attachments WHERE book_id = ?', [bookId])
  run('DELETE FROM story_background_attachments WHERE book_id = ?', [bookId])
  run('DELETE FROM books WHERE id = ?', [bookId])
  return { attachmentPaths: (attachments || []).map((a) => a.stored_path) }
}

Database.renameBook = function (bookId, title) {
  getDb()
  run('UPDATE books SET title = ? WHERE id = ?', [title, bookId])
}

// ─── 大纲（按 book_id 作用域）──────────────────────────────────

Database.saveOutline = function (data) {
  getDb()
  const title = typeof data === 'string' ? data : data.title
  const type = (typeof data === 'object' && data.type) || 'other'
  const xmindData = (typeof data === 'object' && data.xmind_data) || null
  const filePath = (typeof data === 'object' && data.file_path) || null
  const bookId = (typeof data === 'object' && data.book_id) || null
  const writingChapterId = (typeof data === 'object' && data.writing_chapter_id != null) ? data.writing_chapter_id : null
  const parentOutlineId = (typeof data === 'object' && data.parent_outline_id != null) ? data.parent_outline_id : null
  if (type === 'global') {
    const cond = bookId ? [bookId] : []
    const sql = bookId
      ? 'SELECT id FROM outlines WHERE type = ? AND book_id = ?'
      : 'SELECT id FROM outlines WHERE type = ? AND book_id IS NULL'
    const allGlobal = all(sql.replace('type = ?', 'type = ?'), ['global', ...cond])
    if (allGlobal.length > 0) {
      const keepId = allGlobal[0].id
      run('UPDATE outlines SET title = ?, xmind_data = ?, file_path = ? WHERE id = ?', [title, xmindData, filePath, keepId])
      for (let i = 1; i < allGlobal.length; i++) {
        run('DELETE FROM outline_chapters WHERE outline_id = ?', [allGlobal[i].id])
        run('DELETE FROM outlines WHERE id = ?', [allGlobal[i].id])
      }
      return { id: keepId, title, type: 'global', book_id: bookId }
    }
  }
  const maxSort = bookId
    ? get('SELECT COALESCE(MAX(sort), 0) as m FROM outlines WHERE type = ? AND book_id = ?', [type, bookId])
    : get('SELECT COALESCE(MAX(sort), 0) as m FROM outlines WHERE type = ?', [type])
  const sort = (maxSort?.m ?? 0) + 1
  run(
    'INSERT INTO outlines (title, type, sort, xmind_data, file_path, book_id, writing_chapter_id, parent_outline_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [title, type, sort, xmindData, filePath, bookId, writingChapterId, parentOutlineId]
  )
  const inserted = get('SELECT id FROM outlines ORDER BY id DESC LIMIT 1', [])
  return { id: Number(inserted.id), title, type, book_id: bookId, writing_chapter_id: writingChapterId, parent_outline_id: parentOutlineId }
}

Database.updateOutlineXmind = function (outlineId, title, xmindData, filePath) {
  getDb()
  run('UPDATE outlines SET title = ?, xmind_data = ?, file_path = ? WHERE id = ?', [title, xmindData, filePath || null, outlineId])
  return get('SELECT * FROM outlines WHERE id = ?', [outlineId])
}

Database.getVolumeOutlines = function (bookId) {
  getDb()
  const vols = bookId
    ? all('SELECT * FROM outlines WHERE type = ? AND book_id = ? ORDER BY sort ASC, create_time ASC', ['volume', bookId])
    : all('SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC', ['volume'])
  return vols.map((vol) => ({
    ...vol,
    chapters: all(
      'SELECT * FROM outlines WHERE type = ? AND parent_outline_id = ? ORDER BY sort ASC, create_time ASC',
      ['chapter', vol.id]
    ),
  }))
}

Database.getOutlineByWritingChapterId = function (writingChapterId) {
  getDb()
  return get('SELECT * FROM outlines WHERE writing_chapter_id = ? LIMIT 1', [writingChapterId])
}

/** 只读：本书写作大纲行（无则 null，不创建） */
Database.getWritingOutline = function (bookId) {
  getDb()
  if (bookId) return get('SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1', ['writing', bookId])
  return get('SELECT * FROM outlines WHERE type = ? LIMIT 1', ['writing'])
}

Database.getOrCreateWritingOutline = function (bookId) {
  getDb()
  if (bookId) {
    let row = get('SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1', ['writing', bookId])
    if (!row) {
      const book = get('SELECT title FROM books WHERE id = ?', [bookId])
      run('INSERT INTO outlines (title, type, sort, book_id) VALUES (?, ?, ?, ?)', [book?.title || '我的作品', 'writing', 0, bookId])
      row = get('SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1', ['writing', bookId])
    }
    return row
  }
  // 向后兼容：无 bookId 时取第一个 writing outline
  let row = get('SELECT * FROM outlines WHERE type = ? LIMIT 1', ['writing'])
  if (!row) {
    run('INSERT INTO outlines (title, type, sort) VALUES (?, ?, ?)', ['我的作品', 'writing', 0])
    row = get('SELECT * FROM outlines WHERE type = ? LIMIT 1', ['writing'])
  }
  return row
}

Database.getOutlines = function (typeFilter) {
  getDb()
  if (typeFilter === 'global' || typeFilter === 'chapter' || typeFilter === 'other') {
    return all('SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC', [typeFilter])
  }
  return all('SELECT * FROM outlines ORDER BY type ASC, sort ASC, create_time ASC')
}

Database.getGlobalOutline = function (bookId) {
  getDb()
  if (bookId) {
    const scoped = get('SELECT * FROM outlines WHERE type = ? AND book_id = ? LIMIT 1', ['global', bookId])
    if (scoped) return scoped
    // 兼容旧库：历史全局总纲可能未写入 book_id
    return get('SELECT * FROM outlines WHERE type = ? AND book_id IS NULL LIMIT 1', ['global'])
  }
  // 无 bookId 时仅返回未归属旧数据，避免跨书误取
  return get('SELECT * FROM outlines WHERE type = ? AND book_id IS NULL LIMIT 1', ['global'])
}

/** 若无总纲行则插入仅 Markdown 可用的空总纲（标题固定「总纲」），便于与章节大纲一样编辑文本大纲 */
Database.getOrCreateGlobalOutline = function (bookId) {
  getDb()
  const existing = Database.getGlobalOutline(bookId)
  if (existing) return existing
  Database.saveOutline({
    title: '总纲',
    type: 'global',
    book_id: bookId ?? null,
    xmind_data: null,
    file_path: null,
  })
  return Database.getGlobalOutline(bookId)
}

Database.getChapterOutlines = function (bookId) {
  getDb()
  if (bookId) return all('SELECT * FROM outlines WHERE type = ? AND book_id = ? ORDER BY sort ASC, create_time ASC', ['chapter', bookId])
  return all('SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC', ['chapter'])
}

Database.getOtherOutlines = function (bookId) {
  getDb()
  if (bookId) return all('SELECT * FROM outlines WHERE type = ? AND book_id = ? ORDER BY sort ASC, create_time ASC', ['other', bookId])
  return all('SELECT * FROM outlines WHERE type = ? ORDER BY sort ASC, create_time ASC', ['other'])
}

Database.deleteOutline = function (outlineId) {
  getDb()
  run('DELETE FROM outline_chapters WHERE outline_id = ?', [outlineId])
  run('DELETE FROM outlines WHERE id = ?', [outlineId])
}

Database.updateOutline = function (data) {
  getDb()
  const outlineId = data.outlineId
  if (!outlineId) throw new Error('outlineId required')
  const parts = []
  const vals = []
  if (data.title !== undefined) {
    parts.push('title = ?')
    vals.push(data.title)
  }
  if (data.xmind_data !== undefined) {
    parts.push('xmind_data = ?')
    vals.push(data.xmind_data)
    parts.push('file_path = ?')
    vals.push(data.file_path ?? null)
  }
  if (data.markdown_content !== undefined) {
    parts.push('markdown_content = ?')
    vals.push(data.markdown_content)
  }
  if (parts.length === 0) {
    return get('SELECT * FROM outlines WHERE id = ?', [outlineId])
  }
  vals.push(outlineId)
  run(`UPDATE outlines SET ${parts.join(', ')} WHERE id = ?`, vals)
  return get('SELECT * FROM outlines WHERE id = ?', [outlineId])
}

Database.saveChapters = function (outlineId, chapters) {
  getDb()
  run('DELETE FROM outline_chapters WHERE outline_id = ?', [outlineId])
  const lastIdByLevel = {}
  for (const item of chapters) {
    const level = item.level || 1
    const parentId = item.parentId ?? item.parent_id ?? (level > 1 ? lastIdByLevel[level - 1] ?? null : null)
    const id = runAndGetId(
      'INSERT INTO outline_chapters (outline_id, title, level, progress, sort, parent_id) VALUES (?, ?, ?, ?, ?, ?)',
      [outlineId, item.title, level, item.progress || 'todo', item.sort || 0, parentId]
    )
    if (id) lastIdByLevel[level] = id
  }
}

Database.addChapter = function (outlineId, title, parentId = null) {
  getDb()
  const parentIdVal = parentId ?? null
  const level = parentIdVal != null ? 2 : 1
  const max = parentIdVal != null
    ? get('SELECT COALESCE(MAX(sort), 0) as m FROM outline_chapters WHERE outline_id = ? AND parent_id = ?', [outlineId, parentIdVal])
    : get('SELECT COALESCE(MAX(sort), 0) as m FROM outline_chapters WHERE outline_id = ? AND parent_id IS NULL', [outlineId])
  const sort = (max?.m ?? 0) + 1
  run(
    'INSERT INTO outline_chapters (outline_id, title, level, progress, sort, parent_id) VALUES (?, ?, ?, ?, ?, ?)',
    [outlineId, title, level, 'todo', sort, parentIdVal]
  )
  const inserted = get('SELECT id FROM outline_chapters WHERE outline_id = ? ORDER BY id DESC LIMIT 1', [outlineId])
  return { id: Number(inserted.id), outline_id: outlineId, title, level, progress: 'todo', sort, parent_id: parentIdVal }
}

Database.deleteChapter = function (chapterId) {
  getDb()
  run('DELETE FROM outline_chapters WHERE id = ?', [chapterId])
  run('DELETE FROM articles WHERE chapter_id = ?', [chapterId])
}

Database.renameChapter = function (chapterId, title) {
  getDb()
  run('UPDATE outline_chapters SET title = ? WHERE id = ?', [title, chapterId])
}

Database.getChapters = function (outlineId) {
  getDb()
  return all('SELECT * FROM outline_chapters WHERE outline_id = ? ORDER BY COALESCE(parent_id, id) ASC, sort ASC', [outlineId])
}

Database.updateChapterProgress = function (chapterId, progress) {
  getDb()
  run('UPDATE outline_chapters SET progress = ? WHERE id = ?', [progress, chapterId])
}

Database.saveArticle = function (chapterId, content) {
  getDb()
  const existing = get('SELECT id FROM articles WHERE chapter_id = ?', [chapterId])
  if (existing) {
    run('UPDATE articles SET content = ?, update_time = CURRENT_TIMESTAMP WHERE chapter_id = ?', [
      content,
      chapterId,
    ])
  } else {
    run('INSERT INTO articles (chapter_id, content) VALUES (?, ?)', [chapterId, content])
  }
}

Database.getArticle = function (chapterId) {
  getDb()
  return get('SELECT * FROM articles WHERE chapter_id = ?', [chapterId])
}

Database.getStoryBackground = function (bookId) {
  getDb()
  return get('SELECT * FROM story_background WHERE book_id = ?', [bookId])
}

Database.saveStoryBackground = function (bookId, content) {
  getDb()
  const existing = get('SELECT book_id FROM story_background WHERE book_id = ?', [bookId])
  if (existing) {
    run('UPDATE story_background SET content = ?, update_time = CURRENT_TIMESTAMP WHERE book_id = ?', [content, bookId])
  } else {
    run('INSERT INTO story_background (book_id, content) VALUES (?, ?)', [bookId, content])
  }
}

Database.getStoryBackgroundAttachments = function (bookId) {
  getDb()
  return all('SELECT id, book_id, name, stored_path, create_time FROM story_background_attachments WHERE book_id = ? ORDER BY create_time ASC', [bookId])
}

Database.addStoryBackgroundAttachment = function (bookId, name, storedPath) {
  getDb()
  const id = runAndGetId('INSERT INTO story_background_attachments (book_id, name, stored_path) VALUES (?, ?, ?)', [bookId, name, storedPath])
  return id != null ? get('SELECT id, book_id, name, stored_path, create_time FROM story_background_attachments WHERE id = ?', [id]) : null
}

Database.deleteStoryBackgroundAttachment = function (id) {
  getDb()
  const row = get('SELECT stored_path FROM story_background_attachments WHERE id = ?', [id])
  run('DELETE FROM story_background_attachments WHERE id = ?', [id])
  return row ? row.stored_path : null
}

Database.createSession = function (bookId, chapterId) {
  getDb()
  run('INSERT INTO ai_sessions (book_id, chapter_id) VALUES (?, ?)', [bookId, chapterId != null ? chapterId : null])
  let idRow = get('SELECT last_insert_rowid() as id', [])
  let id = idRow != null && Number(idRow.id) > 0 ? Number(idRow.id) : null
  if (id == null || id <= 0) {
    const maxRow = get('SELECT MAX(id) as id FROM ai_sessions', [])
    id = maxRow && maxRow.id != null ? Number(maxRow.id) : 1
  }
  persist()
  const row = get('SELECT * FROM ai_sessions WHERE id = ?', [id])
  const session = row && typeof row === 'object'
    ? { id: Number(row.id), book_id: row.book_id != null ? row.book_id : null, chapter_id: row.chapter_id != null ? row.chapter_id : null, title: row.title || '新对话', create_time: row.create_time, closed: row.closed ?? 0 }
    : { id, book_id: bookId, chapter_id: chapterId != null ? chapterId : null, title: '新对话', create_time: new Date().toISOString(), closed: 0 }
  return session
}

Database.getSessions = function (bookId, chapterId, includeClosed = false) {
  getDb()
  const closedCond = includeClosed ? '' : ' AND (closed IS NULL OR closed = 0)'
  if (chapterId == null) {
    return all(
      'SELECT * FROM ai_sessions WHERE book_id = ? AND chapter_id IS NULL' + closedCond + ' ORDER BY create_time ASC',
      [bookId]
    )
  }
  return all(
    'SELECT * FROM ai_sessions WHERE book_id = ? AND chapter_id = ?' + closedCond + ' ORDER BY create_time ASC',
    [bookId, chapterId]
  )
}

Database.setSessionClosed = function (sessionId) {
  getDb()
  run('UPDATE ai_sessions SET closed = 1 WHERE id = ?', [sessionId])
}

Database.setSessionReopened = function (sessionId) {
  getDb()
  run('UPDATE ai_sessions SET closed = 0 WHERE id = ?', [sessionId])
}

Database.deleteSession = function (sessionId) {
  getDb()
  run('DELETE FROM ai_conversations WHERE session_id = ?', [sessionId])
  run('DELETE FROM ai_sessions WHERE id = ?', [sessionId])
}

Database.updateSessionTitle = function (sessionId, title) {
  getDb()
  run('UPDATE ai_sessions SET title = ? WHERE id = ?', [title, sessionId])
}

Database.saveConversation = function (sessionId, chapterId, prompt, response, model, thinking, toolCallSegmentsJson, thinkingBlocksJson) {
  getDb()
  run(
    'INSERT INTO ai_conversations (session_id, chapter_id, prompt, response, model, thinking, tool_call_segments, thinking_blocks) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [sessionId, chapterId, prompt, response, model || null, thinking || null, toolCallSegmentsJson || null, thinkingBlocksJson || null]
  )
}

Database.getConversations = function (sessionId) {
  getDb()
  return all('SELECT * FROM ai_conversations WHERE session_id = ? ORDER BY create_time ASC', [sessionId])
}

/** 只保留该 session 下前 keepTurnCount 轮对话，删除之后的记录（用于编辑并重新发送时截断） */
Database.deleteConversationsAfterTurn = function (sessionId, keepTurnCount) {
  getDb()
  if (keepTurnCount <= 0) {
    run('DELETE FROM ai_conversations WHERE session_id = ?', [sessionId])
    return
  }
  const toKeep = all('SELECT id FROM ai_conversations WHERE session_id = ? ORDER BY create_time ASC LIMIT ?', [sessionId, keepTurnCount])
  if (toKeep.length === 0) return
  const placeholders = toKeep.map(() => '?').join(',')
  run('DELETE FROM ai_conversations WHERE session_id = ? AND id NOT IN (' + placeholders + ')', [sessionId, ...toKeep.map((r) => r.id)])
}

// ─── AI 收藏 ───────────────────────────────────────────────────
Database.saveAiFavorite = function (sessionId, sessionTitle, prompt, content) {
  getDb()
  const id = runAndGetId(
    'INSERT INTO ai_favorites (session_id, session_title, prompt, content) VALUES (?, ?, ?, ?)',
    [sessionId, sessionTitle, prompt || '', content]
  )
  return get('SELECT * FROM ai_favorites WHERE id = ?', [id])
}

Database.getAiFavorites = function () {
  getDb()
  return all('SELECT * FROM ai_favorites ORDER BY create_time DESC')
}

Database.deleteAiFavorite = function (id) {
  getDb()
  run('DELETE FROM ai_favorites WHERE id = ?', [id])
}

// ─── 长期记忆（五层）────────────────────────────────────────────
const MEMORY_LAYERS = ['全局', '大纲', '人物', '章节']

Database.addMemory = function (bookId, layer, content, chapterId, characterId) {
  getDb()
  const id = runAndGetId(
    'INSERT INTO ai_memories (book_id, layer, content, chapter_id, character_id) VALUES (?, ?, ?, ?, ?)',
    [bookId, layer, content || '', chapterId ?? null, characterId ?? null]
  )
  return get('SELECT * FROM ai_memories WHERE id = ?', [id])
}

Database.updateMemory = function (id, data) {
  getDb()
  const row = get('SELECT * FROM ai_memories WHERE id = ?', [id])
  if (!row) return null
  const content = data.content !== undefined ? data.content : row.content
  const chapterId = data.chapter_id !== undefined ? data.chapter_id : row.chapter_id
  const characterId = data.character_id !== undefined ? data.character_id : row.character_id
  run('UPDATE ai_memories SET content = ?, chapter_id = ?, character_id = ? WHERE id = ?', [content, chapterId, characterId, id])
  return get('SELECT * FROM ai_memories WHERE id = ?', [id])
}

Database.deleteMemory = function (id) {
  getDb()
  run('DELETE FROM ai_memories WHERE id = ?', [id])
}

Database.getMemoriesByBook = function (bookId, layer) {
  getDb()
  if (layer) {
    return all('SELECT * FROM ai_memories WHERE book_id = ? AND layer = ? ORDER BY create_time DESC', [bookId, layer])
  }
  return all('SELECT * FROM ai_memories WHERE book_id = ? ORDER BY layer ASC, create_time DESC', [bookId])
}

Database.getMemoriesByIds = function (ids) {
  if (!ids || ids.length === 0) return []
  getDb()
  const placeholders = ids.map(() => '?').join(',')
  return all(`SELECT * FROM ai_memories WHERE id IN (${placeholders}) ORDER BY layer ASC, create_time ASC`, ids)
}

function scoreMemoryByQuery(content, query) {
  if (!query || !query.trim()) return 1
  const terms = query.trim().split(/\s+/).filter(Boolean)
  if (terms.length === 0) terms.push(query.trim())
  let score = 0
  const lowerContent = content.toLowerCase ? content.toLowerCase() : content
  for (const t of terms) {
    const term = (t.toLowerCase && t.toLowerCase()) || t
    if (lowerContent.indexOf(term) !== -1) score += 1
  }
  return score
}

Database.getMemoriesForPrompt = function (bookId, query, options) {
  getDb()
  const opts = options || {}
  const layers = opts.layers || MEMORY_LAYERS
  const chapterId = opts.chapterId
  const limitPerLayer = opts.limitPerLayer ?? 5
  const totalLimit = opts.limit ?? 20
  const allRows = all('SELECT * FROM ai_memories WHERE book_id = ? ORDER BY create_time DESC', [bookId])
  const byLayer = {}
  for (const layer of layers) byLayer[layer] = []
  for (const row of allRows) {
    if (!layers.includes(row.layer)) continue
    if (chapterId != null && row.chapter_id != null && row.chapter_id !== chapterId && row.layer === '章节') continue
    byLayer[row.layer].push(row)
  }
  const scored = []
  for (const layer of layers) {
    const list = byLayer[layer]
    for (const row of list) {
      const score = scoreMemoryByQuery(row.content, query)
      scored.push({ ...row, _score: score, _layer: row.layer })
    }
  }
  scored.sort((a, b) => {
    if (b._score !== a._score) return b._score - a._score
    return (new Date(b.create_time) || 0) - (new Date(a.create_time) || 0)
  })
  const seen = new Set()
  const result = []
  for (const item of scored) {
    if (result.length >= totalLimit) break
    const layerCount = result.filter(r => r.layer === item.layer).length
    if (layerCount >= limitPerLayer) continue
    if (seen.has(item.id)) continue
    seen.add(item.id)
    const { _score, _layer, ...rest } = item
    result.push(rest)
  }
  return result
}

// ─── 伏笔记忆 ───────────────────────────────────────────────────
Database.addForeshadowing = function (bookId, chapterId, content, type, expectedChapterId) {
  getDb()
  const id = runAndGetId(
    'INSERT INTO ai_foreshadowing (book_id, chapter_id, content, type, expected_chapter_id, status) VALUES (?, ?, ?, ?, ?, ?)',
    [bookId, chapterId, content || '', type || '悬念', expectedChapterId ?? null, '未回收']
  )
  return get('SELECT * FROM ai_foreshadowing WHERE id = ?', [id])
}

Database.updateForeshadowing = function (id, data) {
  getDb()
  const row = get('SELECT * FROM ai_foreshadowing WHERE id = ?', [id])
  if (!row) return null
  const content = data.content !== undefined ? data.content : row.content
  const type = data.type !== undefined ? data.type : row.type
  const expectedChapterId = data.expected_chapter_id !== undefined ? data.expected_chapter_id : row.expected_chapter_id
  const status = data.status !== undefined ? data.status : row.status
  const resolvedChapterId = data.resolved_chapter_id !== undefined ? data.resolved_chapter_id : row.resolved_chapter_id
  run(
    'UPDATE ai_foreshadowing SET content = ?, type = ?, expected_chapter_id = ?, status = ?, resolved_chapter_id = ?, update_time = datetime(\'now\') WHERE id = ?',
    [content, type, expectedChapterId, status, resolvedChapterId, id]
  )
  return get('SELECT * FROM ai_foreshadowing WHERE id = ?', [id])
}

Database.deleteForeshadowing = function (id) {
  getDb()
  run('DELETE FROM ai_foreshadowing WHERE id = ?', [id])
}

Database.getForeshadowingByBook = function (bookId, statusFilter) {
  getDb()
  if (statusFilter) {
    return all('SELECT * FROM ai_foreshadowing WHERE book_id = ? AND status = ? ORDER BY create_time DESC', [bookId, statusFilter])
  }
  return all('SELECT * FROM ai_foreshadowing WHERE book_id = ? ORDER BY create_time DESC', [bookId])
}

Database.getForeshadowingByIds = function (ids) {
  if (!ids || ids.length === 0) return []
  getDb()
  const placeholders = ids.map(() => '?').join(',')
  return all(`SELECT * FROM ai_foreshadowing WHERE id IN (${placeholders}) ORDER BY create_time ASC`, ids)
}

function scoreForeshadowingByQuery(row, query) {
  if (!query || !query.trim()) return 1
  const terms = query.trim().split(/\s+/).filter(Boolean)
  if (terms.length === 0) terms.push(query.trim())
  const text = [row.content, row.type, row.status].join(' ')
  let score = 0
  const lower = text.toLowerCase ? text.toLowerCase() : text
  for (const t of terms) {
    const term = (t.toLowerCase && t.toLowerCase()) || t
    if (lower.indexOf(term) !== -1) score += 1
  }
  return score
}

Database.getForeshadowingForPrompt = function (bookId, query, options) {
  getDb()
  const opts = options || {}
  const limit = opts.limit ?? 10
  const statusFilter = opts.status
  let rows = statusFilter
    ? all('SELECT * FROM ai_foreshadowing WHERE book_id = ? AND status = ? ORDER BY create_time DESC', [bookId, statusFilter])
    : all('SELECT * FROM ai_foreshadowing WHERE book_id = ? ORDER BY create_time DESC', [bookId])
  if (query && query.trim()) {
    rows = rows.map(r => ({ ...r, _score: scoreForeshadowingByQuery(r, query) }))
    rows.sort((a, b) => b._score - a._score)
    rows = rows.filter(r => r._score > 0).slice(0, limit).map(({ _score, ...r }) => r)
  } else {
    rows = rows.slice(0, limit)
  }
  return rows
}

// ─── 人物 CRUD ─────────────────────────────────────────────────

Database.getCharacters = function (bookId) {
  getDb()
  return all('SELECT * FROM characters WHERE book_id = ? ORDER BY create_time ASC', [bookId])
}

Database.createCharacter = function (bookId, data) {
  getDb()
  const name = data.name || ''
  const gender = data.gender ?? ''
  const age = data.age ?? ''
  const height = data.height ?? ''
  const occupation = data.occupation ?? ''
  const appearance = data.appearance ?? ''
  const origin = data.origin ?? ''
  const personality = data.personality ?? ''
  const background = data.background ?? ''
  const biography = data.biography ?? ''
  const tags = data.tags ?? ''
  const remark = data.remark ?? ''
  run(
    'INSERT INTO characters (book_id, name, gender, age, height, occupation, appearance, origin, personality, background, biography, tags, remark) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [bookId, name, gender, age, height, occupation, appearance, origin, personality, background, biography, tags, remark]
  )
  return get('SELECT * FROM characters ORDER BY id DESC LIMIT 1', [])
}

Database.updateCharacter = function (id, data) {
  getDb()
  const row = get('SELECT * FROM characters WHERE id = ?', [id])
  if (!row) return null
  const name = data.name !== undefined ? data.name : row.name
  const gender = data.gender !== undefined ? data.gender : (row.gender ?? '')
  const age = data.age !== undefined ? data.age : (row.age ?? '')
  const height = data.height !== undefined ? data.height : (row.height ?? '')
  const occupation = data.occupation !== undefined ? data.occupation : (row.occupation ?? '')
  const appearance = data.appearance !== undefined ? data.appearance : (row.appearance ?? '')
  const origin = data.origin !== undefined ? data.origin : (row.origin ?? '')
  const personality = data.personality !== undefined ? data.personality : (row.personality ?? '')
  const background = data.background !== undefined ? data.background : row.background
  const biography = data.biography !== undefined ? data.biography : row.biography
  const tags = data.tags !== undefined ? data.tags : row.tags
  const remark = data.remark !== undefined ? data.remark : (row.remark ?? '')
  run(
    'UPDATE characters SET name = ?, gender = ?, age = ?, height = ?, occupation = ?, appearance = ?, origin = ?, personality = ?, background = ?, biography = ?, tags = ?, remark = ? WHERE id = ?',
    [name, gender, age, height, occupation, appearance, origin, personality, background, biography, tags, remark, id]
  )
  return get('SELECT * FROM characters WHERE id = ?', [id])
}

Database.deleteCharacter = function (id) {
  getDb()
  run('DELETE FROM characters WHERE id = ?', [id])
}

// ─── 人物选项（性格 / 标签候选值）CRUD ─────────────────────────

Database.getCharacterOptions = function (category) {
  getDb()
  return all('SELECT * FROM character_options WHERE category = ? ORDER BY sort ASC, id ASC', [category])
}

Database.addCharacterOption = function (category, value) {
  getDb()
  const existing = get('SELECT id FROM character_options WHERE category = ? AND value = ?', [category, value])
  if (existing) return existing
  const maxSort = get('SELECT MAX(sort) as m FROM character_options WHERE category = ?', [category])
  const sort = (maxSort?.m ?? -1) + 1
  run('INSERT INTO character_options (category, value, sort) VALUES (?, ?, ?)', [category, value, sort])
  return get('SELECT * FROM character_options ORDER BY id DESC LIMIT 1', [])
}

Database.updateCharacterOption = function (id, value) {
  getDb()
  run('UPDATE character_options SET value = ? WHERE id = ?', [value, id])
  return get('SELECT * FROM character_options WHERE id = ?', [id])
}

Database.deleteCharacterOption = function (id) {
  getDb()
  run('DELETE FROM character_options WHERE id = ?', [id])
}

const BOOL_SETTINGS_KEYS = ['sync_outline_chapter']
const STRING_SETTINGS_KEYS = ['ai_system_prompt', 'ai_model_configs', 'ai_agent_mode']
const SETTINGS_KEYS = [...BOOL_SETTINGS_KEYS, ...STRING_SETTINGS_KEYS]

const DEFAULT_SYSTEM_PROMPT = '你是一位专业的写作助手，请帮助用户完善写作内容。'

function parseAiModelConfigs(raw) {
  if (raw == null || raw === '') return []
  try {
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : []
  } catch (_) {
    return []
  }
}

Database.getSettings = function () {
  getDb()
  const map = {}
  for (const key of SETTINGS_KEYS) {
    const row = get('SELECT value FROM settings WHERE key = ?', [key])
    map[key] = row?.value
  }
  return {
    sync_outline_chapter: map.sync_outline_chapter === '1',
    ai_system_prompt: map.ai_system_prompt ?? DEFAULT_SYSTEM_PROMPT,
    ai_model_configs: parseAiModelConfigs(map.ai_model_configs),
    ai_agent_mode: map.ai_agent_mode === 'subagent' ? 'subagent' : 'legacy',
  }
}

Database.setSettings = function (data) {
  getDb()
  for (const key of BOOL_SETTINGS_KEYS) {
    if (data[key] !== undefined) {
      const val = typeof data[key] === 'boolean' ? data[key] : data[key] === '1'
      run('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', [key, val ? '1' : '0'])
    }
  }
  for (const key of STRING_SETTINGS_KEYS) {
    if (data[key] !== undefined) {
      const val = key === 'ai_model_configs'
        ? (Array.isArray(data[key]) ? JSON.stringify(data[key]) : String(data[key]))
        : String(data[key])
      run('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', [key, val])
    }
  }
}

Database.getDbPath = getDbPath
Database.exportToBuffer = exportToBuffer
Database.closeDatabase = closeDatabase

module.exports = Database
