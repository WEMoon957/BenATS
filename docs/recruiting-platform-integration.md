# 招聘平台自动接入技术方案

> 状态：方案草稿，待评审
> 关联项目：Talent Hub（本地证据驱动 AI 招聘工作台）
> 目标平台：飞书招聘、BOSS 直聘、猎聘（首期以通用接口占位跑通全链路）

## 1. 背景与目标

Talent Hub 当前以「本地桌面应用」形态运行：HR 手动上传 JD 与简历文件，应用完成批量初筛、电话确认与结果交付。数据获取依赖人工导出上传，无法与招聘平台自动同步。

本方案的目标是：在不重写既有筛选 / 电话流水线的前提下，新增一层「连接器 + 同步调度」，使岗位与候选人数据可自动流入 Talent Hub，并在后续阶段支持把筛选结论回写到平台。

非目标（本期不涉及）：多人协作 / 权限体系重构、企业级 SLA 保障、全量双向集成。

## 2. 总体架构

新增能力集中在「接入层」，业务层与交付层复用现有实现：

```
招聘平台 OpenAPI（飞书 / BOSS / 猎聘）
        │  OAuth2.0 / AppKey 认证
        ▼
连接器层 Connector（RecruitingSource 抽象 + 每平台 Adapter）
        │  归一化后的 Job / Candidate / Application
        ▼
同步调度层（Webhook 事件 + 定时拉取，增量 · 去重 · 幂等）
        │
        ▼
统一数据模型与存储（本地 JSON → 服务化后迁移 PostgreSQL）
        │
        ▼
业务 Pipeline（简历筛选 / 电话确认，复用现有）
        │
        ▼ 阶段二（可选）
回写层（筛选结论 / 面试反馈写回平台）
```

## 3. 连接器抽象设计

核心是一个面向「数据源」的接口，屏蔽各平台字段与认证差异。业务层只依赖统一模型，新增平台仅需新增 Adapter。

```python
from typing import Protocol

class JobRecord:
    external_id: str          # 平台侧职位唯一 ID
    title: str                # 岗位名称
    description: str          # 岗位说明（直接复用为现有「岗位说明」输入）
    raw: dict                 # 保留平台原始载荷，便于回溯

class CandidateRecord:
    external_id: str
    name: str
    source_platform: str
    resume_raw: bytes | None  # 简历原始文件（PDF/DOCX/图片，交给现有解析）
    applied_job_ids: list[str]

class ApplicationRecord:
    external_id: str
    job_id: str
    candidate_id: str
    stage: str                # 平台侧当前阶段

class RecruitingSource(Protocol):
    platform: str

    def list_jobs(self, cursor: str | None = None) -> tuple[list[JobRecord], str | None]: ...
    def list_candidates(self, job_id: str, cursor: str | None = None) -> tuple[list[CandidateRecord], str | None]: ...
    def fetch_resume(self, candidate_id: str) -> bytes: ...
    # 阶段二（回写）
    def push_result(self, application_id: str, result: dict) -> None: ...
```

设计要点：

- `raw` 字段保留平台原始载荷，用于排查「归一化丢失字段」的问题，也避免二次拉取。
- 简历以原始文件字节进入现有 `extract_resume_text` / OCR 流程，不重复造解析能力。
- 每个 Adapter 内聚三类差异：**认证**、**分页 / 游标语义**、**字段映射**。

## 4. 各平台 Adapter 映射（待核实）

以下为通用设计，具体字段与认证以各平台官方开放文档为准，接入前需逐个确认：

| 平台 | 认证方式 | 主要能力 | 备注 |
| --- | --- | --- | --- |
| 飞书招聘 | 飞书开放平台 app_access_token / tenant_access_token | 职位、候选人、投递 | 与既有的飞书 Webhook 推送共用飞书凭证体系，成本最低 |
| BOSS 直聘 | 开放平台 AppKey / AppSecret | 职位、简历获取 | 字段与权限受开放平台约束，需评估候选人联系方式等敏感字段是否开放 |
| 猎聘 | 开放平台用户授权 / 密钥 | 岗位、简历、投递 | 认证与数据口径需单独确认 |

首期「通用接口占位」策略：先实现一个 `MockSource` Adapter，返回符合 `RecruitingSource` 的固定样本，把「连接器 → 调度 → 归一化 → 喂给 Pipeline → 产出结果」全链路跑通，再逐个替换为真实平台实现。

## 5. 统一数据模型

归一化后的内部模型是连接器与业务层之间的稳定契约：

- `Job`：平台职位 ⇄ 现有「岗位说明」任务。`description` 文本直接映射到现有 JD 输入。
- `Candidate`：候选人 + 简历原始文件。
- `Application`：候选人与职位的投递关系，是「回写」的最小单位。

三者与现有 `JobRepository` 的 `job` 数据结构对接：一个平台职位对应一个 Talent Hub 筛选任务；一批候选人对应一组简历。现有 `total / reviewed / stage` 等状态语义保持不变，仅来源从「手动上传」变为「连接器拉取」。

## 6. 同步机制

两种触发方式并行，视平台能力取舍：

### 6.1 Webhook（优先采用）

- 平台在候选人投递 / 职位变更时回调 `POST /api/webhooks/{platform}`。
- 需要公网可达的入口地址（当前应用仅监听 `127.0.0.1`，服务化后需暴露回环外入口）。
- 验证来源合法性（签名校验 / token），处理重复投递（幂等）。

### 6.2 定时拉取（兜底）

- 调度器按周期轮询各平台 `/jobs`、`/candidates` 接口。
- 增量策略：按 `updated_at` 或平台游标推进，只处理新变化。
- 去重：以 `external_id` 为主键，简历内容以 SHA-256 指纹查重（沿用现有上传去重思路）。

通用要求：失败重试（指数退避）、限流（尊重各平台 QPS 配额）、幂等（同一条记录重复到达不产生脏数据）、失败隔离（单平台故障不影响其他平台）。

## 7. 认证与凭证管理

- 平台 AppKey / AppSecret 与 access_token 属敏感凭证：禁止写入代码、日志、提交记录。
- 桌面单机阶段沿用现有机制（Windows DPAPI / 非 Windows 环境变量）；服务化后迁移至 KMS 或密钥管理服务。
- access_token 由 Adapter 缓存并自动刷新，业务层不感知。

## 8. 存储与服务化演进

| 能力 | 现状 | 自动接入后 |
| --- | --- | --- |
| 数据存储 | 本地 JSON（`JobRepository`） | 服务化后迁移 PostgreSQL / SQLite，保留迁移路径 |
| 密钥存储 | 本地 DPAPI / 环境变量 | KMS / Secret 管理 |
| 服务入口 | 回环监听 `127.0.0.1` | 托管服务 + Webhook 公网入口 |
| 任务触发 | 手动上传 | 连接器拉取 + Webhook |

本期重点在「连接器 + 调度」的最小闭环，数据库迁移仅在该闭环需要多人 / 常驻运行时才启动，避免过早引入复杂度。

## 9. 回写能力（阶段二，可选）

回写是把 Talent Hub 的筛选结论回传给平台（如标记通过 / 淘汰、写入面试反馈）。独立于拉取链路：

- 走独立写接口，权限通常更高且需审计。
- 需要失败补偿（记录待回写队列，重试直至成功或人工介入）。
- 明确「只回写结论、不替代招聘决策」的边界，与现有「AI 仅辅助、人工保留最终判断权」一致。

## 10. 对现有代码的改造点

| 文件 / 模块 | 改造内容 |
| --- | --- |
| `app/main.py` | 新增连接器任务入口与 Webhook 路由；将 `UploadFile` 上传替换为 `JobRecord` / `CandidateRecord` 输入 |
| `app/config.py` | 新增平台凭证配置项，复用 / 扩展 `SettingsStore` |
| 新增 `app/connectors/` | `RecruitingSource` 抽象、`MockSource`、飞书 / BOSS / 猎聘 Adapter |
| 新增 `app/sync/` | 定时调度器、增量游标、去重与幂等 |
| `app/repository.py` | 数据来源字段扩展；为后续数据库迁移预留接口边界 |
| 文档 | `README`、`docs/SOURCE_MAP.md`、`docs/source-map/` 同步更新 |

## 11. 安全与合规

- 简历含大量个人信息（姓名、联系方式、教育 / 工作经历），属敏感数据：拉取、存储、外发均需授权与脱敏边界。
- Webhook 入口需验签，防止伪造投递事件。
- 平台访问凭证的最小权限原则：只申请读职位 / 候选人所需 scope，回写接口单独授权。
- 沿用既有「回环隔离 + 会话令牌」思路，服务化后补 TLS 与鉴权。

## 12. 里程碑建议

1. **M1 · 占位跑通**：`RecruitingSource` 抽象 + `MockSource`，连接器 → 调度 → 归一化 → 现有 Pipeline → 产出结果全链路打通。
2. **M2 · 飞书招聘**：接入真实飞书招聘拉取（复用飞书凭证体系）。
3. **M3 · BOSS / 猎聘**：逐个接入，确认字段与权限。
4. **M4 · 服务化**：数据库迁移 + Webhook 公网入口 + 凭证管理。
5. **M5 · 回写（可选）**：筛选结论回写平台。

## 13. 风险与待确认事项

- 各平台开放接口的字段范围与权限（尤其候选人联系方式等敏感字段）需逐家确认。
- Webhook 需要公网入口，涉及部署形态变化。
- 平台凭证与简历数据的合规 / 授权边界需法务与采购侧确认。
- access_token 有效期与刷新策略因平台而异，需逐家验证。