import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    env: {
      NODE_ENV: "test",
      DATABASE_URL: process.env.TEST_DATABASE_URL ?? "postgres://hillsignal:hillsignal@localhost:5432/hillsignal_test",
      RTDN_SHARED_SECRET: "test-rtdn-secret",
      ANTHROPIC_API_KEY: "",
      FREE_DELAY_DAYS: "3",
    },
    globalSetup: ["test/globalSetup.ts"],
    fileParallelism: false, // tests share one database
  },
});
