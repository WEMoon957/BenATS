/** 本文件负责启动本机 Chrome 并通过 CDP 建立连接。 */

import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import puppeteer, { type Browser } from 'puppeteer-core';
import {
  BROWSER_USER_DATA_DIR,
  REMOTE_DEBUGGING_PORT,
  RESUME_DOWNLOADS_DIR,
  ensureAppDataLayout,
} from '../config.js';

/** Chrome 启动日志中的 CDP WebSocket 地址（可能在 stdout 或 stderr）。 */
const CDP_WEBSOCKET_ENDPOINT_REGEX = /^DevTools listening on (ws:\/\/.*)$/;

/** 等待 Chrome 输出 DevTools 地址的上限。 */
const LAUNCH_READY_MS = 30_000;

/** 让浏览器用临时密钥而非系统钥匙串保存 Cookie 的启动参数。 */
const MOCK_KEYCHAIN_ARGS = new Set(['--use-mock-keychain', '--password-store=basic']);

let spawnedChromeChild: ChildProcess | null = null;

export type ConnectBrowserOptions = {
  /** 浏览器可执行文件路径；未传时按环境变量与常见安装位置探测。 */
  executablePath?: string;
  /** 复用登录态的用户数据目录。 */
  userDataDir?: string;
  /** 是否无头；默认有界面。 */
  headless?: boolean;
};

/** 清空当前进程记录的 Chrome 子进程引用。 */
export function clearSpawnedChromeProcessRef(): void {
  spawnedChromeChild = null;
}

/** 在未配置路径时，按系统探测常见 Chrome/Edge/Chromium 安装位置。 */
function findLocalChromiumExecutable(): string | undefined {
  const candidates: string[] = [];
  if (process.platform === 'win32') {
    const local = process.env.LOCALAPPDATA;
    const pf = process.env.PROGRAMFILES;
    const pf86 = process.env['PROGRAMFILES(X86)'];
    if (local) candidates.push(path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'));
    if (pf) {
      candidates.push(path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'));
      candidates.push(path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe'));
    }
    if (pf86) {
      candidates.push(path.join(pf86, 'Google', 'Chrome', 'Application', 'chrome.exe'));
      candidates.push(path.join(pf86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'));
    }
  } else if (process.platform === 'darwin') {
    candidates.push(
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      '/Applications/Chromium.app/Contents/MacOS/Chromium',
    );
  } else {
    candidates.push(
      '/usr/bin/google-chrome-stable',
      '/usr/bin/google-chrome',
      '/usr/bin/chromium',
      '/usr/bin/chromium-browser',
      '/usr/bin/microsoft-edge-stable',
      '/usr/bin/microsoft-edge',
    );
  }
  return candidates.find((candidate) => existsSync(candidate));
}

/** 探测固定调试端口是否已有可复用的 Chrome，命中则返回其 CDP 地址。 */
async function probeRemoteDebuggingWsEndpoint(
  port: number,
  timeoutMs: number,
): Promise<string | undefined> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/json/version`, {
      signal: controller.signal,
    });
    if (!response.ok) return undefined;
    const data = (await response.json()) as { webSocketDebuggerUrl?: string };
    const ws = data.webSocketDebuggerUrl;
    return typeof ws === 'string' && ws.length > 0 ? ws : undefined;
  } catch {
    return undefined;
  } finally {
    clearTimeout(timer);
  }
}

/** 从 Chrome 进程输出中解析出 CDP WebSocket 地址。 */
function waitForDevToolsWebSocketUrl(
  proc: ChildProcess,
  userDataDir: string,
  timeoutMs: number,
): Promise<string> {
  const streams = [proc.stdout, proc.stderr].filter(
    (stream): stream is NonNullable<typeof stream> => stream != null,
  );
  if (streams.length === 0) {
    return Promise.reject(new Error('浏览器子进程无 stdout/stderr，无法获取 CDP 地址'));
  }

  return new Promise((resolve, reject) => {
    const readers: readline.Interface[] = [];
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const cleanup = () => {
      for (const reader of readers) {
        try {
          reader.close();
        } catch {
          /* 忽略关闭失败 */
        }
      }
      readers.length = 0;
    };

    const finish = (action: () => void) => {
      if (settled) return;
      settled = true;
      if (timer !== undefined) {
        clearTimeout(timer);
        timer = undefined;
      }
      proc.off('exit', onExit);
      proc.off('error', onProcessError);
      cleanup();
      action();
    };

    timer = setTimeout(() => {
      finish(() => reject(new Error(`等待 Chrome 输出 DevTools 地址超时（${timeoutMs}ms）`)));
    }, timeoutMs);

    const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
      finish(() =>
        reject(
          new Error(
            code === 0
              ? `浏览器进程立即以代码 0 退出：user-data-dir「${userDataDir}」可能正被另一只没有调试端口的 Chrome 占用。请关闭占用该目录的 Chrome 后重试。`
              : `浏览器进程在就绪前退出（代码 ${code ?? 'unknown'}${signal ? `，信号 ${signal}` : ''}）。若伴随 sandbox initialization failed，说明当前运行环境阻止了浏览器的子进程沙箱，请改用没有沙箱限制的终端运行。`,
          ),
        ),
      );
    };

    const onProcessError = (error: Error) => finish(() => reject(error));

    const onLine = (line: string) => {
      const matched = line.trim().match(CDP_WEBSOCKET_ENDPOINT_REGEX);
      if (matched?.[1]) {
        finish(() => resolve(matched[1]!));
      }
    };

    proc.once('exit', onExit);
    proc.once('error', onProcessError);
    for (const stream of streams) {
      const reader = readline.createInterface(stream);
      readers.push(reader);
      reader.on('line', onLine);
    }
  });
}

/**
 * 连接本机 Chrome。
 *
 * 可用环境变量：
 * - `CHROME_PATH` / `PUPPETEER_EXECUTABLE_PATH` — 指定浏览器可执行文件
 * - `ZHAOPIN_BROWSER_USER_DATA_DIR` — 复用登录态的用户数据目录
 * - `ZHAOPIN_BROWSER_REMOTE_DEBUGGING_PORT` — 远程调试端口（默认 53471）
 * - `ZHAOPIN_BROWSER_HEADLESS` — 设为 true 时无头运行，默认有界面
 *
 * 优先复用固定端口上已在运行的实例，未命中时自行启动；退出时只断开 CDP，不关浏览器窗口。
 */
export async function connectBrowser(options: ConnectBrowserOptions = {}): Promise<Browser> {
  const executablePath =
    options.executablePath?.trim() ||
    process.env.CHROME_PATH?.trim() ||
    process.env.PUPPETEER_EXECUTABLE_PATH?.trim() ||
    findLocalChromiumExecutable();

  const userDataDir =
    options.userDataDir?.trim() ||
    process.env.ZHAOPIN_BROWSER_USER_DATA_DIR?.trim() ||
    BROWSER_USER_DATA_DIR;

  if (!executablePath) {
    throw new Error(
      '未找到本机 Chrome/Edge：请设置 CHROME_PATH 或 PUPPETEER_EXECUTABLE_PATH 指向可执行文件。',
    );
  }

  if (!process.env.ZHAOPIN_BROWSER_USER_DATA_DIR?.trim()) {
    ensureAppDataLayout();
  }

  const headless = options.headless ?? process.env.ZHAOPIN_BROWSER_HEADLESS === 'true';
  const useSystemKeychain = process.env.ZHAOPIN_BROWSER_SYSTEM_KEYCHAIN === 'true';
  const noSandbox = process.env.ZHAOPIN_BROWSER_NO_SANDBOX === 'true';

  const extraArgs = ['--disable-infobars'];
  if (noSandbox) {
    extraArgs.push('--no-sandbox', '--disable-setuid-sandbox');
  }

  clearSpawnedChromeProcessRef();

  // 附件简历等文件下载统一落盘到 `~/.zhaopin-cli/downloads/`，供下载类命令读取。
  mkdirSync(RESUME_DOWNLOADS_DIR, { recursive: true });
  const downloadBehavior = { policy: 'allow', downloadPath: RESUME_DOWNLOADS_DIR } as const;

  // 优先复用固定端口上已在跑的 Chrome，跨命令保持同一登录态与同一标签页。
  const existingWsUrl = await probeRemoteDebuggingWsEndpoint(REMOTE_DEBUGGING_PORT, 800);
  if (existingWsUrl) {
    return await puppeteer.connect({
      browserWSEndpoint: existingWsUrl,
      defaultViewport: null,
      downloadBehavior,
    });
  }

  const chromeArgs = puppeteer
    .defaultArgs({ browser: 'chrome', userDataDir, headless, args: extraArgs })
    .filter((arg) => {
      if (arg === '--enable-automation' || arg === 'about:blank' || arg === 'data:,') {
        return false;
      }
      // puppeteer 默认用 mock keychain 加密 Cookie，导致登录态无法被其它 Chromium
      // 工具读取；需要与外部工具共享登录态时，用环境变量切到系统钥匙串。
      if (useSystemKeychain && MOCK_KEYCHAIN_ARGS.has(arg)) {
        return false;
      }
      return true;
    });

  if (!chromeArgs.some((arg) => arg.startsWith('--remote-debugging-'))) {
    chromeArgs.push(`--remote-debugging-port=${REMOTE_DEBUGGING_PORT}`);
  }

  // 不用 puppeteer.launch()：它会在 Node 退出时连带杀掉浏览器；改为自行 spawn + connect。
  const proc = spawn(executablePath, chromeArgs, {
    detached: true,
    env: process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  spawnedChromeChild = proc;

  let wsUrl: string;
  try {
    wsUrl = await waitForDevToolsWebSocketUrl(proc, userDataDir, LAUNCH_READY_MS);
  } catch (error) {
    try {
      proc.kill();
    } catch {
      /* 忽略终止失败 */
    }
    clearSpawnedChromeProcessRef();
    throw error;
  }

  try {
    proc.stdout?.resume();
    proc.stderr?.resume();
  } catch {
    /* 忽略 */
  }

  if (proc.exitCode === null && proc.signalCode === null) {
    try {
      proc.unref();
    } catch {
      /* 忽略 */
    }
  } else {
    clearSpawnedChromeProcessRef();
  }

  try {
    return await puppeteer.connect({
      browserWSEndpoint: wsUrl,
      defaultViewport: null,
      downloadBehavior,
    });
  } catch (error) {
    try {
      proc.kill();
    } catch {
      /* 忽略终止失败 */
    }
    clearSpawnedChromeProcessRef();
    throw error;
  }
}
