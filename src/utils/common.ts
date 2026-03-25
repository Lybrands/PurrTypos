/** 与 electron/idUtils.shortId8 一致：数字 + 大写 + 小写，共 62 字符 */
const ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
const MOD = 62
/** 拒绝 248–255，使 x % 62 在 [0,255] 上均匀（256 = 4*62 + 8） */
const REJECT_FROM = 248

/**
 * 8 位随机 id（渲染进程用 Web Crypto）；用于导出文件夹名等防重场景。
 * 与主进程 shortId8 字符集、长度一致。
 */
export function shortUuid(): string {
  let out = ''
  const buf = new Uint8Array(1)
  while (out.length < 8) {
    crypto.getRandomValues(buf)
    const x = buf[0]
    if (x < REJECT_FROM) {
      out += ALPHABET[x % MOD]
    }
  }
  return out
}
