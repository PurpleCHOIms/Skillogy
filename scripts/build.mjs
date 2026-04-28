#!/usr/bin/env node
// Bundle each Skillogy entrypoint into a self-contained ESM file with all
// runtime deps inlined. Output goes to dist/ in the same tree shape that the
// plugin manifest, hooks, slash commands, and bin entry already reference.
//
// Why bundle?
//   The Claude Code plugin install delivers the repo via `git clone`, so
//   shipping a bundled dist/ in git means OSS users do not have to run
//   `npm install` (which exceeds the SessionStart 30s timeout for a cold
//   first run with @anthropic-ai/sdk + neo4j-driver et al.).
//
// External deps:
//   Only Node built-ins are external. Everything else is inlined so the
//   bundles run without node_modules. The Anthropic Claude Agent SDK uses a
//   subprocess (`claude`) provided by Claude Code itself, not by node_modules,
//   so bundling the SDK wrapper is fine.
import { build } from "esbuild";
import { mkdirSync, readFileSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pkg = JSON.parse(readFileSync(resolve(root, "package.json"), "utf-8"));

// External-mark every runtime dependency. The plugin runtime needs them
// resolved from ${CLAUDE_PLUGIN_DATA}/node_modules at runtime via NODE_PATH —
// inlining @anthropic-ai/claude-agent-sdk in particular breaks because esbuild
// can't bundle the optional native .node binaries.
const externals = Object.keys(pkg.dependencies ?? {});

const entries = [
  { in: "src/cli/index.ts",          out: "dist/cli/index.js"           },
  { in: "src/adapters/hook.ts",      out: "dist/adapters/hook.js"       },
  { in: "src/adapters/mcp_server.ts", out: "dist/adapters/mcp_server.js" },
  { in: "src/adapters/web_api.ts",   out: "dist/adapters/web_api.js"    },
];

mkdirSync(resolve(root, "dist"), { recursive: true });

const builds = entries.map(async (e) => {
  await build({
    entryPoints: [resolve(root, e.in)],
    outfile: resolve(root, e.out),
    bundle: true,
    platform: "node",
    format: "esm",
    target: "node20",
    // Sourcemaps quadruple the shipped bundle size; opt-in via env for dev.
    sourcemap: process.env.SKILLOGY_BUILD_SOURCEMAP === "1",
    minify: process.env.SKILLOGY_BUILD_MINIFY === "1",
    legalComments: "none",
    logLevel: "warning",
    external: externals,
    banner: {
      // Provide require() inside ESM so transitively-bundled CJS deps
      // (neo4j-driver internals, etc.) keep working under --format=esm.
      js: "import { createRequire as __skillogyCreateRequire } from 'node:module';\nconst require = __skillogyCreateRequire(import.meta.url);",
    },
  });
  const size = statSync(resolve(root, e.out)).size;
  console.log(`  ${e.out}  ${(size / 1024).toFixed(1)} KB`);
});

await Promise.all(builds);
console.log("Skillogy bundles built.");
