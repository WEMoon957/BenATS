// 诊断 v2：用 boss-cli 打开王俊力会话，观察「点击预览附件简历」后发生什么
import { createRequire } from "module";
const bossclidir = "C:/Users/李俊颖/AppData/Roaming/npm/node_modules/@joohw/boss-cli";
const require = createRequire(`${bossclidir}/package.json`);
const puppeteer = require("puppeteer-core");
const { withBossSessionPage } = require(`${bossclidir}/dist/common/boss_session_page.js`);
const { runOpenCandidateChat } = require(`${bossclidir}/dist/toolset/chat.js`);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const version = await (await fetch("http://127.0.0.1:53470/json/version")).json();
  const browser = await puppeteer.connect({ browserWSEndpoint: version.webSocketDebuggerUrl, defaultViewport: null });

  const created = [];
  browser.on("targetcreated", (t) => created.push(`created:${t.type()}:${t.url()}`));

  await withBossSessionPage(async (page) => {
    console.log("打开王俊力会话...");
    await runOpenCandidateChat(page, "王俊力", true);
    await sleep(2000);

    // dump 附件卡片
    const card = await page.evaluate(`(() => {
      const norm = (v) => (v ?? "").replace(/\\s+/g, "").trim();
      const items = Array.from(document.querySelectorAll(".chat-message-list .message-item"));
      for (let i = items.length - 1; i >= 0; i--) {
        const friend = items[i].querySelector(".item-friend");
        if (!friend) continue;
        const title = norm(friend.querySelector(".message-card-top-title")?.textContent);
        if (!title.includes("附件简历")) continue;
        return {
          title,
          btns: Array.from(friend.querySelectorAll(".message-card-buttons .card-btn")).map((b) => norm(b.textContent)),
        };
      }
      return null;
    })()`);
    console.log("附件卡片:", JSON.stringify(card));

    // 点击预览
    const clicked = await page.evaluate(`(() => {
      const norm = (v) => (v ?? "").replace(/\\s+/g, "").trim();
      const items = Array.from(document.querySelectorAll(".chat-message-list .message-item"));
      for (let i = items.length - 1; i >= 0; i--) {
        const friend = items[i].querySelector(".item-friend");
        if (!friend) continue;
        const title = norm(friend.querySelector(".message-card-top-title")?.textContent);
        if (!title.includes("附件简历")) continue;
        const btn = Array.from(friend.querySelectorAll(".message-card-buttons .card-btn"))
          .find((el) => norm(el.textContent).includes("预览"));
        if (!btn) return false;
        btn.click();
        return true;
      }
      return false;
    })()`);
    console.log("点击预览:", clicked);

    for (let i = 1; i <= 6; i++) {
      await sleep(1500);
      const st = await page.evaluate(`(() => {
        return {
          url: location.href,
          iframes: Array.from(document.querySelectorAll("iframe")).map((f) => (f.getAttribute("src") || "").slice(0, 80)),
          dialogs: Array.from(document.querySelectorAll(".boss-popup__wrapper, .dialog-container, .dialog-lib-resume, .boss-dialog__wrapper")).length,
        };
      })()`);
      console.log(`t=${i * 1.5}s`, JSON.stringify(st));
    }
  });

  console.log("新 target:", created.join(" | ") || "(无)");
  const allPages = await browser.pages();
  console.log("所有页面:", allPages.map((p) => p.url()).join(" | "));
  await browser.disconnect();
}
main().catch((e) => { console.error("失败:", e.message); process.exit(1); });