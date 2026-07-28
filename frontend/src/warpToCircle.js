const SQRT2 = Math.sqrt(2)

// Inverse elliptical grid mapping: given a point (x, y) in the unit disc,
// find the corresponding point (u, v) in the unit square that the forward
// mapping x = u*sqrt(1 - v^2/2), y = v*sqrt(1 - u^2/2) would have produced.
// `strength` blends toward that mapping (0 = untouched square, 1 = the true
// mapping); values above 1 extrapolate past it for a more exaggerated pull.
function discToSquare(x, y, strength = 1) {
  const x2 = x * x
  const y2 = y * y

  const u = 0.5 * Math.sqrt(Math.max(0, 2 + x2 - y2 + 2 * x * SQRT2)) -
    0.5 * Math.sqrt(Math.max(0, 2 + x2 - y2 - 2 * x * SQRT2))
  const v = 0.5 * Math.sqrt(Math.max(0, 2 - x2 + y2 + 2 * y * SQRT2)) -
    0.5 * Math.sqrt(Math.max(0, 2 - x2 + y2 - 2 * y * SQRT2))

  return [
    x + (u - x) * strength,
    y + (v - y) * strength,
  ]
}

// Draws `image` into `canvas` warped so the full square image fills a circle
// (corners stretched inward) instead of being cropped to one.
export function warpSquareToCircle(image, canvas, size, strength = 1.2) {
  canvas.width = size
  canvas.height = size

  const srcCanvas = document.createElement('canvas')
  srcCanvas.width = size
  srcCanvas.height = size
  const srcCtx = srcCanvas.getContext('2d')
  srcCtx.drawImage(image, 0, 0, size, size)
  const srcData = srcCtx.getImageData(0, 0, size, size).data

  const ctx = canvas.getContext('2d')
  const outImage = ctx.createImageData(size, size)
  const out = outImage.data
  const radius = size / 2

  for (let py = 0; py < size; py++) {
    const ny = (py - radius) / radius
    for (let px = 0; px < size; px++) {
      const nx = (px - radius) / radius
      if (nx * nx + ny * ny > 1) continue // outside the circle: leave transparent

      const [u, v] = discToSquare(nx, ny, strength)
      const sx = Math.min(size - 1, Math.max(0, Math.round((u * 0.5 + 0.5) * (size - 1))))
      const sy = Math.min(size - 1, Math.max(0, Math.round((v * 0.5 + 0.5) * (size - 1))))

      const outIdx = (py * size + px) * 4
      const srcIdx = (sy * size + sx) * 4
      out[outIdx] = srcData[srcIdx]
      out[outIdx + 1] = srcData[srcIdx + 1]
      out[outIdx + 2] = srcData[srcIdx + 2]
      out[outIdx + 3] = 255
    }
  }

  ctx.putImageData(outImage, 0, 0)
}
