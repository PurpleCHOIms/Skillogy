#!/usr/bin/env node
// Tiny standalone state mutator used by skillogy-bootstrap.sh to record
// environment facts (e.g. docker-missing) the hook needs to surface as
// one-time UX messages. Pure node:* dependency so it works without
// node_modules.
//
// Usage:
//   node scripts/state-set.mjs <key> <value>
// where <value> is "1" (sets true), "0" (deletes the key), and matching
// notification flags are reset so the hook can re-notify on the new state.
//
// Keep keys mirrored with src/infra/userState.ts.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const [, , key, value] = process.argv;
if (!key || (value !== "0" && value !== "1")) {
  console.error("usage: node scripts/state-set.mjs <key> <0|1>");
  process.exit(2);
}

const dir = process.env.XDG_STATE_HOME
  ? join(process.env.XDG_STATE_HOME, "skillogy")
  : join(homedir(), ".local", "state", "skillogy");
const file = join(dir, "state.json");

let state = {};
if (existsSync(file)) {
  try {
    state = JSON.parse(readFileSync(file, "utf-8")) ?? {};
  } catch {
    state = {};
  }
}

if (key === "docker-missing") {
  if (value === "1") state.dockerMissing = true;
  else delete state.dockerMissing;
  // Always reset the notification flag so the hook re-emits the message after
  // the underlying environment changes.
  delete state.notifiedDockerMissing;
} else {
  console.error(`unknown key: ${key}`);
  process.exit(2);
}

mkdirSync(dir, { recursive: true });
writeFileSync(file, JSON.stringify(state, null, 2));
