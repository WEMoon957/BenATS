#!/usr/bin/env node
/** 本文件是 zhaopin-cli 的可执行入口。 */

import { detachBrowserSession } from '../browser/index.js';
import { runCli } from './cliRouter.js';

async function main(): Promise<void> {
  try {
    await runCli(process.argv.slice(2));
  } finally {
    // 只断开 CDP 连接，不关闭浏览器窗口：登录态得以保留，下次命令通过固定端口复用。
    await detachBrowserSession().catch(() => undefined);
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
