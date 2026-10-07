# BenATS 整体体检记录（2026-10-07）

对 Talent Hub（BenATS）做一次跨层体检：后端 `app/`、前端 `frontend/`、文档 `docs/` 与 `README`，核对事实、修复高价值缺口并同步文档。本文件是一次性诊断记录，不参与产品文档维护。

## 一、验证基线（改动前后对照）

| 检查项 | 改动前 | 改动后 |
| --- | --- | --- |
| 后端 `python -m pytest` | 102 passed | 104 passed（新增 2 例考勤测试） |
| 前端 `npm run test:unit` | 5 failed / 57 passed | 64 passed |
| 前端 `npm run test:contract` | 6 passed | 6 passed |
| 前端 `npm run build` | 通过 | 通过 |
| `markdownlint`（AGENTS.md glob） | 2 issues | 0 issues |
| 前端 `tsc --noEmit` | 3 errors（含本人新增 1 处，已修） | 2 errors（均为既存） |

## 二、已修复项

### 1. 前端 5 例音频测试失败（根因：测试环境两套 Blob 实现）

- 现象：`tests/unit/call-item-detail.test.tsx` 5 例断言 `audio.src` 恒为空。
- 根因：jsdom 的 `Blob` 未实现 `stream()`，而本环境的 `Response` 来自 Node，`new Response(blob)` 抛 `object.stream is not a function`；且 `response.blob()` 返回 Node Blob，与全局 Blob（jsdom）不是同一个类，`expect.any(Blob)` 恒为假。
- 修法：`frontend/tests/setup.ts` 统一环境——为 jsdom Blob 补 `stream()`，并包装 `Response.prototype.blob` 返回本环境 Blob。**未改动任何断言与期望值。**

### 2. 考勤导入文件名未消毒（路径遍历，P0）

- 位置：`app/attendance/routes.py` 原 `filename = file.filename or ""` 直接参与落盘路径。
- 修法：改用仓库既有 `safe_filename()`；错误文案去掉「第一版」历史措辞。
- 回归测试：`tests/test_attendance_import.py::test_import_filename_cannot_escape_directory`。

### 3. 考勤库不保存支付类敏感信息

- 位置：`app/attendance/db.py`（schema）、`routes.py`（员工新建/修改）、`exporter.py`（汇总表导出）。
- 事实：`bank_name / bank_account_holder / bank_province / bank_branch / bank_card_number / alipay_account` 在后端有表结构、写入与「银行卡信息」导出列，但**前端没有任何输入项**，实际恒为空串。
- 修法：六字段从 schema、API 模型、写入语句与导出表（含列块与列宽/对齐）中整体移除；导出表列数由 A–W 收敛为 A–Q。

### 4. 考勤默认口令：首次登录强制改密

- 修法：`account` 表新增 `must_change_password`（`initialize()` 对既有库补列）；默认 admin 标记为需改密；`require_account(write=True)` 在标记为真时返回 403「请先修改初始密码后再操作」；`change-password` 成功后清除标记；前端 `AttendanceView` 在标记为真时只呈现不可跳过的改密面板。
- 回归测试：`tests/test_attendance_import.py::test_default_admin_must_change_password_before_writing`。

### 5. 数据目录双事实来源（`--data-dir` 未覆盖考勤/招聘 SQLite）

- 位置：`app/main.py::main()`。
- 事实：`create_app(data_dir)` 把 `root` 传给任务仓储与设置，而 `get_attendance_store()` / `get_recruitment_store()` 是进程级单例，固定用 `app_data_dir()`。带 `--data-dir` 启动时，考勤库与招聘库仍落在默认目录。
- 修法：`--data-dir` 同步写回 `TALENT_HUB_DATA_DIR`，使两类 SQLite 与任务仓储共用同一根目录。

### 6. 非原子写入（进程中断可能留下半截 JSON）

- 位置：`app/pipeline.py`（`筛选标准.json` 与标准 Markdown 两处）、`app/main.py`（电话摘要 `summaries/*.json|md`）。
- 修法：统一改用既有 `atomic_write_text()`（临时文件 + `os.replace`）。

### 7. 智联 CLI 错误未纳入全局处理

- 位置：`app/main.py`。`BossCliError` 有 503 处理器，`ZhaopinCliError` 直接落到默认 500。
- 修法：补对称的异常处理器。

### 8. 评分标准上传无大小限制

- 位置：`app/main.py::parse_rubric_resumes`。原实现把任意数量、任意大小的文件读入内存。
- 修法：复用既有常量，单文件 50 MB、累计 1 GB 上限，超限返回 413。

### 9. 顶栏工具入口缺失（用户可感知的行为回归）

- 事实：顶栏「新建」工具条只有「招聘工作台」「考勤管理」；`toolScreening` / `toolPhone` 两个 i18n 键无任何引用；界面无法新建一条简历筛选或电话确认任务，而 `ScreeningView` / `PhoneView` 的手工流程完好。
- 修法：`App.tsx` 恢复「简历筛选」「电话确认」两个入口并接线（筛选重置工作区后进入新建页，电话重置后进入新建页），新增 `toolRecruitment` 键统一四个入口文案。
- 回归测试：`frontend/tests/unit/app-shell.test.tsx`「顶栏工具入口」。

### 10. 生产代码卫生

- `app/attendance/services.py`、`app/recruitment/services.py`：去掉「移植自…」「不再使用…」等描述实现历史的措辞。
- `app/attendance/db.py`：模块 docstring 由「共享一个连接」改为与实现一致的「每次读写新建连接并关闭」。

### 11. 文档事实错误与同步

- `docs/source-map/01-architecture.md`：删掉「本项目不使用数据库」（实际使用 SQLite）；补考勤/招聘/评分标准在运行拓扑与启动链路中的位置；补 `feishu_sync.start()`。
- `docs/source-map/06-api-runtime.md`：触达状态机由 `pending → approved/rejected → sent/failed` 改为与实现一致的 `pending → sent / failed / rejected`；`approve-all` 补目标岗位过滤与 `skipped` 语义；补 BOSS/智联目标岗位端点；新增 §11.7 招聘候选人与考勤端点清单；原子写入表述随代码更新。
- `docs/source-map/02-code-map.md`：补 `app/recruitment/`、`app/rubric/`、`app/attendance/` 与 7 个前端视图。
- `docs/SOURCE_MAP.md`：新增「招聘候选人与考勤」主题行。
- `README.md` / `README.en.md`：环境变量表补齐 `ZHAOPINCLI_BIN`、`CHROME_PATH`、`BENATS_TARGET_JOB`、`BENATS_ZHAOPIN_TARGET_JOB`；智联命令表补 `download` / `download-all`；候选人看板列名与顺序、招聘作业台首步名称、「招聘接入」在界面中的实际位置、「首次登录强制改密」等改为与实现一致；英文版补齐「候选人跟进」「考勤管理」两节、场景/技术特性/安全/项目结构条目与 SQLite 徽章（两版标题结构现均为 13 个二级 + 9 个三级）。
- `APP_GUIDE.md`：新增「考勤管理」操作章节；修正招聘接入与智联目标岗位的界面位置；补智联下载命令。

## 三、未修复项（建议后续处理）

按严重度排序，均附证据位置。

### P1

1. **`SettingsStore` 读改写无锁**：`app/config.py`（`load` / `save`）与调用点 `app/main.py` 的设置保存、BOSS/智联目标岗位设置。并发「load → 改字段 → save」会互相覆盖。建议抽出加锁的 `update(mutator)` 并替换调用点。
2. **电话条目摘要落盘在仓储锁外**：`app/main.py::update_call_item` 先 `call_repository.update_item()`（锁内）再写 `summaries/*`（锁外），并发 PUT 可能写出与记录字段不一致的摘要。建议把摘要落盘放进仓储锁内。
3. **长任务在请求内执行**：`approve_all_outreaches`（逐条子进程，单条超时 180 s）与 `import_position`（串行下载附件简历）会在一次 HTTP 请求内持续数分钟，无进度回传。建议改为后台任务 + 轮询。
4. **招聘候选人 SQLite 与考勤 SQLite 的连接策略**：`get_*_store()` 是进程级单例，`create_app(root)` 换根目录时不重建。当前已通过「`--data-dir` 写回环境变量」规避，但单进程内多实例（测试场景）仍会串用。建议后续把 root 显式传入。
5. **考勤飞书 `app_secret` 明文入库**：`app/attendance/routes.py` / `db.py`，与主配置的 DPAPI 加密不一致。建议复用 `app/config.py` 的加解密，或改由环境变量提供。

### P2

1. 全局异常处理不完整：未捕获异常仍走 FastAPI 默认 500 文本，成功/失败响应结构未统一（部分 `{detail}`、部分 `{"ok": true}`）。
1. 若干端点缺少输入长度/数量上限（`JobInput.title`、`JobBriefInput.text`、`CallItemInput`、评分标准相关输入），与已加 `max_length` 的 `CallInput` / `OutreachInput` 风格不一致。
1. 横向对比缓存 `_compare_cache` 无锁、无上限，任务删除时才清理（`app/main.py`）。
1. 前端在 `api/client.ts` 之外直连后端：`AttendanceView` 的导出下载绕过 `api()`，失败时无提示；考勤 token 存 `localStorage`。
1. 前端多处 `.catch(() => {})` 静默吞异常，页面停留在空态（`AttendanceView`、`RubricView`、`CallsView`）。
1. 死代码：`app/recruitment/scoring.py` 整模块与 `services.py` 的 `score_candidates` 无调用；`app/connectors/zhaopin_imports.py::import_candidates`、`boss_cli.py` 的 `list_contacts` / `preview_resume` 无引用。其 docstring 仍以「在线简历文本」描述数据源，与现行附件简历流程不符。
1. 测试覆盖缺口：考勤模块（`app/attendance/*`）、`app/repository.py`、`app/config.py`、`app/call_repository.py`、`app/rubric/*` 与全部 HTTP 端点中间件均无测试；前端 `AttendanceView`、`RecruitmentWizard`、`CandidateView`、`BossView`、`RubricView`、`CallsView` 无单测。
1. 前端既存类型错误：`frontend/tests/unit/compare-dialog.test.tsx` 两处向 `CompareCandidate` 传入不存在的 `conclusion` 字段（`tsc --noEmit` 报 TS2353），与本次改动无关。

## 四、说明

- 本轮未执行 `git commit` / `push`；`zhaopin-cli/*` 的改动与未跟踪文件属此前会话，未纳入本次改动。
- 会话中曾因测试未隔离数据目录而向 `~/.local/share/TalentHub` 写入 1 条导入记录与 1 个文件，已精确回滚（删除该记录与文件、并移除仅由测试创建的空目录），现有测试已改为指向临时目录。
