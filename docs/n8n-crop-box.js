/**
 * Crop-box helpers for n8n Code nodes (mirrors syndicator.crop_math).
 *
 * focus: {x,y} normalized 0–1 from OpenAI crop-focus.
 * Returns {left, top, width, height} in source pixels for Edit Image / FFmpeg.
 */
function cropBox(width, height, targetRatio, focus) {
  const fx = focus?.x ?? 0.5;
  const fy = focus?.y ?? 0.5;
  const srcRatio = width / height;
  let cropW;
  let cropH;
  if (srcRatio > targetRatio) {
    cropH = height;
    cropW = Math.round(height * targetRatio);
  } else {
    cropW = width;
    cropH = Math.round(width / targetRatio);
  }
  let left = Math.round(fx * width - cropW / 2);
  let top = Math.round(fy * height - cropH / 2);
  left = Math.min(Math.max(left, 0), width - cropW);
  top = Math.min(Math.max(top, 0), height - cropH);
  return { left, top, width: cropW, height: cropH };
}

function even(n) {
  return n % 2 === 0 ? n : n - 1;
}

function fitWithoutUpscale(cropW, cropH, maxW, maxH) {
  if (cropW <= maxW && cropH <= maxH) return { width: cropW, height: cropH };
  const scale = Math.min(maxW / cropW, maxH / cropH);
  return { width: even(Math.floor(cropW * scale)), height: even(Math.floor(cropH * scale)) };
}

module.exports = { cropBox, fitWithoutUpscale, even };
