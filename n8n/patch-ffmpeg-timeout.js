#!/usr/bin/env node
// n8n-nodes-ffmpeg-studio reads timeoutSeconds and never passes it to
// runFfmpeg, so every encode dies at the 300s default. Ignore the node
// setting and raise that default. Idempotent.
const fs = require('fs');
const path = require('path');

const FROM = 'timeoutMs = 300000';
const TO = 'timeoutMs = 1800000';

const root = process.argv[2];
if (!root) {
	console.error('usage: patch-ffmpeg-timeout.js <nodes-dir>');
	process.exit(1);
}

const target = path.join(
	root,
	'node_modules/n8n-nodes-ffmpeg-studio/dist/utils/ffmpeg.utils.js',
);
let text;
try {
	text = fs.readFileSync(target, 'utf8');
} catch (err) {
	console.error(`ffmpeg utils not found at ${target}: ${err.message}`);
	process.exit(1);
}

if (text.includes(TO) && !text.includes(FROM)) {
	console.log(`ffmpeg timeout already 1800s (${target})`);
	process.exit(0);
}
if (!text.includes(FROM)) {
	console.error(`unexpected ffmpeg timeout default in ${target}; refusing to leave the 300s default in place`);
	process.exit(1);
}

text = text.replaceAll(FROM, TO);
text = text.replace('default: 300000 = 5 minutes', 'default: 1800000 = 30 minutes');
fs.writeFileSync(target, text);
console.log(`Patched ffmpeg timeout to 1800s in ${target}`);
