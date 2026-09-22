import { test, describe } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_URL = process.env.FRONTEND_TEST_URL || "http://localhost:3000";
const BACKEND_URL = process.env.BACKEND_TEST_URL || "http://127.0.0.1:8000";

describe("Frontend Auth Proxy & Secret Isolation", () => {
  test("Direct backend call without X-API-Key returns 401", async () => {
    const res = await fetch(`${BACKEND_URL}/workflows`);
    assert.equal(res.status, 401, "Backend must return 401 without X-API-Key");
  });

  test("Proxy /api/backend/health forwards and returns 200", async () => {
    const res = await fetch(`${FRONTEND_URL}/api/backend/health`);
    assert.equal(res.status, 200, "Proxy health check must return 200");
    const json = await res.json();
    assert.equal(json.status, "ok");
  });

  test("Proxy /api/backend/workflows attaches server key and returns 200", async () => {
    const res = await fetch(`${FRONTEND_URL}/api/backend/workflows`);
    assert.equal(res.status, 200, "Proxy workflows list must return 200");
    const workflows = await res.json();
    assert.ok(Array.isArray(workflows), "Expected array of workflows");
  });

  test("Proxy workflow dispatch (POST /api/backend/workflow/start) succeeds", async () => {
    const res = await fetch(`${FRONTEND_URL}/api/backend/workflow/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repo_url: "https://github.com/octocat/Hello-World",
        task: "Node test proxy run",
        dry_run: true,
      }),
    });
    assert.equal(res.status, 201, "Workflow start must return 201 Created");
    const data = await res.json();
    assert.ok(data.workflow_id, "Response must include workflow_id");
  });

  test("Client bundles (.next/static) contain ZERO secret API keys", () => {
    const staticDir = path.resolve(__dirname, "../.next/static");
    assert.ok(fs.existsSync(staticDir), ".next/static must exist after npm run build");

    const forbiddenKeys = [
      "test-api-key",
      "tenant-a-secret-key-12345",
      "tenant-b-secret-key-67890",
      "admin-secret-key",
    ];

    function scanDir(dir) {
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          scanDir(fullPath);
        } else if (entry.isFile() && entry.name.endsWith(".js")) {
          const content = fs.readFileSync(fullPath, "utf8");
          for (const key of forbiddenKeys) {
            assert.ok(
              !content.includes(key),
              `SECURITY BREACH: Found secret key '${key}' in client bundle: ${fullPath}`
            );
          }
        }
      }
    }

    scanDir(staticDir);
  });
});
