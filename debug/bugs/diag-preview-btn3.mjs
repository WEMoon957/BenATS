// 诊断 v3：直接调 runDownloadResume，监听新 target/下载，观察「点击预览」后的真实行为
import { createRequire } from "module";
const bossclidir = "C:/Users/李俊颖/AppData/Roaming/npm/node_modules/@joohw/boss-cli";
const require = createRequire(`${bossclidir}/package.json`);
const puppeteer = require("puppeteer-core");
const { withBossSessionPage } = require(`${bossclidir}/dist/common/boss_session_page.js`);
const { runDownloadResume } = require(`${bossclidir}/dist/toolset/download-resume.js`);

async function main() {
  const version = await (await fetch("http://127.0.0.1:53470/json/version")).json();
  const browser = await puppeteer.connect({ browserWSEndpoint: version.webSocketDebuggerUrl, defaultViewport: null });

  const created = [];
  browser.on("targetcreated", (t) => created.push(`${t.type()}:${t.url().slice(0, 100)}`));

  try {
    const result = await withBossSessionPage(async (page) => {
      return await runDownloadResume(page, "王俊力");
    });
    console.log("下载结果:", result);
  } catch (e) {
    console.log("下载报错:", e.message);
  }

  await new Promise((r) => setTimeout(r, 3000));
  console.log("新 target:", created.join(" | ") || "(无)");
  const allPages = await browser.pages();
  console.log("所有页面:", allPages.map((p) => p.url().slice(0, 120)).join(" | "));
  await browser.disconnect();
}
main().catch((e) => { console.error("失败:", e.message); process.exit(1); });