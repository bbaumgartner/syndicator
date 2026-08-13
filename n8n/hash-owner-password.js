#!/usr/bin/env node
"use strict";

const password = process.env.N8N_OWNER_PASSWORD;
if (!password) {
  console.error("N8N_OWNER_PASSWORD is required to hash the instance owner password.");
  process.exit(1);
}

function loadBcrypt() {
  const candidates = [
    "bcryptjs",
    "bcrypt",
    "/usr/local/lib/node_modules/n8n/node_modules/bcryptjs",
    "/usr/local/lib/node_modules/n8n/node_modules/bcrypt",
  ];
  for (const id of candidates) {
    try {
      return require(id);
    } catch {
      // try the next candidate
    }
  }
  throw new Error("Unable to load bcrypt from the n8n image");
}

const bcrypt = loadBcrypt();
process.stdout.write(bcrypt.hashSync(password, 10));
