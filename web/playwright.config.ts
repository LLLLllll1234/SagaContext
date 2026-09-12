import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:37782",
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `PYTHONPATH=../src:.. ${process.env.CONSOLE_PYTHON ?? "python3"} -m scripts.serve_console_fixture --port 37782`,
    url: "http://127.0.0.1:37782/console/",
    reuseExistingServer: false,
  },
});
