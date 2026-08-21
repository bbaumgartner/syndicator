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
  for (const name of ["N8N_ENCRYPTION_KEY", "OPENAI_API_KEY", "POSTIZ_API_KEY", "NARRAREACH_API_TOKEN"]) {
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

function templateSecrets(templatePath) {
  return [
    ...fs.readFileSync(templatePath, "utf8").matchAll(/\$\{([A-Z0-9_]+)\}/g),
  ].map((match) => match[1]);
}

function renderCredential(templatePath) {
  const raw = fs.readFileSync(templatePath, "utf8");
  const rendered = raw.replace(/\$\{([A-Z0-9_]+)\}/g, (_, key) => {
    if (!process.env[key]) {
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

const BROWSER_ID = crypto.randomUUID();

async function request(url, { method = "GET", headers = {}, body, cookie } = {}) {
  const res = await fetch(url, {
    method,
    headers: {
      "browser-id": BROWSER_ID,
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

function payloadOf(result) {
  return (result.json && result.json.data) || result.json || {};
}

async function getWorkflow(cookie, id) {
  for (const urlPath of [`/rest/workflows/${id}`, `/api/v1/workflows/${id}`]) {
    const result = await rest(cookie, "GET", urlPath);
    if (result.res.ok) {
      return payloadOf(result);
    }
  }
  return null;
}

async function postFirstOk(cookie, paths, body) {
  let last = null;
  for (const urlPath of paths) {
    const result = await rest(cookie, "POST", urlPath, body);
    last = result;
    if (result.res.ok) {
      return result;
    }
  }
  return last;
}

function isInactive(result) {
  return payloadOf(result).active === false;
}

async function unpublish(cookie, id) {
  const result = await postFirstOk(
    cookie,
    [
      `/rest/workflows/${id}/deactivate`,
      `/rest/workflows/${id}/unpublish`,
      `/api/v1/workflows/${id}/deactivate`,
      `/api/v1/workflows/${id}/unpublish`,
    ],
    {},
  );
  if (result && (result.res.ok || isInactive(result))) {
    return;
  }
  const current = await getWorkflow(cookie, id);
  if (current && current.active !== true) {
    return;
  }
  fail(
    `Failed to unpublish workflow ${id} (HTTP ${result ? result.res.status : "none"}): ${result ? result.text : ""}`,
  );
}

async function publish(cookie, id) {
  // Publish through the running n8n HTTP API so production webhooks register
  // in its live router. CLI publish from this sidecar only writes the DB.
  await unpublish(cookie, id);
  const current = await getWorkflow(cookie, id);
  if (!current || !current.versionId) {
    fail(`Workflow ${id} has no versionId after import`);
  }
  const result = await postFirstOk(
    cookie,
    [
      `/rest/workflows/${id}/activate`,
      `/rest/workflows/${id}/publish`,
      `/api/v1/workflows/${id}/activate`,
      `/api/v1/workflows/${id}/publish`,
    ],
    { versionId: current.versionId },
  );
  if (result && result.res.ok && payloadOf(result).active === true) {
    return;
  }
  fail(
    `Failed to publish workflow ${id} (HTTP ${result ? result.res.status : "none"}): ${result ? result.text : ""}`,
  );
}

async function publishAll(cookie, files) {
  for (const filePath of files) {
    await publish(cookie, JSON.parse(fs.readFileSync(filePath, "utf8")).id);
  }
}

function importWorkflow(filePath, userId, tmpDir) {
  const workflow = JSON.parse(fs.readFileSync(filePath, "utf8"));
  workflow.active = false;
  delete workflow.activeVersionId;
  const out = path.join(tmpDir, path.basename(filePath));
  fs.writeFileSync(out, `${JSON.stringify(workflow)}\n`);
  importOwned("workflow", out, userId);
}

function webhookPaths() {
  const paths = [];
  for (const filePath of listBundle("workflows", ".json")) {
    const workflow = JSON.parse(fs.readFileSync(filePath, "utf8"));
    for (const node of workflow.nodes || []) {
      if (node.type !== "n8n-nodes-base.webhook") {
        continue;
      }
      const hook = String((node.parameters && node.parameters.path) || "")
        .trim()
        .replace(/^\/+/, "");
      if (hook) {
        paths.push(hook);
      }
    }
  }
  return paths.sort();
}

function webhookIsLive(status, message) {
  if (status === 405) {
    return true;
  }
  return (
    /not registered for GET/i.test(message) ||
    /Did you mean to make a POST/i.test(message)
  );
}

async function webhooksLive() {
  for (const hook of webhookPaths()) {
    const { res, text, json } = await request(`${N8N_BASE}/webhook/${hook}`, {
      method: "GET",
    });
    const message = (json && json.message) || text || "";
    if (!webhookIsLive(res.status, message)) {
      return { ok: false, hook, status: res.status, message };
    }
  }
  return { ok: true };
}

async function assertWebhooksLive() {
  const result = await webhooksLive();
  if (result.ok) {
    return;
  }
  fail(
    `Production webhook /webhook/${result.hook} is not registered (HTTP ${result.status}): ${result.message}`,
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
    if ((await webhooksLive()).ok) {
      console.log("n8n bootstrap is already current.");
      return;
    }
    console.log(
      "Workflows are current but production webhooks are not registered; republishing...",
    );
    await publishAll(cookie, files);
    if (!(await allWorkflowsCurrent(cookie, files))) {
      fail("Republished workflows differ from source or are inactive.");
    }
    await assertWebhooksLive();
    console.log("n8n bootstrap complete.");
    return;
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "syndicator-"));
  try {
    console.log(`Importing credentials for owner ${userId}...`);
    for (const templatePath of listBundle("credentials", ".template.json")) {
      const missing = templateSecrets(templatePath).filter(
        (key) => !process.env[key],
      );
      if (missing.length) {
        console.log(
          `Skipping ${path.basename(templatePath)} (unset: ${missing.join(", ")}).`,
        );
        continue;
      }
      const out = path.join(
        tmp,
        path.basename(templatePath, ".template.json") + ".json",
      );
      fs.writeFileSync(out, renderCredential(templatePath));
      importOwned("credentials", out, userId);
    }

    console.log("Importing and publishing workflows...");
    for (const filePath of files) {
      importWorkflow(filePath, userId, tmp);
      await publish(cookie, JSON.parse(fs.readFileSync(filePath, "utf8")).id);
    }

    if (!(await allWorkflowsCurrent(cookie, files))) {
      fail("At least one imported workflow differs from source or is inactive.");
    }
    await assertWebhooksLive();
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }

  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, `${digest}\n`);
  console.log("n8n bootstrap complete.");
}

main().catch((error) => fail(error.stack || String(error)));
