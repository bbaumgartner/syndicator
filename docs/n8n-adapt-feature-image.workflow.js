import { workflow, node, trigger, ifElse, expr, newCredential } from '@n8n/workflow-sdk';

const CROP_FOCUS_PROMPT =
  'You are a photo editor preparing images for social media crops.\n\n' +
  'You get one photo. Identify the main subject (person, animal, boat, focal\n' +
  'landmark, whatever the photo is really about) and return the normalized\n' +
  'coordinates of the point the crop should be centered on:\n\n' +
  '- x: 0.0 = left edge, 1.0 = right edge\n' +
  '- y: 0.0 = top edge, 1.0 = bottom edge\n\n' +
  'If there is no clear subject (pure landscape), return the most interesting\n' +
  'area, typically near the horizon or following the rule of thirds.\n\n' +
  'Return ONLY a JSON object like {"x":0.5,"y":0.4} with no markdown.';

const PLAN_HEADER_JS =
  "function cropBox(width, height, targetRatio, focus) {\n" +
  "  const fx = focus?.x ?? 0.5;\n" +
  "  const fy = focus?.y ?? 0.5;\n" +
  "  const srcRatio = width / height;\n" +
  "  let cropW, cropH;\n" +
  "  if (srcRatio > targetRatio) { cropH = height; cropW = Math.round(height * targetRatio); }\n" +
  "  else { cropW = width; cropH = Math.round(width / targetRatio); }\n" +
  "  let left = Math.round(fx * width - cropW / 2);\n" +
  "  let top = Math.round(fy * height - cropH / 2);\n" +
  "  left = Math.min(Math.max(left, 0), width - cropW);\n" +
  "  top = Math.min(Math.max(top, 0), height - cropH);\n" +
  "  return { left, top, width: cropW, height: cropH };\n" +
  "}\n" +
  "function even(n) { return n % 2 === 0 ? n : n - 1; }\n" +
  "function fitWithoutUpscale(cropW, cropH, maxW, maxH) {\n" +
  "  if (cropW <= maxW && cropH <= maxH) return { width: cropW, height: cropH };\n" +
  "  const scale = Math.min(maxW / cropW, maxH / cropH);\n" +
  "  return { width: even(Math.floor(cropW * scale)), height: even(Math.floor(cropH * scale)) };\n" +
  "}\n" +
  "function parseFocus(raw) {\n" +
  "  try {\n" +
  "    let t = raw?.content ?? raw?.text ?? raw?.[0]?.content?.[0]?.text ?? raw?.output?.[0]?.content?.[0]?.text ?? raw;\n" +
  "    if (typeof t !== 'string') t = JSON.stringify(t);\n" +
  "    const m = t.match(/\\{[\\s\\S]*\\}/);\n" +
  "    if (!m) return { x: 0.5, y: 0.5 };\n" +
  "    const j = JSON.parse(m[0]);\n" +
  "    return { x: Number(j.x) || 0.5, y: Number(j.y) || 0.5 };\n" +
  "  } catch (e) { return { x: 0.5, y: 0.5 }; }\n" +
  "}\n" +
  "function readSize(info) {\n" +
  "  const width = Number(info?.size?.width || info?.width || info?.data?.width || 0);\n" +
  "  const height = Number(info?.size?.height || info?.height || info?.data?.height || 0);\n" +
  "  if (width > 0 && height > 0) return { width, height };\n" +
  "  const geo = String(info?.Geometry || '');\n" +
  "  const m = geo.match(/^(\\d+)x(\\d+)/);\n" +
  "  if (m) return { width: Number(m[1]), height: Number(m[2]) };\n" +
  "  throw new Error('Feature Image Info missing size: ' + JSON.stringify(Object.keys(info || {})));\n" +
  "}\n" +
  "const trigger = $('Adapt Feature Trigger').first().json;\n" +
  "const slug = String(trigger.slug || '');\n" +
  "const { width, height } = readSize($('Feature Image Info').first().json);\n" +
  "const focus = parseFocus($('Feature Crop Focus').first().json);\n" +
  "const jobs = [];\n" +
  "{\n" +
  "  const box = cropBox(width, height, 1080 / 1350, focus);\n" +
  "  const out = fitWithoutUpscale(box.width, box.height, 1080, 1350);\n" +
  "  jobs.push({ platform: 'instagram', crop: box, out, sftp_path: '/syndicator/' + slug + '/header/instagram.jpg' });\n" +
  "}\n" +
  "for (const platform of ['facebook', 'x']) {\n" +
  "  let outW = width, outH = height;\n" +
  "  const maxEdge = Math.max(width, height);\n" +
  "  if (maxEdge > 2048) {\n" +
  "    const scale = 2048 / maxEdge;\n" +
  "    outW = even(Math.floor(width * scale));\n" +
  "    outH = even(Math.floor(height * scale));\n" +
  "  }\n" +
  "  jobs.push({ platform, crop: { left: 0, top: 0, width, height }, out: { width: outW, height: outH }, sftp_path: '/syndicator/' + slug + '/header/' + platform + '.jpg' });\n" +
  "}\n" +
  "return jobs.map((j) => ({ json: j }));";

const adaptFeatureTrigger = trigger({
  type: 'n8n-nodes-base.executeWorkflowTrigger',
  version: 1.2,
  config: {
    name: 'Adapt Feature Trigger',
    parameters: {
      inputSource: 'workflowInputs',
      workflowInputs: {
        values: [
          { name: 'slug', type: 'string' },
          { name: 'header_source_sftp_path', type: 'string' },
        ],
      },
    },
    output: [{ json: { slug: 'example', header_source_sftp_path: '/syndicator/example/source/header.jpg' } }],
  },
});

const hasFeatureSource = ifElse({
  version: 2.3,
  config: {
    name: 'Has Feature Source?',
    parameters: {
      conditions: {
        combinator: 'and',
        options: { caseSensitive: true, leftValue: '', typeValidation: 'loose', version: 2 },
        conditions: [
          {
            id: 'has-header',
            leftValue: expr('{{ $json.header_source_sftp_path }}'),
            rightValue: '',
            operator: { type: 'string', operation: 'notEquals' },
          },
        ],
      },
    },
    output: [
      [{ json: { slug: 'example', header_source_sftp_path: '/syndicator/example/source/header.jpg' } }],
      [{ json: { slug: 'example', header_source_sftp_path: '' } }],
    ],
  },
});

const downloadFeatureSource = node({
  type: 'n8n-nodes-base.ftp',
  version: 1,
  config: {
    name: 'Download Feature Source',
    parameters: {
      protocol: 'sftp',
      operation: 'download',
      path: expr('{{ $json.header_source_sftp_path }}'),
      binaryPropertyName: 'data',
      options: {},
    },
    credentials: { ftp: newCredential('FTP account') },
    output: [{ json: { slug: 'example' }, binary: { data: { fileName: 'header.jpg', mimeType: 'image/jpeg' } } }],
  },
});

const featureImageInfo = node({
  type: 'n8n-nodes-base.editImage',
  version: 1,
  config: {
    name: 'Feature Image Info',
    parameters: { operation: 'information', dataPropertyName: 'data' },
    output: [{ json: { width: 2000, height: 1500 }, binary: { data: { fileName: 'header.jpg', mimeType: 'image/jpeg' } } }],
  },
});

const featureCropFocus = node({
  type: '@n8n/n8n-nodes-langchain.openAi',
  version: 2.3,
  config: {
    name: 'Feature Crop Focus',
    parameters: {
      resource: 'image',
      operation: 'analyze',
      modelId: { __rl: true, mode: 'list', value: 'gpt-5.4-mini', cachedResultName: 'GPT-5.4-MINI' },
      text: CROP_FOCUS_PROMPT,
      inputType: 'base64',
      binaryPropertyName: 'data',
      options: { detail: 'low', maxTokens: 100 },
    },
    credentials: { openAiApi: newCredential('OpenAI account') },
    output: [{ json: { content: '{"x":0.5,"y":0.4}' } }],
  },
});

const planHeaderJobs = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Plan Header Jobs',
    parameters: { mode: 'runOnceForAllItems', jsCode: PLAN_HEADER_JS },
    output: [
      {
        json: {
          platform: 'instagram',
          crop: { left: 0, top: 0, width: 1200, height: 1500 },
          out: { width: 1080, height: 1350 },
          sftp_path: '/syndicator/example/header/instagram.jpg',
        },
      },
    ],
  },
});

const reattachFeatureBinary = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Reattach Feature Binary',
    parameters: {
      mode: 'runOnceForEachItem',
      jsCode: "return { json: $input.item.json, binary: $('Download Feature Source').first().binary };",
    },
    output: [
      {
        json: {
          platform: 'instagram',
          crop: { left: 0, top: 0, width: 1200, height: 1500 },
          out: { width: 1080, height: 1350 },
          sftp_path: '/syndicator/example/header/instagram.jpg',
        },
        binary: { data: { fileName: 'header.jpg', mimeType: 'image/jpeg' } },
      },
    ],
  },
});

const editHeaderImage = node({
  type: 'n8n-nodes-base.editImage',
  version: 1,
  config: {
    name: 'Edit Header Image',
    parameters: {
      operation: 'multiStep',
      dataPropertyName: 'data',
      operations: {
        operations: [
          {
            operation: 'crop',
            width: expr('{{ $json.crop.width }}'),
            height: expr('{{ $json.crop.height }}'),
            positionX: expr('{{ $json.crop.left }}'),
            positionY: expr('{{ $json.crop.top }}'),
          },
          {
            operation: 'resize',
            width: expr('{{ $json.out.width }}'),
            height: expr('{{ $json.out.height }}'),
            resizeOption: 'ignoreAspectRatio',
          },
        ],
      },
      options: {
        format: 'jpeg',
        quality: 90,
        fileName: expr('{{ $json.platform }}.jpg'),
      },
    },
    output: [{ json: { platform: 'instagram' }, binary: { data: { fileName: 'instagram.jpg', mimeType: 'image/jpeg' } } }],
  },
});

const uploadHeader = node({
  type: 'n8n-nodes-base.ftp',
  version: 1,
  config: {
    name: 'Upload Header',
    parameters: {
      protocol: 'sftp',
      operation: 'upload',
      path: expr("{{ $('Plan Header Jobs').item.json.sftp_path }}"),
      binaryData: true,
      options: {},
    },
    credentials: { ftp: newCredential('FTP account') },
    output: [{ json: { platform: 'instagram' } }],
  },
});

const buildHeaderResult = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'Build Header Result',
    executeOnce: true,
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode:
        "const t = $('Adapt Feature Trigger').first().json;\n" +
        'const slug = t.slug;\n' +
        'return [{\n' +
        '  json: {\n' +
        '    ok: true,\n' +
        '    slug,\n' +
        '    header: {\n' +
        "      facebook: '/syndicator/' + slug + '/header/facebook.jpg',\n" +
        "      instagram: '/syndicator/' + slug + '/header/instagram.jpg',\n" +
        "      x: '/syndicator/' + slug + '/header/x.jpg',\n" +
        '    },\n' +
        '  },\n' +
        '}];',
    },
    output: [
      {
        json: {
          ok: true,
          slug: 'example',
          header: {
            facebook: '/syndicator/example/header/facebook.jpg',
            instagram: '/syndicator/example/header/instagram.jpg',
            x: '/syndicator/example/header/x.jpg',
          },
        },
      },
    ],
  },
});

const noFeaturePassthrough = node({
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {
    name: 'No Feature Passthrough',
    parameters: {
      mode: 'runOnceForAllItems',
      jsCode:
        "const t = $('Adapt Feature Trigger').first().json;\n" +
        'return [{ json: { ok: true, slug: t.slug, header: {} } }];',
    },
    output: [{ json: { ok: true, slug: 'example', header: {} } }],
  },
});

export default workflow('adapt-feature-image', 'Adapt Feature Image')
  .add(adaptFeatureTrigger)
  .to(
    hasFeatureSource
      .onTrue(
        downloadFeatureSource
          .to(featureImageInfo)
          .to(featureCropFocus)
          .to(planHeaderJobs)
          .to(reattachFeatureBinary)
          .to(editHeaderImage)
          .to(uploadHeader)
          .to(buildHeaderResult),
      )
      .onFalse(noFeaturePassthrough),
  );
