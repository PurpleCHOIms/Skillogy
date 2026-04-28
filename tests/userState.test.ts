import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  patchState,
  readState,
  stateDir,
  statePath,
  writeState,
} from "../src/infra/userState.js";

let originalXdg: string | undefined;
let scratch: string;

beforeEach(() => {
  scratch = mkdtempSync(join(tmpdir(), "skillogy-state-"));
  originalXdg = process.env.XDG_STATE_HOME;
  process.env.XDG_STATE_HOME = scratch;
});

afterEach(() => {
  if (originalXdg === undefined) delete process.env.XDG_STATE_HOME;
  else process.env.XDG_STATE_HOME = originalXdg;
  rmSync(scratch, { recursive: true, force: true });
});

describe("userState (XDG override)", () => {
  it("stateDir + statePath honor XDG_STATE_HOME", () => {
    expect(stateDir()).toBe(join(scratch, "skillogy"));
    expect(statePath()).toBe(join(scratch, "skillogy", "state.json"));
  });

  it("readState returns {} when no file exists yet", () => {
    expect(readState()).toEqual({});
  });

  it("writeState then readState round-trips", () => {
    writeState({ notifiedNeo4jDown: true, dockerMissing: true });
    expect(readState()).toEqual({
      notifiedNeo4jDown: true,
      dockerMissing: true,
    });
  });

  it("patchState merges into existing state without overwriting unrelated keys", () => {
    writeState({ notifiedNeo4jDown: true });
    const merged = patchState({ notifiedIndexingPending: true });
    expect(merged.notifiedNeo4jDown).toBe(true);
    expect(merged.notifiedIndexingPending).toBe(true);
    expect(readState()).toEqual(merged);
  });

  it("readState swallows malformed JSON and returns {}", () => {
    writeState({ notifiedNeo4jDown: true });
    // Corrupt the file
    const fs = require("node:fs") as typeof import("node:fs");
    fs.writeFileSync(statePath(), "{not-json", "utf-8");
    expect(readState()).toEqual({});
  });
});
