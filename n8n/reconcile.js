#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const BUNDLE = process.env.SYNDICATOR_BUNDLE || "/opt/syndicator";
const STATE_FILE =
  process.env.SYNDICATOR_RECONCILE_STATE ||
  "/home/node/.n8n/.syndicator-reconcile.sha256";
const N8N_BASE = (process.env.N8N_INTERNAL_URL || "http://n8n:5678").replace(
  /\/$/,
  "",
);

function fail(message) {
  console.error(message);
  process.exit(1);
}

function runN8n(args) {
  const result = spawnSync("n8n", args, { encoding: "utf8" });
  if (result.status !== 0) {
    fail(`n8n ${args.join(" ")} failed:\n${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

function importOwned(kind, inputPath, userId) {
  const withOwner = [`import:${kind}`, `--input=${inputPath}`, `--userId=${userId}`];
  const result = spawnSync("n8n", withOwner, { encoding: "utf8" });
  if (result.status === 0) {
    return;
  }
  const msg = `${result.stderr || ""}${result.stdout || ""}`;
  if (!msg.includes("already owned")) {
    fail(`n8n ${withOwner.join(" ")} failed:\n${msg}`);
  }
  runN8n([`import:${kind}`, `--input=${inputPath}`]);
}

function listBundle(kind, suffix) {
  const dir = path.join(BUNDLE, kind);
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith(suffix))
    .sort()
    .map((name) => path.join(dir, name));
}

function fingerprint() {
  const digest = crypto.createHash("sha256");
  for (const filePath of [
    ...listBundle("credentials", ".template.json"),
    ...listBundle("workflows", ".json"),
  ]) {
    digest.update(path.relative(BUNDLE, filePath));
    digest.update(fs.readFileSync(filePath));
  }
  for (const name of ["N8N_ENCRYPTION_KEY", "OPENAI_API_KEY", "POSTIZ_API_KEY"]) {
    digest.update(name);
    digest.update(process.env[name] || "");
  }
  return digest.digest("hex");
}

function workflowFiles() {
  const files = listBundle("workflows", ".json");
  const workflows = new Map(
    files.map((filePath) => [
      filePath,
      JSON.parse(fs.readFileSync(filePath, "utf8")),
    ]),
  );
  const byId = new Map(
    [...workflows].map(([filePath, workflow]) => [workflow.id, filePath]),
  );

  function reference(node) {
    const value = node.parameters && node.parameters.workflowId;
    if (typeof value === "string") {
      return value;
    }
    if (value && typeof value.value === "string") {
      return value.value;
    }
    return null;
  }

  const ordered = [];
  const visiting = new Set();
  const visited = new Set();

  function visit(filePath) {
    if (visited.has(filePath)) {
      return;
    }
    if (visiting.has(filePath)) {
      fail(`workflow dependency cycle at ${path.basename(filePath)}`);
    }
    visiting.add(filePath);
    const dependencies = new Set();
    for (const node of workflows.get(filePath).nodes || []) {
      const dependency = reference(node);
      if (dependency) {
        dependencies.add(dependency);
      }
    }
    for (const dependency of [...dependencies].sort()) {
      if (!byId.has(dependency)) {
        fail(
          `${path.basename(filePath)} references unknown workflow ${dependency}`,
        );
      }
      visit(byId.get(dependency));
    }
    visiting.delete(filePath);
    visited.add(filePath);
    ordered.push(filePath);
  }

  for (const filePath of files) {
    visit(filePath);
  }
  return ordered;
}

function renderCredential(templatePath) {
  const raw = fs.readFileSync(templatePath, "utf8");
  const rendered = raw.replace(/\$\{([A-Z0-9_]+)\}/g, (_, key) => {
    if (process.env[key] === undefined) {
      fail(`Missing environment value for template: ${key}`);
    }
    return JSON.stringify(process.env[key]).slice(1, -1);
  });
  JSON.parse(rendered);
  return rendered;
}

function cookieHeader(res) {
  const raw =
    typeof res.headers.getSetCookie === "function"
      ? res.headers.getSetCookie()
      : [];
  const header = res.headers.get("set-cookie");
  const list = raw.length ? raw : header ? [header] : [];
  return list.map((item) => item.split(";")[0]).join("; ");
}

async function request(url, { method = "GET", headers = {}, body, cookie } = {}) {
  const res = await fetch(url, {
    method,
    headers: {
      ...headers,
      ...(cookie ? { Cookie: cookie } : {}),
    },
    body,
  });
  const text = await res.text();
  let json = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = null;
  }
  return { res, text, json, cookie: cookieHeader(res) || cookie || "" };
}

async function login() {
  const { res, json, text, cookie } = await request(`${N8N_BASE}/rest/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      emailOrLdapLoginId: process.env.N8N_OWNER_EMAIL,
      password: process.env.N8N_OWNER_PASSWORD,
    }),
  });
  if (!res.ok) {
    fail(`n8n login failed (HTTP ${res.status}): ${text}`);
  }
  const data = (json && json.data) || json || {};
  if (!data.id) {
    fail(`Login response has no owner id: ${text}`);
  }
  return { userId: data.id, cookie };
}

async function rest(cookie, method, urlPath, body) {
  return request(`${N8N_BASE}${urlPath}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    cookie,
  });
}

async function publish(cookie, id) {
  const cli = spawnSync("n8n", ["publish:workflow", `--id=${id}`], {
    encoding: "utf8",
  });
  if (cli.status === 0) {
    return;
  }

  for (const urlPath of [
    `/rest/workflows/${id}/publish`,
    `/rest/workflows/${id}/activate`,
    `/api/v1/workflows/${id}/publish`,
    `/api/v1/workflows/${id}/activate`,
  ]) {
    const result = await rest(cookie, "POST", urlPath);
    if (result.res.status === 200) {
      return;
    }
  }
  fail(
    `Failed to publish workflow ${id} (CLI: ${cli.stderr || cli.stdout})`,
  );
}

function same(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

async function workflowMatches(cookie, id, sourcePath) {
  let payload = null;
  for (const urlPath of [`/rest/workflows/${id}`, `/api/v1/workflows/${id}`]) {
    const result = await rest(cookie, "GET", urlPath);
    if (result.res.status === 200) {
      payload = (result.json && result.json.data) || result.json;
      break;
    }
  }
  if (!payload) {
    return false;
  }
  const desired = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  if (payload.active !== true) {
    return false;
  }
  return ["name", "nodes", "connections", "settings", "staticData"].every(
    (key) => same(payload[key], desired[key]),
  );
}

async function allWorkflowsCurrent(cookie, files) {
  for (const filePath of files) {
    const id = JSON.parse(fs.readFileSync(filePath, "utf8")).id;
    if (!(await workflowMatches(cookie, id, filePath))) {
      return false;
    }
  }
  return true;
}

async function main() {
  for (const name of [
    "N8N_ENCRYPTION_KEY",
    "N8N_OWNER_EMAIL",
    "N8N_OWNER_PASSWORD",
    "OPENAI_API_KEY",
    "POSTIZ_API_KEY",
  ]) {
    if (!process.env[name]) {
      fail(`Missing required environment value: ${name}`);
    }
  }

  const files = workflowFiles();
  if (!files.length) {
    fail(`No workflow exports found under ${BUNDLE}`);
  }

  const digest = fingerprint();
  const { userId, cookie } = await login();
  if (
    fs.existsSync(STATE_FILE) &&
    fs.readFileSync(STATE_FILE, "utf8").trim() === digest &&
    (await allWorkflowsCurrent(cookie, files))
  ) {
    console.log("n8n bootstrap is already current.");
    return;
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "syndicator-"));
  try {
    console.log(`Importing credentials for owner ${userId}...`);
    for (const templatePath of listBundle("credentials", ".template.json")) {
      const out = path.join(
        tmp,
        path.basename(templatePath, ".template.json") + ".json",
      );
      fs.writeFileSync(out, renderCredential(templatePath));
      importOwned("credentials", out, userId);
    }

    console.log("Importing and publishing workflows...");
    for (const filePath of files) {
      const id = JSON.parse(fs.readFileSync(filePath, "utf8")).id;
      importOwned("workflow", filePath, userId);
      await publish(cookie, id);
    }

    if (!(await allWorkflowsCurrent(cookie, files))) {
      fail("At least one imported workflow differs from source or is inactive.");
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }

  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, `${digest}\n`);
  console.log("n8n bootstrap complete.");
}

main().catch((error) => fail(error.stack || String(error)));
