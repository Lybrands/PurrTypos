const UUID_CHARS = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'

/**
 * 生成四位短 uuid（字母+数字），用于导出文件夹名等防重场景
 */
export function shortUuid(): string {
  let id = ''
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    const arr = new Uint8Array(4)
    crypto.getRandomValues(arr)
    for (let i = 0; i < 4; i++) id += UUID_CHARS[arr[i] % UUID_CHARS.length]
  } else {
    for (let i = 0; i < 4; i++) id += UUID_CHARS[Math.floor(Math.random() * UUID_CHARS.length)]
  }
  return id
}
