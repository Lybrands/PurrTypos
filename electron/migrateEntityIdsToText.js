/**
 * 将 books / outlines / outline_chapters 及所有外键从 INTEGER 迁移为 8 位字母数字 TEXT（idUtils.shortId8）。
 * 在首次检测到 books.id 仍为 INTEGER 时执行一次。
 */
const { shortId8 } = require('./idUtils')

function createEntityTablesSql() {
  return [
    `CREATE TABLE books (
      id TEXT PRIMARY KEY NOT NULL,
      title TEXT NOT NULL,
      cover_color TEXT DEFAULT NULL,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
      enable_volume INTEGER DEFAULT 0
    )`,
    `CREATE TABLE outlines (
      id TEXT PRIMARY KEY NOT NULL,
      title TEXT NOT NULL,
      type TEXT DEFAULT 'chapter',
      sort INTEGER DEFAULT 0,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
      xmind_data TEXT DEFAULT NULL,
      file_path TEXT DEFAULT NULL,
      book_id TEXT DEFAULT NULL,
      parent_outline_id TEXT DEFAULT NULL,
      writing_chapter_id TEXT DEFAULT NULL,
      markdown_content TEXT DEFAULT NULL
    )`,
    `CREATE TABLE outline_chapters (
      id TEXT PRIMARY KEY NOT NULL,
      outline_id TEXT NOT NULL,
      title TEXT NOT NULL,
      level INTEGER DEFAULT 1,
      progress TEXT DEFAULT 'todo',
      sort INTEGER DEFAULT 0,
      parent_id TEXT DEFAULT NULL
    )`,
    `CREATE TABLE articles (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chapter_id TEXT NOT NULL UNIQUE,
      content TEXT DEFAULT '',
      update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
    `CREATE TABLE ai_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chapter_id TEXT,
      title TEXT DEFAULT '新对话',
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
      closed INTEGER DEFAULT 0,
      book_id TEXT
    )`,
    `CREATE TABLE ai_conversations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id INTEGER,
      chapter_id TEXT,
      prompt TEXT NOT NULL,
      response TEXT NOT NULL,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
      model TEXT DEFAULT NULL,
      thinking TEXT DEFAULT NULL,
      tool_call_segments TEXT DEFAULT NULL,
      thinking_blocks TEXT DEFAULT NULL
    )`,
    `CREATE TABLE ai_favorites (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id INTEGER NOT NULL,
      session_title TEXT NOT NULL,
      prompt TEXT NOT NULL DEFAULT '',
      content TEXT NOT NULL,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
    `CREATE TABLE ai_memories (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id TEXT NOT NULL,
      layer TEXT NOT NULL,
      content TEXT NOT NULL DEFAULT '',
      chapter_id TEXT DEFAULT NULL,
      character_id INTEGER DEFAULT NULL,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
    `CREATE TABLE ai_foreshadowing (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id TEXT NOT NULL,
      chapter_id TEXT NOT NULL,
      content TEXT NOT NULL DEFAULT '',
      type TEXT NOT NULL DEFAULT '悬念',
      expected_chapter_id TEXT DEFAULT NULL,
      status TEXT NOT NULL DEFAULT '未回收',
      resolved_chapter_id TEXT DEFAULT NULL,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
      update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
    `CREATE TABLE story_background (
      book_id TEXT NOT NULL PRIMARY KEY,
      content TEXT DEFAULT '',
      update_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
    `CREATE TABLE story_background_attachments (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id TEXT NOT NULL,
      name TEXT NOT NULL,
      stored_path TEXT NOT NULL,
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
    `CREATE TABLE characters (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      book_id TEXT NOT NULL,
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
      remark TEXT DEFAULT '',
      create_time DATETIME DEFAULT CURRENT_TIMESTAMP
    )`,
  ]
}

function migrateEntityIdsToText8({ all, get, run, persist }) {
  const cols = all('PRAGMA table_info(books)')
  const idCol = cols.find((c) => c.name === 'id')
  if (!idCol || String(idCol.type).toUpperCase() === 'TEXT') {
    run('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ['entity_ids_text_v1', '1'])
    persist()
    return
  }

  const bookRows = all('SELECT * FROM books')
  const outlineRows = all('SELECT * FROM outlines')
  const chapterRows = all('SELECT * FROM outline_chapters')
  const articleRows = all('SELECT * FROM articles')
  const aiSessionRows = all('SELECT * FROM ai_sessions')
  const aiConvRows = all('SELECT * FROM ai_conversations')
  const storyBgRows = all('SELECT * FROM story_background')
  const storyAttRows = all('SELECT * FROM story_background_attachments')
  const charRows = all('SELECT * FROM characters')
  const memRows = all('SELECT * FROM ai_memories')
  const foreRows = all('SELECT * FROM ai_foreshadowing')
  const favRows = all('SELECT * FROM ai_favorites')

  const bookMap = new Map(bookRows.map((b) => [Number(b.id), shortId8()]))
  const outlineMap = new Map(outlineRows.map((o) => [Number(o.id), shortId8()]))
  const chapterMap = new Map(chapterRows.map((c) => [Number(c.id), shortId8()]))

  const mb = (x) => (x == null || x === '' ? null : bookMap.get(Number(x)) ?? String(x))
  const mo = (x) => (x == null || x === '' ? null : outlineMap.get(Number(x)) ?? String(x))
  const mc = (x) => (x == null || x === '' ? null : chapterMap.get(Number(x)) ?? String(x))

  const drops = [
    'ai_conversations',
    'ai_favorites',
    'ai_sessions',
    'articles',
    'ai_memories',
    'ai_foreshadowing',
    'outline_chapters',
    'outlines',
    'story_background_attachments',
    'story_background',
    'characters',
    'books',
  ]
  for (const t of drops) run('DROP TABLE IF EXISTS ' + t)

  for (const sql of createEntityTablesSql()) run(sql)

  for (const b of bookRows) {
    run('INSERT INTO books (id, title, cover_color, create_time, enable_volume) VALUES (?,?,?,?,?)', [
      mb(b.id),
      b.title,
      b.cover_color ?? null,
      b.create_time,
      b.enable_volume ?? 0,
    ])
  }

  for (const o of outlineRows) {
    run(
      'INSERT INTO outlines (id, title, type, sort, create_time, xmind_data, file_path, book_id, parent_outline_id, writing_chapter_id, markdown_content) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
      [
        mo(o.id),
        o.title,
        o.type ?? 'chapter',
        o.sort ?? 0,
        o.create_time,
        o.xmind_data ?? null,
        o.file_path ?? null,
        mb(o.book_id),
        mo(o.parent_outline_id),
        mc(o.writing_chapter_id),
        o.markdown_content ?? null,
      ],
    )
  }

  // 拓扑插入：先无 parent，再逐批插入 parent 已存在的行
  const remaining = [...chapterRows]
  const insertedOld = new Set()
  let guard = 0
  while (remaining.length && guard++ < remaining.length + 10) {
    let progressed = false
    for (let i = remaining.length - 1; i >= 0; i--) {
      const c = remaining[i]
      const pid = c.parent_id
      const needParent = pid != null && pid !== ''
      if (needParent && !insertedOld.has(Number(pid))) continue
      run(
        'INSERT INTO outline_chapters (id, outline_id, title, level, progress, sort, parent_id) VALUES (?,?,?,?,?,?,?)',
        [
          mc(c.id),
          mo(c.outline_id),
          c.title,
          c.level ?? 1,
          c.progress ?? 'todo',
          c.sort ?? 0,
          needParent ? mc(pid) : null,
        ],
      )
      insertedOld.add(Number(c.id))
      remaining.splice(i, 1)
      progressed = true
    }
    if (!progressed) break
  }
  for (const c of remaining) {
    run(
      'INSERT INTO outline_chapters (id, outline_id, title, level, progress, sort, parent_id) VALUES (?,?,?,?,?,?,?)',
      [
        mc(c.id),
        mo(c.outline_id),
        c.title,
        c.level ?? 1,
        c.progress ?? 'todo',
        c.sort ?? 0,
        c.parent_id != null && c.parent_id !== '' ? mc(c.parent_id) : null,
      ],
    )
  }

  for (const a of articleRows) {
    run('INSERT INTO articles (id, chapter_id, content, update_time) VALUES (?,?,?,?)', [
      a.id,
      mc(a.chapter_id),
      a.content ?? '',
      a.update_time,
    ])
  }

  for (const s of aiSessionRows) {
    run(
      'INSERT INTO ai_sessions (id, chapter_id, title, create_time, closed, book_id) VALUES (?,?,?,?,?,?)',
      [
        s.id,
        s.chapter_id != null && s.chapter_id !== '' ? mc(s.chapter_id) : null,
        s.title,
        s.create_time,
        s.closed ?? 0,
        s.book_id != null && s.book_id !== '' ? mb(s.book_id) : null,
      ],
    )
  }

  for (const c of aiConvRows) {
    run(
      'INSERT INTO ai_conversations (id, session_id, chapter_id, prompt, response, create_time, model, thinking, tool_call_segments, thinking_blocks) VALUES (?,?,?,?,?,?,?,?,?,?)',
      [
        c.id,
        c.session_id,
        c.chapter_id != null && c.chapter_id !== '' ? mc(c.chapter_id) : null,
        c.prompt,
        c.response,
        c.create_time,
        c.model ?? null,
        c.thinking ?? null,
        c.tool_call_segments ?? null,
        c.thinking_blocks ?? null,
      ],
    )
  }

  for (const s of storyBgRows) {
    run('INSERT INTO story_background (book_id, content, update_time) VALUES (?,?,?)', [
      mb(s.book_id),
      s.content ?? '',
      s.update_time,
    ])
  }

  for (const t of storyAttRows) {
    run('INSERT INTO story_background_attachments (id, book_id, name, stored_path, create_time) VALUES (?,?,?,?,?)', [
      t.id,
      mb(t.book_id),
      t.name,
      t.stored_path,
      t.create_time,
    ])
  }

  for (const ch of charRows) {
    run(
      'INSERT INTO characters (id, book_id, name, gender, age, height, occupation, appearance, origin, personality, background, biography, tags, remark, create_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      [
        ch.id,
        mb(ch.book_id),
        ch.name,
        ch.gender ?? '',
        ch.age ?? '',
        ch.height ?? '',
        ch.occupation ?? '',
        ch.appearance ?? '',
        ch.origin ?? '',
        ch.personality ?? '',
        ch.background ?? '',
        ch.biography ?? '',
        ch.tags ?? '',
        ch.remark ?? '',
        ch.create_time,
      ],
    )
  }

  for (const m of memRows) {
    run(
      'INSERT INTO ai_memories (id, book_id, layer, content, chapter_id, character_id, create_time) VALUES (?,?,?,?,?,?,?)',
      [
        m.id,
        mb(m.book_id),
        m.layer,
        m.content ?? '',
        m.chapter_id != null && m.chapter_id !== '' ? mc(m.chapter_id) : null,
        m.character_id ?? null,
        m.create_time,
      ],
    )
  }

  for (const f of foreRows) {
    run(
      'INSERT INTO ai_foreshadowing (id, book_id, chapter_id, content, type, expected_chapter_id, status, resolved_chapter_id, create_time, update_time) VALUES (?,?,?,?,?,?,?,?,?,?)',
      [
        f.id,
        mb(f.book_id),
        mc(f.chapter_id),
        f.content ?? '',
        f.type ?? '悬念',
        f.expected_chapter_id != null && f.expected_chapter_id !== '' ? mc(f.expected_chapter_id) : null,
        f.status ?? '未回收',
        f.resolved_chapter_id != null && f.resolved_chapter_id !== '' ? mc(f.resolved_chapter_id) : null,
        f.create_time,
        f.update_time,
      ],
    )
  }

  for (const fav of favRows) {
    run(
      'INSERT INTO ai_favorites (id, session_id, session_title, prompt, content, create_time) VALUES (?,?,?,?,?,?)',
      [
        fav.id,
        fav.session_id,
        fav.session_title,
        fav.prompt ?? '',
        fav.content,
        fav.create_time,
      ],
    )
  }

  run('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', ['entity_ids_text_v1', '1'])
  persist()
  console.log('[migrateEntityIdsToText8] 已迁移书籍/大纲/章节 id 为 TEXT')
}

module.exports = { migrateEntityIdsToText8, createEntityTablesSql }
