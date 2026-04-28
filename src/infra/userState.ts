// Tiny persistent state file used by the UserPromptSubmit hook to remember
// whether one-time UX messages have already been shown to the user. This
// keeps cold-start failure modes (Neo4j down, indexing pending, docker
// missing) from flooding every prompt with the same diagnostic.
//
// State lives at $XDG_STATE_HOME/skillogy/state.json, or
// ~/.local/state/skillogy/state.json as fallback. JSON is small enough that
// sync I/O on the hot path is fine (<1ms).

import {
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export interface UserState {
  notifiedNeo4jDown?: boolean;
  notifiedIndexingPending?: boolean;
  notifiedDockerMissing?: boolean;
  notifiedAuthMismatch?: boolean;
  dockerMissing?: boolean;
}

export function stateDir(): string {
  const xdg = process.env.XDG_STATE_HOME;
  if (xdg && xdg.trim().length > 0) {
    return join(xdg, "skillogy");
  }
  return join(homedir(), ".local", "state", "skillogy");
}

export function statePath(): string {
  return join(stateDir(), "state.json");
}

export function readState(): UserState {
  const path = statePath();
  if (!existsSync(path)) return {};
  try {
    const raw = readFileSync(path, "utf-8");
    const parsed = JSON.parse(raw) as UserState;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function writeState(state: UserState): void {
  const dir = dirname(statePath());
  mkdirSync(dir, { recursive: true });
  writeFileSync(statePath(), JSON.stringify(state, null, 2), "utf-8");
}

export function patchState(patch: Partial<UserState>): UserState {
  const current = readState();
  const merged = { ...current, ...patch };
  writeState(merged);
  return merged;
}
