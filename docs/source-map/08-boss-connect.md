# 招聘接入链路

> BOSS 直聘职位与简历导入、触达审核状态机、自动化引擎轮次和去重状态。
>
> 返回 [SOURCE_MAP.md](../SOURCE_MAP.md) 选择其他主题。

## 20. 招聘接入端到端数据流

```text
boss-cli（独立部署，子进程调用）
  ├─ positions ────────────── 在招职位列表
  ├─ jd <职位名> ──────────── 职位 JD（Markdown）
  ├─ recommend [关键词] ───── 职位推荐候选人；打招呼前必须先切到该页
  ├─ list / list --unread ─── 已沟通候选人 / 对方主动发来的消息
  └─ download-resume <姓名> ─ 附件简历本地路径
        │
        ▼
AutomationEngine.run_once()（默认每 300 秒一轮，daemon 线程）
  1. sync_positions()          新职位 → JobRepository.create() + 写入 JD；同时记录全部职位名供界面选择
  2. sync_candidates()         新候选人 → 写入候选人库 + OutreachStore.create(greet) 草稿
  3. sync_inbound()            未读消息（限目标岗位）→ 按类型分流：简历卡片「同意接收」、对方已发简历登记待下载、其他生成「回复+求简历」
  4. auto_accept_resumes()     自动执行「同意接收」草稿（非对外触达，无需 HR 审核）
  5. sync_outreach_results()   已发送触达 → 回写候选人状态为 outreached
  6. sync_resumes()            outreached / resume_ready 候选人 → 下载附件简历写入任务，并登记到候选人本人
  7. sync_scoring()            已收简历候选人 → 岗位专属标准自动评分
  8. start_ready_jobs()        draft/waiting 且材料齐备的任务 → EvaluationEngine.start()
  9. sync_s_calls()            已完成的筛选任务 → S 级名单电话确认任务
        │
        ▼
OutreachStore（outreaches/<id>/outreach.json，pending → sent / failed / rejected）
  ├─ 界面审核：批准发送 / 否决 / 一键发送
  └─ execute_outreach() 把动作翻译成 boss 命令并执行
```

引擎只由 `main()` 启动路径拉起（`app.state.automation.start()`），`create_app()` 只装配不启动。

目标岗位由设置项 `boss_target_job` 指定，未配置时回退环境变量 `BENATS_TARGET_JOB`；两者都为空表示不限定岗位。该值每轮实时读取，用于筛职位、推荐候选人、往来消息与推荐页打招呼。

## 20.1 触达动作与状态机

动作 `OutreachAction`：

| 字段 | 说明 |
| --- | --- |
| `kind` | `greet` 打招呼、`send` 发消息、`action` 聊天页操作 |
| `target` | 候选人姓名 |
| `text` | 消息正文（`send`） |
| `request_resume` | `send` 时是否附带索要简历 |
| `job_keyword` | 关联职位名，用于回写候选人状态 |
| `command` | `action` 的具体命令：`agree-resume`、`request-attachment-resume`、`remark`、`not-fit` |
| `remark` | `remark` 命令的备注内容 |

草稿状态机为 `pending → sent / failed / rejected`，只有 `pending` 可批准或否决，其他状态由端点返回 409。`execute_outreach()` 失败时把 `BossCliError` 文本写入记录的 `error`，状态置 `failed`，不影响其他草稿，也不改变筛选或电话任务状态。

候选人状态（`AutomationStore.candidates`，键为 `<job_id>:<姓名>`）：

```text
outreach_pending → outreached    打招呼/回复草稿发送成功
agree_pending    → outreached    同意接收附件简历自动执行成功（无需 HR 审核）
inbound_pending  → outreached    回复并索要简历草稿发送成功
resume_requested → outreached    求简历草稿发送成功，回到下载探测
resume_ready     → resume_downloaded   对方已发来的附件简历下载成功（不经过对外触达）
outreached       → resume_downloaded   附件简历下载并写入任务成功
```

对方已发来附件简历时，该候选人尚未发送的求简历草稿会被置为 `rejected`，不再索要。

下载失败计数仅在候选人处于 `outreached` 时累加；达到 3 轮失败生成一次「求简历」草稿并置为 `resume_requested`。`resume_ready` 不计失败数，因为对方的简历已经在手。

## 20.2 去重与持久化状态

- `automation.json` 的 `positions`：职位名 → `{job_id, s_call_synced}`。职位名已存在时不重复建任务；`s_call_synced` 保证每个职位的 S 级电话任务只创建一次。
- `automation.json` 的 `candidates`：`<job_id>:<姓名>` → `{status, download_failures}`。候选人已有任意状态时不重复生成草稿。
- `automation.json` 的 `available_positions`：最近一次拉取到的全部职位名，供界面「目标岗位」下拉选择。
- 候选人库 `candidate.resume_file`：该候选人在任务 `resumes/` 下的附件简历文件名。同一任务下会有多位候选人的简历，自动打分按此字段读取本人简历。
- 附件简历写入任务时同步记录 SHA-256 哈希，与手工上传共用同一套判重字段。
- S 级名单从任务的 `评估结果.json` 读取，按结论原文枚举筛选；该文件是模型输出经守卫后的落盘结果，不读前端结果预览。

## 20.3 修改时必须同步检查

- 修改 `boss` 子命令、参数或输出格式：同步 `boss_cli.py` 的解析正则与 `outreach.py` 的命令翻译，二者与 boss-cli 的实际输出耦合。
- 修改触达动作字段：同步 `OutreachAction`、`OutreachInput`、`_new_record()`、`execute_outreach()`、`BossView` 的展示字段与 `tests/test_automation.py`。
- 修改候选人状态名：同步 `AutomationStore` 的状态判断、`sync_*` 各步的迁移条件与自动化验证。
- 修改轮询间隔或引擎启停方式：同步 `AutomationEngine` 构造参数、`main()` 启动路径、`/api/boss/automation/*` 端点与界面状态文案。
- 修改去重键：同步 `positions` / `candidates` 的键格式，并检查已持久化的 `automation.json` 兼容性。
- 修改目标岗位的取值方式：同步 `AppSettings.boss_target_job`、`GET /api/boss/target-job`（选择器数据，读本地缓存）、`POST /api/boss/target-job`、`BossView` 的目标岗位选择器，以及 `_is_target_job()` 的各调用点。注意 `GET /api/boss/positions` 是另一个端点（调 boss-cli 拉实时职位表，供 `RecruitmentWizard` 导入 JD 用），两者不可合并为同一路径。
- 修改候选人附件简历的存放位置或命名：同步 `sync_resumes()` 写入的 `candidate.resume_file` 与 `services._get_resume_text()` 的读取，否则自动打分可能读到同任务下别人的简历。
- 新增对外发送动作时，必须保持「先生成草稿、HR 审核后才执行」的边界，不得直接发送。
