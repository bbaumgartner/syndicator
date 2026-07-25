function even(n) {
  const x = Math.floor(Number(n) || 0);
  return x % 2 === 0 ? x : x - 1;
}

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
  cropW = even(cropW);
  cropH = even(cropH);
  let left = Math.round(fx * width - cropW / 2);
  let top = Math.round(fy * height - cropH / 2);
  left = Math.min(Math.max(left, 0), Math.max(width - cropW, 0));
  top = Math.min(Math.max(top, 0), Math.max(height - cropH, 0));
  left = even(left);
  top = even(top);
  return { left, top, width: cropW, height: cropH };
}

function fitWithoutUpscale(cropW, cropH, maxW, maxH) {
  if (cropW <= maxW && cropH <= maxH) return { width: even(cropW), height: even(cropH) };
  const scale = Math.min(maxW / cropW, maxH / cropH);
  return { width: even(Math.floor(cropW * scale)), height: even(Math.floor(cropH * scale)) };
}

function parseFocus(raw) {
  try {
    let t = raw?.content ?? raw?.text ?? raw?.[0]?.content?.[0]?.text ?? raw?.output?.[0]?.content?.[0]?.text ?? raw;
    if (typeof t !== 'string') t = JSON.stringify(t);
    const m = t.match(/\{[\s\S]*\}/);
    if (!m) return { x: 0.5, y: 0.5 };
    const j = JSON.parse(m[0]);
    return { x: Number(j.x) || 0.5, y: Number(j.y) || 0.5 };
  } catch (e) {
    return { x: 0.5, y: 0.5 };
  }
}

// ffmpeg-studio Analyze returns { video: { width, height }, raw: { streams } }
function pickMeta(meta) {
  const directW = Number(meta?.video?.width ?? meta?.width ?? 0);
  const directH = Number(meta?.video?.height ?? meta?.height ?? 0);
  if (directW > 0 && directH > 0) return { width: directW, height: directH };

  const streams = meta?.raw?.streams || meta?.streams || [];
  const v = streams.find((s) => (s.codec_type || s.codecType) === 'video') || streams[0] || {};
  const width = Number(v?.width ?? 0);
  const height = Number(v?.height ?? 0);
  if (!width || !height) {
    throw new Error('Analyze Source missing video dimensions: ' + JSON.stringify(Object.keys(meta || {})));
  }
  return { width, height };
}

// Hardcoded 4:5 social reel. Later add a parallel 9:16 Shorts encode if needed.
const VARIANT = '4x5';
const OUT_W = 1080;
const OUT_H = 1350;

const trigger = $('Adapt Reel Trigger').first().json;
const slug = String(trigger.slug || '');
const index = Number(trigger.index || 1);
const sourcePath = String($('Resolve Paths').first().json.source_local || '');
const meta = pickMeta($('Analyze Source').first().json);
const focus = parseFocus($('Crop Focus').first().json);

const box = cropBox(meta.width, meta.height, OUT_W / OUT_H, focus);
const out = fitWithoutUpscale(box.width, box.height, OUT_W, OUT_H);
if (!box.width || !box.height || !out.width || !out.height) {
  throw new Error('Invalid crop/out for ' + meta.width + 'x' + meta.height + ': ' + JSON.stringify({ box, out }));
}
const root = sourcePath.replace(/\/[^/]+$/, '');
const safeSlug = slug.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 60);
const video_4x5_local = `${root}/syndicator-video-${safeSlug}-${index}-${VARIANT}.mp4`;
const video_4x5_sftp = `/syndicator/${slug}/reels/${VARIANT}/${index}.mp4`;
const vf = `crop=${box.width}:${box.height}:${box.left}:${box.top},scale=${out.width}:${out.height}`;
const customArgs = `-y -i ${sourcePath} -vf "${vf}" -c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart ${video_4x5_local}`;

return [{ json: { slug, index, video_4x5_local, video_4x5_sftp, customArgs, crop: box, out, source: meta } }];
