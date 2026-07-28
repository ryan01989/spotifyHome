import { useEffect, useRef } from 'react'
import { warpSquareToCircle } from './warpToCircle'

export default function WarpedCover({ src, size = 720, className, style }) {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!src || !canvas) return

    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        warpSquareToCircle(img, canvas, size)
      } catch (err) {
        // Cover art host didn't allow cross-origin pixel reads
        // fall back to a plain center crop instead of a blank circle.
        console.error('Cover warp failed, falling back to a cropped circle:', err)
        canvas.width = size
        canvas.height = size
        const ctx = canvas.getContext('2d')
        ctx.save()
        ctx.beginPath()
        ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2)
        ctx.clip()
        ctx.drawImage(img, 0, 0, size, size)
        ctx.restore()
      }
    }
    img.src = src
  }, [src, size])

  return <canvas ref={canvasRef} className={className} style={style} />
}
