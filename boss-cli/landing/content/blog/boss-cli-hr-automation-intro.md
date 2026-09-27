---
title: boss-cli 是什么？Boss直聘招聘自动化入门
description: 面向 HR 与招聘团队的开源 CLI：基于 CDP 自动化候选人管理、消息发送与简历筛选，支持 AI Agent 集成。
date: 2026-05-20
---

HR 和招聘团队每天重复的操作很多：翻候选人列表、发跟进消息、筛简历、记沟通状态。Boss直聘网页端功能完整，但**重复点击**不适合规模化，也不方便交给 AI Agent 执行。

## boss-cli 解决什么

boss-cli 是专为 Boss直聘设计的开源命令行工具：

- 基于 **CDP（Chrome DevTools Protocol）** 驱动浏览器，贴近真实用户操作
- 封装候选人列表、会话、筛选等常见动作
- 支持脚本化与 Agent 调用，一行命令完成批量任务
- 开源（GPL-3.0），可自托管、可审计

## 典型场景

| 场景 | 价值 |
| --- | --- |
| 批量打招呼 / 跟进 | 减少手工复制粘贴 |
| 候选人状态同步 | 配合内部 ATS 或表格 |
| Agent 工作流 | 让 coding agent 代 HR 执行重复操作 |

## 安装

```bash
npm install -g @joohw/boss-cli
```

首次使用需完成 Boss直聘登录（CLI 会引导浏览器登录流程）。

## 下一步

- 阅读 [批量发送候选人消息](/blog/batch-message-candidates-with-boss-cli)
- 查看 [GitHub 文档](https://github.com/joohw/boss-cli#readme)
- 首页 [功能介绍](/#features)
