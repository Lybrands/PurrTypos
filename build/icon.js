const path = require('node:path')
const fs = require('node:fs')
const { PNG } = require('pngjs')
const pngToIco = require('png-to-ico')

const ICON_CANVAS_SIZE = 1024
const ICON_CONTENT_SIZE = 824

function fitIconToCanvas(source, {
  canvasSize = ICON_CANVAS_SIZE,
  contentSize = ICON_CONTENT_SIZE,
} = {}) {
  if (contentSize > canvasSize) {
    throw new RangeError('Icon content cannot be larger than its canvas')
  }

  const output = new PNG({ width: canvasSize, height: canvasSize })
  const offset = Math.floor((canvasSize - contentSize) / 2)
  const scaleX = source.width / contentSize
  const scaleY = source.height / contentSize

  for (let targetY = 0; targetY < contentSize; targetY += 1) {
    const sourceY = Math.max(0, Math.min(source.height - 1, (targetY + 0.5) * scaleY - 0.5))
    const y0 = Math.floor(sourceY)
    const y1 = Math.min(source.height - 1, y0 + 1)
    const yWeight = sourceY - y0

    for (let targetX = 0; targetX < contentSize; targetX += 1) {
      const sourceX = Math.max(0, Math.min(source.width - 1, (targetX + 0.5) * scaleX - 0.5))
      const x0 = Math.floor(sourceX)
      const x1 = Math.min(source.width - 1, x0 + 1)
      const xWeight = sourceX - x0
      const samples = [
        { x: x0, y: y0, weight: (1 - xWeight) * (1 - yWeight) },
        { x: x1, y: y0, weight: xWeight * (1 - yWeight) },
        { x: x0, y: y1, weight: (1 - xWeight) * yWeight },
        { x: x1, y: y1, weight: xWeight * yWeight },
      ]

      let alpha = 0
      const premultiplied = [0, 0, 0]
      for (const sample of samples) {
        const sourceIndex = (sample.y * source.width + sample.x) * 4
        const sampleAlpha = source.data[sourceIndex + 3] / 255
        const alphaWeight = sampleAlpha * sample.weight
        alpha += alphaWeight
        for (let channel = 0; channel < 3; channel += 1) {
          premultiplied[channel] += source.data[sourceIndex + channel] * alphaWeight
        }
      }

      const outputIndex = ((targetY + offset) * canvasSize + targetX + offset) * 4
      for (let channel = 0; channel < 3; channel += 1) {
        output.data[outputIndex + channel] = alpha > 0
          ? Math.round(premultiplied[channel] / alpha)
          : 0
      }
      output.data[outputIndex + 3] = Math.round(alpha * 255)
    }
  }

  return output
}

async function generateIcons({ ifMissing = false } = {}) {
  const sourcePath = path.join(__dirname, '..', 'public', 'PurrTypos.png')
  const pngPath = path.join(__dirname, 'icon.png')
  const icoPath = path.join(__dirname, 'icon.ico')

  if (ifMissing && fs.existsSync(pngPath) && fs.existsSync(icoPath)) {
    return
  }

  if (!fs.existsSync(sourcePath)) {
    throw new Error(`PNG not found: ${sourcePath}`)
  }

  const source = PNG.sync.read(fs.readFileSync(sourcePath))
  const icon = fitIconToCanvas(source)
  fs.writeFileSync(pngPath, PNG.sync.write(icon))
  fs.writeFileSync(icoPath, await pngToIco(pngPath))
  console.log('Generated build/icon.png and build/icon.ico')
}

if (require.main === module) {
  generateIcons({ ifMissing: process.argv.includes('--if-missing') }).catch((error) => {
    console.error('Icon generation failed:', error)
    process.exitCode = 1
  })
}

module.exports = {
  ICON_CANVAS_SIZE,
  ICON_CONTENT_SIZE,
  fitIconToCanvas,
  generateIcons,
}
