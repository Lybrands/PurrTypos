---
name: getBookCharacters
description: 只读：获取本书人物设定（姓名、性别、年龄、职业、性格、外貌、背景等）。默认全部；每条含人物ID 供 addMemory 等使用。可选 characterIds 按 ID 子集；可选 names 按角色名模糊匹配。可与 getStoryBackground 组合。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    characterIds:
      type: array
      description: 可选。人物 ID 列表，来自返回中的「人物ID」
    names:
      type: array
      description: 可选。角色名关键词列表，模糊匹配
  required:
    - bookId
---

包含人物的相关信息（姓名、性别、年龄、职业、性格、外貌、背景等）。未传 characterIds 与 names 时返回全部；子集筛选优先 characterIds，否则可用 names。若只要 id 与姓名的 JSON 列表、不要详情，用 listBookCharacters。
