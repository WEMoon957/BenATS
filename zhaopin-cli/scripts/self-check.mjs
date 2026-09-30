/**
 * 本脚本逐项检查智联 CLI 的运行前置条件，用于快速定位「跑不起来」的原因。
 *
 * 用法（在 zhaopin-cli 目录下）：
 *   node scripts/self-check.mjs
 *
 * 检查项：Node 版本、Chrome 可执行文件、数据目录写入、浏览器启动与 CDP 连接、智联域名可达。
 * 全部通过后，再执行 `zhaopin login` 完成登录即可正常使用。
 */

import { existsSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { join } from 'node:path';
import { connectBrowser, detachBrowserSession } from '../dist/browser/index.js';

/** 打印一条检查结果。 */
function report(name, ok, detail) {
  console.log(`${ok ? '[通过]' : '[失败]'} ${name}${detail ? ` — ${detail}` : ''}`);
  return ok;
}

/** 探测本机浏览器路径，与 CLI 使用的规则保持一致。 */
function findBrowser() {
  const candidates =
    platform() === 'darwin'
      ? [
          '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
          '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
          '/Applications/Chromium.app/Contents/MacOS/Chromium',
        ]
      : platform() === 'win32'
        ? [
            join(process.env.LOCALAPPDATA ?? '', 'Google/Chrome/Application/chrome.exe'),
            join(process.env.PROGRAMFILES ?? '', 'Microsoft/Edge/Application/msedge.exe'),
          ]
        : ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/microsoft-edge'];
  return candidates.find((candidate) => candidate && existsSync(candidate));
}

/** 返回 Node 主版本号。 */
function nodeMajor() {
  return Number.parseInt(process.versions.node.split('.')[0] ?? '0', 10);
}

async function main() {
  console.log('智联 CLI 自检');
  console.log('='.repeat(50));

  let failed = 0;

  if (!report('Node 版本', nodeMajor() >= 20, `当前 ${process.versions.node}，需要 20 及以上`)) {
    failed += 1;
  }

  const browserPath = process.env.CHROME_PATH?.trim() || findBrowser();
  if (!report('浏览器可执行文件', Boolean(browserPath), browserPath || '未找到 Chrome/Edge，请设置 CHROME_PATH')) {
    failed += 1;
  }

  const cacheDir = join(homedir(), '.zhaopin-cli', '.cache');
  const probeFile = join(cacheDir, `write-probe-${Date.now()}`);
  try {
    mkdirSync(cacheDir, { recursive: true });
    writeFileSync(probeFile, 'ok');
    rmSync(probeFile, { force: true });
    report('数据目录写入', true, cacheDir);
  } catch (error) {
    report('数据目录写入', false, error instanceof Error ? error.message : String(error));
    failed += 1;
  }

  try {
    const response = await fetch('https://rd6.zhaopin.com', { method: 'HEAD' });
    report('智联域名可达', response.ok || response.status < 500, `HTTP ${response.status}`);
  } catch (error) {
    report('智联域名可达', false, error instanceof Error ? error.message : String(error));
    failed += 1;
  }

  let browser;
  try {
    browser = await connectBrowser();
    report('浏览器启动与 CDP 连接', true, `版本 ${await browser.version()}`);
    const pages = await browser.pages();
    report('页面目标创建', pages.length > 0, `当前标签数 ${pages.length}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    report('浏览器启动与 CDP 连接', false, message);
    failed += 1;
    if (message.includes('main frame too early') || message.includes('sandbox')) {
      console.log('');
      console.log('提示：浏览器进程被运行环境的沙箱拦截，渲染进程无法启动。');
      console.log('请在没有沙箱限制的普通终端里重跑本脚本。');
    }
  } finally {
    await detachBrowserSession().catch(() => undefined);
  }

  console.log('='.repeat(50));
  console.log(failed === 0 ? '全部检查通过，可以执行 zhaopin login。' : `有 ${failed} 项未通过，请按上面的提示处理。`);
  process.exitCode = failed === 0 ? 0 : 1;

  void browser;
}

main().catch((error) => {
  console.error('自检异常：', error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
