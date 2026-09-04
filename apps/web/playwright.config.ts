import {defineConfig,devices} from '@playwright/test';

export default defineConfig({
  testDir:'./e2e',
  fullyParallel:false,
  timeout:60_000,
  forbidOnly:Boolean(process.env.CI),
  retries:process.env.CI?1:0,
  workers:1,
  reporter:'line',
  use:{
    baseURL:'http://127.0.0.1:4173',
    screenshot:'only-on-failure',
    trace:'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer:{
    command:'npm run dev -- --host 127.0.0.1 --port 4173',
    url:'http://127.0.0.1:4173',
    reuseExistingServer:!process.env.CI,
    timeout:120_000,
  },
  outputDir:'../.test-artifacts/personal-career-browser-e2e',
});
