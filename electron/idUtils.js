const { randomInt } = require('crypto')

/** 数字 + 大写 + 小写，共 62 字符 */
const ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'

/**
 * 8 位随机 id：由上述 62 字符均匀抽取（crypto.randomInt），非 UUID。
 */
function shortId8() {
  let s = ''
  for (let i = 0; i < 8; i++) {
    s += ALPHABET[randomInt(62)]
  }
  return s
}

module.exports = { shortId8 }
