// 诊断：「点击预览附件简历」按钮点击后发生了什么
import { createRequire } from "module";
const bossclidir = "C:/Users/李俊颖/AppData/Roaming/npm/node_modules/@joohw/boss-cli";
const require = createRequire(`${bossclidir}/package.json`);
const puppeteer = require("puppeteer-core");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const version = await (await fetch("http://127.0.0.1:53470/json/version")).json();
  const browser = await puppeteer.connect({ browserWSEndpoint: version.webSocketDebuggerUrl, defaultViewport: null });

  const targets = [];
  browser.on("targetcreated", (t) => targets.push(`created:${t.type()}:${t.url()}`));

  const page = (await browser.pages()).find((p) => p.url().includes("zhipin.com")) || (await browser.newPage());
  await page.bringToFront();

  // 打开王俊力会话（滚动查找）
  let opened = "notfound";
  for (let round = 0; round < 30 && opened === "notfound"; round++) {
    opened = await page.evaluate(`(() => {
      const norm = (v) => (v ?? "").replace(/\\s+/g, " ").trim();
      const wraps = Array.from(document.querySelectorAll(".geek-item-wrap"));
      const wrap = wraps.find((el) => norm(el.querySelector(".geek-name")?.textContent) === "王俊力");
      if (!wrap) return "notfound";
      const row = wrap.querySelector(".geek-item") || wrap;
      row.scrollIntoView({ block: "center", inline: "nearest" });
      row.click();
      return "clicked";
    })()`);
    if (opened === "notfound") {
      await page.evaluate(`(() => {
        const first = document.querySelector(".geek-item-wrap");
        if (!first) return;
        let node = first.parentElement;
        while (node) {
          const s = window.getComputedStyle(node);
          if ((s.overflowY === "auto" || s.overflowY === "scroll") && node.scrollHeight > node.clientHeight) {
            node.scrollTop += 400;
            break;
          }
          node = node.parentElement;
        }
      })()`);
      await sleep(500);
    }
  }
  console.log("打开王俊力:", opened);
  await sleep(3000);

  // dump 附件卡片
  const cardInfo = await page.evaluate(`(() => {
    const norm = (v) => (v ?? "").replace(/\\s+/g, "").trim();
    const items = Array.from(document.querySelectorAll(".chat-message-list .message-item"));
    for (let i = items.length - 1; i >= 0; i--) {
      const friend = items[i].querySelector(".item-friend");
      if (!friend) continue;
      const title = norm(friend.querySelector(".message-card-top-title")?.textContent);
      if (!title.includes("附件简历")) continue;
      return {
        title,
        buttons: Array.from(friend.querySelectorAll(".message-card-buttons .card-btn")).map((b) => norm(b.textContent)),
        html: friend.outerHTML.slice(0, 1500),
      };
    }
    return null;
  })()`);
  console.log("附件卡片:", JSON.stringify(cardInfo, null, 1));

  // 点击「预览」按钮
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
      btn.scrollIntoView({ block: "center", inline: "nearest" });
      btn.click();
      return true;
    }
    return false;
  })()`);
  console.log("点击预览:", clicked);

  for (let i = 1; i <= 6; i++) {
    await sleep(1500);
    const state = await page.evaluate(`(() => {
      const iframes = Array.from(document.querySelectorAll("iframe")).map((f) => f.getAttribute("src") || "");
      const dialogs = Array.from(document.querySelectorAll(".boss-popup__wrapper, .dialog-container, .dialog-lib-resume")).length;
      return { url: location.href, iframes, dialogs };
    })()`);
    console.log(`t=${i * 1.5}s`, JSON.stringify(state));
  }

  console.log("target 事件:", targets.join(" | "));
  const allPages = await browser.pages();
  console.log("所有页面:", allPages.map((p) => p.url()).join(" | "));

  await browser.disconnect();
}
main().catch((e) => { console.error("失败:", e.message); process.exit(1); });