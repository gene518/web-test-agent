import { existsSync } from 'fs';
import { defineConfig } from '@playwright/test';
import process from 'process';

// 获取报告名称（时间戳+用例路径）
const reportName = getReportName();
const chromeExecutablePath = resolveChromeExecutablePath();

export default defineConfig({
  testDir: './test_case',                      // 测试用例目录
  outputDir: `test-results/${reportName}/artifacts`,  // 测试产物输出目录（按时间戳+用例路径分组）
  fullyParallel: false,                        // 禁用完全并行，确保测试按顺序执行
  forbidOnly: !!process.env.CI,                // CI 环境禁止使用 .only
  retries: 1,                                  // 用例失败后重试次数
  workers: process.env.CI ? 1 : 1,             // 单 worker 执行，避免并发问题
  reporter: [
    ['html', {
      outputFolder: `test-results/${reportName}/html-report`,
      open: 'always'
    }],
    ['list']
  ],
  use: {
    baseURL: 'https://www.jk.cn',
    trace: 'on',
    screenshot: 'on',
    video: 'on',
    actionTimeout: 30000,
    headless: false,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        // 移动端 H5 模拟：保持 isMobile + hasTouch，业务点击统一使用 tap()
        viewport: { width: 500, height: 844 },
        userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) CriOS/120.0.0.0 Mobile/15E148 Safari/604.1',
        isMobile: true,
        hasTouch: true,
        deviceScaleFactor: 2,
        headless: false,
        video: 'on',
        launchOptions: {
          executablePath: chromeExecutablePath,
          slowMo: 1000,
        },
      },
    },
  ],
});

// ============ 报告名称生成相关函数 ============

function getTestPath() {
  const args = process.argv;
  for (const arg of args) {
    if (arg.includes('test_case/')) {
      const match = arg.match(/test_case\/(.+?)(\.spec\.ts)?$/);
      if (match) {
        return match[1].replace(/\.spec\.ts$/, '').replace(/\//g, '-');
      }
    }
  }
  return 'all';
}

function getReportName() {
  if (process.env.PW_TEST_REPORT_NAME) {
    return process.env.PW_TEST_REPORT_NAME;
  }
  const now = new Date();
  const ts = [
    String(now.getFullYear()).slice(-2),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
    '-',
    String(now.getHours()).padStart(2, '0'),
    String(now.getMinutes()).padStart(2, '0'),
    String(now.getSeconds()).padStart(2, '0'),
  ].join('');
  const testPath = getTestPath();
  const generatedName = `${ts}-${testPath}`;
  process.env.PW_TEST_REPORT_NAME = generatedName;
  return generatedName;
}

function resolveChromeExecutablePath() {
  const chromePathByPlatform: Record<string, string> = {
    darwin: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    win32: 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  };

  const executablePath = chromePathByPlatform[process.platform];
  if (!executablePath) {
    throw new Error(
      `当前 demo 仅内置 darwin/win32 的 Chrome 路径，当前平台为 ${process.platform}。请按本机 Chrome 安装位置调整 playwright.config.ts。`
    );
  }

  if (!existsSync(executablePath)) {
    throw new Error(
      `未找到本地 Chrome 可执行文件：${executablePath}。请按本机 Chrome 安装位置调整 playwright.config.ts。`
    );
  }

  return executablePath;
}
