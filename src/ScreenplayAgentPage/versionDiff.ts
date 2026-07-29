export type ScreenplayDiffLineType = 'same' | 'added' | 'removed'

export interface ScreenplayDiffLine {
  type: ScreenplayDiffLineType
  text: string
  oldLine: number | null
  newLine: number | null
}

const MAX_LCS_CELLS = 250_000

function linesOf(value: string): string[] {
  return value.replace(/\r\n?/g, '\n').split('\n')
}

function coarseDiff(oldLines: string[], newLines: string[]): ScreenplayDiffLine[] {
  let prefix = 0
  while (
    prefix < oldLines.length
    && prefix < newLines.length
    && oldLines[prefix] === newLines[prefix]
  ) prefix += 1

  let suffix = 0
  while (
    suffix < oldLines.length - prefix
    && suffix < newLines.length - prefix
    && oldLines[oldLines.length - 1 - suffix] === newLines[newLines.length - 1 - suffix]
  ) suffix += 1

  const result: ScreenplayDiffLine[] = []
  for (let index = 0; index < prefix; index += 1) {
    result.push({
      type: 'same',
      text: oldLines[index],
      oldLine: index + 1,
      newLine: index + 1,
    })
  }
  for (let index = prefix; index < oldLines.length - suffix; index += 1) {
    result.push({
      type: 'removed',
      text: oldLines[index],
      oldLine: index + 1,
      newLine: null,
    })
  }
  for (let index = prefix; index < newLines.length - suffix; index += 1) {
    result.push({
      type: 'added',
      text: newLines[index],
      oldLine: null,
      newLine: index + 1,
    })
  }
  for (let offset = suffix; offset > 0; offset -= 1) {
    const oldIndex = oldLines.length - offset
    const newIndex = newLines.length - offset
    result.push({
      type: 'same',
      text: oldLines[oldIndex],
      oldLine: oldIndex + 1,
      newLine: newIndex + 1,
    })
  }
  return result
}

export function buildScreenplayLineDiff(
  oldValue: string,
  newValue: string,
): ScreenplayDiffLine[] {
  const oldLines = linesOf(oldValue)
  const newLines = linesOf(newValue)
  if (oldLines.length * newLines.length > MAX_LCS_CELLS) {
    return coarseDiff(oldLines, newLines)
  }

  const table = Array.from(
    { length: oldLines.length + 1 },
    () => new Uint32Array(newLines.length + 1),
  )
  for (let oldIndex = oldLines.length - 1; oldIndex >= 0; oldIndex -= 1) {
    for (let newIndex = newLines.length - 1; newIndex >= 0; newIndex -= 1) {
      table[oldIndex][newIndex] = oldLines[oldIndex] === newLines[newIndex]
        ? table[oldIndex + 1][newIndex + 1] + 1
        : Math.max(table[oldIndex + 1][newIndex], table[oldIndex][newIndex + 1])
    }
  }

  const result: ScreenplayDiffLine[] = []
  let oldIndex = 0
  let newIndex = 0
  while (oldIndex < oldLines.length || newIndex < newLines.length) {
    if (
      oldIndex < oldLines.length
      && newIndex < newLines.length
      && oldLines[oldIndex] === newLines[newIndex]
    ) {
      result.push({
        type: 'same',
        text: oldLines[oldIndex],
        oldLine: oldIndex + 1,
        newLine: newIndex + 1,
      })
      oldIndex += 1
      newIndex += 1
    } else if (
      newIndex < newLines.length
      && (
        oldIndex >= oldLines.length
        || table[oldIndex][newIndex + 1] > table[oldIndex + 1][newIndex]
      )
    ) {
      result.push({
        type: 'added',
        text: newLines[newIndex],
        oldLine: null,
        newLine: newIndex + 1,
      })
      newIndex += 1
    } else {
      result.push({
        type: 'removed',
        text: oldLines[oldIndex],
        oldLine: oldIndex + 1,
        newLine: null,
      })
      oldIndex += 1
    }
  }
  return result
}
