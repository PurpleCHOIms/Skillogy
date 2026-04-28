import { describe, expect, it } from "vitest";

import { decideColdStartUx } from "../src/adapters/hook.js";
import type { HealthReport } from "../src/infra/health.js";
import type { UserState } from "../src/infra/userState.js";

const ready: HealthReport = { status: "ready", skillCount: 42 };
const indexing: HealthReport = { status: "indexing", skillCount: 0 };
const down: HealthReport = { status: "down", skillCount: 0, errorMessage: "refused" };
const authMismatch: HealthReport = {
  status: "auth_mismatch",
  skillCount: 0,
  errorMessage: "Neo.ClientError.Security.Unauthorized",
};

describe("decideColdStartUx — ready path", () => {
  it("proceeds and clears stale notification flags", () => {
    const state: UserState = {
      notifiedNeo4jDown: true,
      notifiedIndexingPending: true,
      notifiedDockerMissing: true,
    };
    const d = decideColdStartUx(ready, state);
    expect(d.proceedWithRouting).toBe(true);
    expect(d.contextText).toBe("");
    expect(d.stateUpdate).toMatchObject({
      notifiedNeo4jDown: false,
      notifiedIndexingPending: false,
      notifiedDockerMissing: false,
    });
  });

  it("ready + clean state → no state update emitted", () => {
    const d = decideColdStartUx(ready, {});
    expect(d.proceedWithRouting).toBe(true);
    expect(d.contextText).toBe("");
    expect(d.stateUpdate).toEqual({});
  });
});

describe("decideColdStartUx — indexing path", () => {
  it("first time emits friendly message and marks notified", () => {
    const d = decideColdStartUx(indexing, {});
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toContain("indexed in the background");
    expect(d.contextText).toContain("/skillogy:status");
    expect(d.stateUpdate.notifiedIndexingPending).toBe(true);
  });

  it("subsequent calls are silent", () => {
    const d = decideColdStartUx(indexing, { notifiedIndexingPending: true });
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toBe("");
    expect(d.stateUpdate).toEqual({});
  });
});

describe("decideColdStartUx — neo4j down (no docker problem)", () => {
  it("first time emits 'starting up' message", () => {
    const d = decideColdStartUx(down, {});
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toContain("Neo4j is not reachable yet");
    expect(d.contextText).toContain("/skillogy:status");
    expect(d.stateUpdate.notifiedNeo4jDown).toBe(true);
  });

  it("subsequent calls are silent", () => {
    const d = decideColdStartUx(down, { notifiedNeo4jDown: true });
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toBe("");
  });
});

describe("decideColdStartUx — auth mismatch (stale Neo4j volume)", () => {
  it("first time emits volume-reset hint", () => {
    const d = decideColdStartUx(authMismatch, {});
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toContain("password doesn't match");
    expect(d.contextText).toContain("docker volume");
    expect(d.stateUpdate.notifiedAuthMismatch).toBe(true);
  });

  it("subsequent calls are silent", () => {
    const d = decideColdStartUx(authMismatch, { notifiedAuthMismatch: true });
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toBe("");
  });

  it("ready clears the auth-mismatch flag", () => {
    const d = decideColdStartUx(ready, { notifiedAuthMismatch: true });
    expect(d.proceedWithRouting).toBe(true);
    expect(d.stateUpdate.notifiedAuthMismatch).toBe(false);
  });
});

describe("decideColdStartUx — docker missing", () => {
  it("first time emits docker install hint", () => {
    const d = decideColdStartUx(down, { dockerMissing: true });
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toContain("Docker not found");
    expect(d.contextText).toContain("docs.docker.com");
    expect(d.contextText).toContain("NEO4J_URI");
    expect(d.stateUpdate.notifiedDockerMissing).toBe(true);
  });

  it("subsequent calls are silent (still docker missing, already notified)", () => {
    const d = decideColdStartUx(down, {
      dockerMissing: true,
      notifiedDockerMissing: true,
    });
    expect(d.proceedWithRouting).toBe(false);
    expect(d.contextText).toBe("");
  });

  it("once docker becomes available again, ready clears the docker-missing notice", () => {
    const d = decideColdStartUx(ready, { notifiedDockerMissing: true });
    expect(d.proceedWithRouting).toBe(true);
    expect(d.stateUpdate.notifiedDockerMissing).toBe(false);
  });
});
