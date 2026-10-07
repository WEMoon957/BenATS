<p align="center" style="margin-bottom: 6px;">
  <img src="assets/app-icon-logo.png" alt="Talent Hub logo" width="120" />
</p>

<h1 align="center" style="margin-top: 0;">Talent Hub</h1>

<p align="center">
  <em>A local-first, evidence-driven AI HR workbench covering the full hiring flow and attendance accounting: from sourcing on BOSS Zhipin and AI pre-scoring through resume screening, phone confirmation, and candidate follow-up, plus automatic Feishu check-in sync and accounting — so every judgment has a traceable basis.</em>
</p>

<p align="center">
  English · <a href="README.md">简体中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Language-Python-3776AB?style=flat&amp;logo=python&amp;logoColor=white" alt="Language: Python" />
  <img src="https://img.shields.io/badge/Backend-FastAPI-009688?style=flat&amp;logo=fastapi&amp;logoColor=white" alt="Backend: FastAPI" />
  <img src="https://img.shields.io/badge/Frontend-React%20%2B%20TS-61DAFB?style=flat&amp;logo=react&amp;logoColor=black" alt="Frontend: React + TS" />
  <img src="https://img.shields.io/badge/Excel-openpyxl-217346?style=flat" alt="Excel: openpyxl" />
  <img src="https://img.shields.io/badge/PDF-pdfplumber-7B5BF2?style=flat" alt="PDF: pdfplumber" />
  <img src="https://img.shields.io/badge/Storage-SQLite-003B57?style=flat" alt="Storage: SQLite" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/OCR-Tesseract-B45F2A?style=flat" alt="OCR: Tesseract" />
  <img src="https://img.shields.io/badge/ASR-Volcano%20Engine-3370FF?style=flat" alt="ASR: Volcano Engine" />
  <img src="https://img.shields.io/badge/Platform-Windows%20%2B%20macOS-555555?style=flat" alt="Platform: Windows + macOS" />
  <img src="https://img.shields.io/badge/Packaging-PyInstaller-8AA0B5?style=flat" alt="Packaging: PyInstaller" />
</p>

> [!IMPORTANT]
> AI results are hiring assistance only — never an automatic hire-or-reject decision. HR and hiring managers always keep the final call.

## Table of contents

- [What it solves](#what-it-solves)
- [HR productivity scenarios](#hr-productivity-scenarios)
- [Current capabilities](#current-capabilities)
- [Technical highlights](#technical-highlights)
- [Quick start (development)](#quick-start-development)
- [Application settings](#application-settings)
- [Feishu push setup (optional)](#feishu-push-setup-optional)
- [Windows build](#windows-build)
- [macOS build](#macos-build)
- [Data & security](#data--security)
- [Project layout](#project-layout)
- [Limitations](#limitations)

## What it solves

The three most repetitive parts of hiring, and the ones whose judgment is easiest to distort by inconsistent standards: bulk resume screening, justifying and reviewing calls, and organizing phone recordings plus consolidating results. Attendance accounting is the fourth: matching monthly Feishu check-ins to employees and computing attendance.

Talent Hub runs hiring as one trackable funnel — source → pre-score → greet → collect resumes → screen → phone confirmation → interview → offer — and automates attendance accounting, leaving a verifiable basis for each step and making results ready to deliver. Settings, task materials, and results are stored locally by default; when model, ASR, or Feishu features are used, the corresponding content is sent to the configured service.

## HR productivity scenarios

| Step | How Talent Hub handles it |
| --- | --- |
| **Candidate sourcing** | Pulls open positions and candidates from BOSS Zhipin and sends them to the follow-up board after AI pre-scoring |
| **Candidate follow-up** | A stage board from discovery to hire, tracking scoring, greeting, interview scheduling, interviews, and offers in one line |
| **Bulk screening** | Screens against one consistent standard and returns a tiered shortlist |
| **Justifying & reviewing calls** | Attaches a traceable basis to each judgment and flags uncertain points |
| **Phone confirmation** | Transcribes recordings and organizes the key points; reviewed records export as files |
| **Delivering results** | Produces one unified evaluation outcome and shortlist |
| **Sharing results** | Pushes the result summary to a Feishu group when a task finishes |
| **Attendance accounting** | Syncs Feishu check-ins automatically, matches employees, and computes attendance; only cross-day anomalies need human review |

## Current capabilities

Currently supported modules:

### Boss Connect (BOSS Zhipin)

| Capability | Description |
| --- | --- |
| **Target position** | Pick the active position from the fetched list in the Recruitment workbench → Outreach review panel; only that position's job posts, recommended candidates, and inbound messages are processed. Leave it empty to process all positions. |
| **Automatic position import** | Pulls open positions on a schedule and creates a screening task with the job brief for every position seen for the first time. |
| **Candidate sourcing** | Pulls recommended candidates for each position and drafts a "greet" outreach action. |
| **Outreach review** | Every outbound action is drafted first. HR approves or rejects each draft in the Recruitment workbench → Outreach review panel, or sends all pending drafts for the current target position at once; a failed send records its reason without blocking the other drafts. |
| **Inbound message handling** | When a candidate messages you, the app auto-accepts attached-resume request cards; an attached resume the candidate already sent is downloaded instead of requested again; other messages get a drafted reply that also asks for the resume. |
| **Attached resume retrieval** | Attached resumes from candidates are downloaded into the matching task; a contacted candidate who yields no resume for three consecutive rounds gets one "request resume" draft. |
| **Screening hand-off** | Tasks with both a job brief and resumes start screening automatically, and S-tier candidates from a completed run enter a phone-confirmation task. |
| **Deduplication** | Processed positions and candidate states are stored locally, so the same position or candidate never runs through the flow twice. |
| **Engine control** | The automation engine starts with the app and advances one round every five minutes by default; you can stop it, start it again, or run a single round from the interface. |

> [!NOTE]
> Boss Connect requires a separately deployed boss-cli and a signed-in BOSS Zhipin account. Prerequisites and setup steps are in [APP_GUIDE「招聘接入」](APP_GUIDE.md#招聘接入) (Chinese).

### Zhaopin Connect (智联招聘)

Zhaopin is integrated through a separately deployed zhaopin-cli, which exposes these commands:

| Capability | Description |
| --- | --- |
| **Sign-in** | `zhaopin login` opens the Zhaopin sign-in page for you to complete manually; the session is stored locally and reused by later commands. |
| **Enterprise navigation** | `zhaopin home` jumps the browser straight to the Zhaopin enterprise candidate-recommendation page; if you are not signed in it tells you to run `zhaopin login` first. |
| **Position listing** | `zhaopin positions [keyword]` opens the position picker and lists the selectable positions, optionally filtered by keyword. |
| **Position switching** | `zhaopin recommend <keyword>` switches to the given position through the picker before reading candidates, instead of relying on the unstable current-position text. |
| **Candidate listing** | `zhaopin recommend` loads the recommendation list with real mouse-wheel scrolling and prints each candidate's name, basic info, and whether they can still be greeted. |
| **Detail reading** | `zhaopin open <name>` opens a candidate's detail panel, prints its text, and closes it again. |
| **Greeting** | `zhaopin greet <name>` greets a candidate. The first-time greeting dialog is confirmed automatically, and candidates who were already greeted are detected and skipped. |
| **Requesting information** | `zhaopin request <name> [resume\|phone\|wechat]` opens the chat panel to ask for an attached resume, a phone number, or a WeChat ID; the actions can be combined and default to `resume`. The phone request's method chooser is confirmed automatically. |
| **Resume download** | `zhaopin download <name>...` and `zhaopin download-all` download every attached resume the candidate sent into `~/.zhaopin-cli/downloads/`, skipping duplicates per candidate; both the inline PDF and the `.doc` download paths are handled. |
| **Resume analysis** | `app/connectors/zhaopin_imports.py` reads recommended candidates, captures their online resume text, stores it as Markdown, and writes it into a Talent Hub job so the existing screening pipeline (criteria → evaluation → S/A/B/C) applies unchanged. |
| **Connector** | `app/connectors/zhaopin_cli.py` invokes these commands as a subprocess and parses the plain-text output into structured data. |

> [!NOTE]
> Zhaopin is wired into the background automation engine: pulling positions, pulling recommended candidates, greeting, and requesting attached resumes all go through a "draft first, execute after HR approval" flow. Scope it with the "Zhaopin target position" setting or the `BENATS_ZHAOPIN_TARGET_JOB` environment variable. On the application side, reading unread messages, downloading attached resumes, and auto-scoring still apply to BOSS only (the Zhaopin CLI does provide `zhaopin download` / `download-all`; the automation engine does not use them yet). Prerequisites and setup steps are in [APP_GUIDE「智联招聘」](APP_GUIDE.md#智联招聘) (Chinese).

### Candidate follow-up

| Capability | Description |
| --- | --- |
| **Stage board** | Columns follow "to greet → greeted → to score → screening → interviewing → closed", with one card per candidate showing name, position, score tier, and reason. |
| **AI pre-scoring** | Before greeting, the online resume is coarsely tiered against the job JD on a four-level scale (S phone contact / A interview first / B phone confirmation / C do not proceed); clear mismatches are marked skipped to avoid wasted outreach. |
| **Bulk greeting** | Scored candidates land in the "to greet" list, where HR selects and greets them in bulk. |
| **Stage progression** | Candidates move manually from discovery all the way to interview, offer, or rejection, persisted throughout in local SQLite. |
| **Recruitment wizard** | A four-step wizard starts a passive-sourcing run: position & account → hiring standard → execution plan → pre-flight checks. Once the checks pass it starts, and the system pulls candidates, greets, requests resumes, and downloads and scores automatically. |

### Resume screening

| Capability | Description |
| --- | --- |
| **Criteria first** | Generates the job essence, target profile, business scenarios, key actions, hard requirements, negative signals, per-dimension scoring anchors, and A/B/C decision rules from the JD. |
| **Evidence-driven evaluation** | Match and mismatch judgments must be grounded in the resume source: direct quotes are preferred, and reasonable inference from the full experience is allowed; judgments with no traceable basis in the source are downgraded or sent to manual review. |
| **Per-dimension scoring & S tier** | The model scores the four core dimensions (object/scenario/actions/ownership) from 0 to 10; when all hard gates pass, the conclusion is A, and every score meets the threshold, code promotes the candidate to S and routes them to phone contact. |
| **Tiered recommendations** | Code applies one state machine: a supported hard/core mismatch is C, an unknown hard/core fact is B, all required checks passing is A, and an A whose dimension scores all meet the threshold becomes S. |
| **Batch evaluation** | Supports 1–12 concurrent candidates. Each resume follows one evaluation flow, but retryable transport errors or failed JSON/structure validation can trigger another model request. One parsing or model-request failure does not stop the remaining resumes. |
| **Saved partial results** | Each successful evaluation is saved locally immediately; if batch finalization fails, completed candidates remain viewable and the run can be restarted, while download, append, and notification actions stay unavailable until formal artifacts exist. |
| **Side-by-side comparison** | Compares up to 20 S/A/B candidates; code keeps every S ahead of every A and every A ahead of every B, while AI only orders candidates within the same tier and explains why. |
| **Multi-format parsing** | Supports PDF, DOCX, TXT, Markdown, and common image formats; scanned documents can use Tesseract OCR. |
| **Deliverable results** | Generates and validates a five-sheet Excel workbook (candidate summary with per-dimension score summary and ranking, evidence matching, phone-confirmation questions, screening criteria, recommendation list) plus Markdown screening criteria. |

### Phone screening

| Capability | Description |
| --- | --- |
| **S-tier contact roster** | With automation enabled, S-tier candidates from completed screening land in a phone-confirmation task with their names and one-line judgments, ready for HR to call one by one. |
| **Batch transcription** | Upload multiple recordings (m4a / wav / mp3 / ogg / opus) powered by Volcano Engine ASR. |
| **AI summarization** | Uses a senior-recruiter perspective to produce structured notes, soft-skill evaluations that prioritize the selected focus dimensions without being limited to them, and an optional Q&A transcript (off by default to reduce output length and processing time). |
| **Structured result delivery** | Code validates JSON and required structure only. It does not delete model-produced notes or soft-skill evaluations or change field status based on citations; citations are used only for audio positioning. |
| **Manual review & download** | Date-based default task titles follow the interface language while manual titles remain unchanged; after per-candidate review, export a Markdown record for each candidate. |

### Feishu push

| Capability | Description |
| --- | --- |
| **Auto notification on completion** | Pushes results to a designated Feishu group when resume screening or phone screening finishes. |
| **HR-focused result messages** | Each resume-screening run sends one overview with submitted/successful/failed counts, S/A/B/C distribution, and one-line judgments for up to five S/A/B candidates. Phone screening sends one organized record per candidate; records over the per-message limit are truncated with a prompt to return to the app. |
| **Incremental deduplication** | Appended resumes notify only newly evaluated results and include the cumulative role total; appended recordings send only entries not yet pushed successfully; a full re-screen after criteria changes is notified as a new version. |
| **Reliable delivery** | Transient network errors, HTTP 429, and 5xx responses receive limited retries with rate limiting; push failures are recorded without changing the screening or phone task's business status. |
| **Manual resume-notification retry** | From a completed resume-screening result, click "Retry Feishu notification" to send only pending results and see whether this attempt sent anything. The phone task view has no manual retry action wired up yet; the retry capability is provided by a backend endpoint. |
| **Test Feishu link** | Send a test message in Settings to verify Webhook connectivity. |

### Attendance management

| Capability | Description |
| --- | --- |
| **Multi-role sign-in** | A separate account system (administrator / HR / supervisor / read-only) with PBKDF2-hashed passwords; administrators and HR can edit. The default account is `admin` / `admin`, and first sign-in forces you to set a new password; no write action is possible until then. |
| **Employee records & policies** | Employee records (employee number, name, aliases, department, position), tags, and attendance policies (standard / flexible / exempt / part-time / shift) are managed separately. |
| **Feishu check-in import** | Imports a Feishu check-in `.xlsx`; matching prefers employee number with name / alias as fallback. Blank or `-` counts as rest, any check-in counts as attendance. |
| **Automatic Feishu sync** | Pulls check-in results (including device check-ins) on a schedule through a Feishu custom app, with no manual Excel export; requires the "export check-in data" permission, configured under Attendance settings. |
| **Cross-day anomaly review** | Detects cross-day candidates from "next-day" records and small-hours check-ins; a human decides whether to attribute them to the previous day or keep them on the current day. |
| **Accounting & export** | Computes attendance days from the policy and supports manual adjustment, confirmation, rule tracing, and an Excel summary export (summary / detail / raw check-ins). |
| **Attendance dashboard** | Supports cross-month queries and department filters, with a daily attendance-rate line chart, department comparison, and import summary. |

## Technical highlights

- **Local-first**: settings, original task materials, and results are stored in the user data directory by default (Windows: `%LOCALAPPDATA%\TalentHub`; macOS: `~/.local/share/TalentHub`), which is outside the source tree. When overridden with `TALENT_HUB_DATA_DIR` or `--data-dir`, the operator chooses the location.
- **Key security**: Windows encrypts model, ASR, and Feishu signature keys with the current user's DPAPI; macOS uses environment variables for secrets.
- **Loopback isolation**: the service listens on `127.0.0.1` only and generates a per-session token at startup.
- **Fairness safeguards**: model prompts prohibit using age, sex, ethnicity, place of origin, marital status, or reproductive status for evaluation or ranking. Code also filters hard requirements, A/B/C conditions, and negative signals against its built-in protected-attribute terms. These safeguards do not replace human bias review.
- **Frontend**: React + TypeScript frontend (Vite build), served by FastAPI.
- **SQLite storage**: attendance and candidate data persist through Python's standard-library sqlite3 (`attendance.db` and `recruitment.db`), adding no extra database dependency.
- **Multi-role sign-in**: the attendance module ships its own account system (administrator / HR / supervisor / read-only) with PBKDF2-hashed passwords.
- **Optional Feishu notifications**: pushes result summaries via a Feishu custom bot Webhook, with no new third-party dependency.
- **Optional Boss Zhipin integration**: reads positions, candidates, and attached resumes through a locally deployed boss-cli (a standalone Node/TS CLI driving the local Chrome over CDP), and hands HR-approved outreach actions to it for sending. When it is missing or not signed in, only Boss Connect is unavailable; the other modules are unaffected.
- **Optional Zhaopin integration**: reads positions, candidates, and details and performs greetings through a locally deployed zhaopin-cli (a standalone Node/TS CLI driving the local Chrome over CDP). Clicking, scrolling, and typing use native browser events rather than injected scripts. When it is missing or not signed in, only the Zhaopin commands are unavailable; the other modules are unaffected.

## Quick start (development)

**Prerequisites**: Windows or macOS, Python, Node.js and npm, and an OpenAI Chat Completions-compatible model service.

1. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

   To run the backend tests, also install the dev dependencies with `python -m pip install -r requirements-dev.txt`, then run `python -m pytest`.

2. Build the frontend (the backend serves the `frontend/dist` build output):

   Windows:

   ```powershell
   cd frontend
   npm ci
   npm run build
   ```

   macOS:

   ```bash
   cd frontend && npm ci && npm run build
   ```

   > Windows shortcut: after installing the Python and frontend dependencies, double-click `start-app.bat` in the project root. It starts the backend and runs `npm run build -- --watch` to rebuild frontend changes automatically, but it does not run `npm ci`; refresh the page to see frontend changes.

3. Start the app:

   Windows:

   ```powershell
   python -X utf8 -m app.main
   ```

   macOS:

   ```bash
   export TALENT_HUB_API_KEY="your model API key"
   python -X utf8 -m app.main
   ```

The app opens your default browser on startup. On first use, on Windows enter the API endpoint, API key, and model name in Settings and test the connection; on macOS, provide secrets through environment variables and enter only the non-sensitive items such as the API endpoint and model name in Settings.

> [!NOTE]
> Text-based PDF, DOCX, TXT, and Markdown need no OCR. For scanned PDFs or images, install Tesseract (and the `chi_sim` language pack for Chinese resumes). The app checks `TESSERACT_CMD`, `PATH`, and common platform paths automatically; enter the executable path in Settings only if detection fails. On Windows, install it from the [UB Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) and select Simplified Chinese during setup;  A step-by-step guide is available in [APP_GUIDE「OCR 配置」](APP_GUIDE.md#ocr-配置) (Chinese).

## Application settings

The Settings dialog (top-right) centrally manages the options below. "API endpoint", "API key", and "Model" are required; the rest are optional or tuned on demand.

| Setting | Default / range | Description |
| --- | --- | --- |
| API endpoint | `https://api.openai.com/v1` | An OpenAI Chat Completions-compatible service with JSON output. Enter the base URL without `/chat/completions`; the app appends that path and does not require the URL to end in `/v1`. |
| API key | empty | Model service access key; Windows encrypts it with DPAPI, while macOS uses the `TALENT_HUB_API_KEY` environment variable. |
| Model | empty | The model identifier to use, as defined by your model provider. |
| Concurrency | 6 (1–12) | Number of candidates processed in parallel per batch. Duration depends on candidate count, model latency, and provider rate limits; lower it when rate-limited. |
| Timeout (seconds) | 180 (30–600) | Timeout for each ordinary model HTTP attempt. Each criteria-generation call and resume-evaluation call allows up to 3 and 2 retryable transport attempts, respectively; failed JSON/structure validation can start one more corrective call. Each phone-summarization call uses at least 300 seconds per attempt and allows up to 3 retryable transport attempts; failed structure validation can also start one more call. |
| OCR executable path | empty | Tesseract executable path for scanned PDFs or image resumes. When empty, the app detects it automatically (see the OCR note above). |
| Keep parsed resume text in the local task folder | on | Whether to keep each resume's parsed text locally. When off, those texts are not saved, while parsed JD text and the parsing manifest without resume bodies remain stored. |
| Speech-to-text key | empty | Volcano Engine large-model speech recognition (audio-file fast version) API key; Windows encrypts it with DPAPI, while macOS uses the `TALENT_HUB_ASR_API_KEY` environment variable. |
| Generate phone screening details (Q&A transcript) | off | When enabled, phone summarization also produces a full Q&A transcript; leaving it off reduces model output length and processing time. |
| Double-check A conclusions | off | When enabled, resume screening runs an independent review call for each initially A-rated candidate; a disagreement with the initial conclusion downgrades the candidate to B with a verification question. Increases model calls. |

### Speech-to-text (Volcano Engine) setup

Phone-call transcription uses **Volcano Engine large-model speech recognition (audio-file fast version)** and requires only one API key.

1. Open the [Volcano Engine Speech / Doubao voice console](https://console.volcengine.com/speech/app) and log in (register and complete real-name verification first if needed).
2. Create an app and be sure to select **"Audio-file fast version / Large-model speech recognition fast version"** (resource `volc.bigasr.auc_turbo`); do not pick the standard or streaming variant, which cannot transcribe local files.
3. In the console's "API key" page (new console), copy the **API key**.
4. On Windows, paste that key into "Speech-to-text key" in Settings and save; on macOS, set `TALENT_HUB_ASR_API_KEY` before launch.

> [!NOTE]
> Transcription is billed by Volcano Engine by transcript duration; check the free allowance and resource packs in the console first. Original recording content is sent to Volcano Engine ASR for transcription. The transcript is sent to the configured model service to generate the organized call record. Original recordings, transcripts, and organized records are saved in the local data directory.

### Environment variables (optional)

| Variable | Description |
| --- | --- |
| `TALENT_HUB_API_KEY` | Injects the model API key via environment variable. On Windows, a saved key takes precedence and this variable is a fallback; on macOS, use this variable for the model key. |
| `TALENT_HUB_ASR_API_KEY` | Injects the Volcano Engine ASR API key via environment variable; macOS uses this variable for speech-to-text. |
| `TALENT_HUB_FEISHU_SIGN_SECRET` | Injects the Feishu bot signature secret via environment variable; the Webhook URL can still be saved in Settings. |
| `TALENT_HUB_DATA_DIR` | Overrides the default data directory (Windows: `%LOCALAPPDATA%\TalentHub`; macOS: `~/.local/share/TalentHub`) for settings, task materials, result files, and the attendance / candidate SQLite databases. Parsed JD text is saved. The operator is responsible for keeping a custom path outside the source tree. |
| `TESSERACT_CMD` | Specifies the Tesseract executable path; if unset, the app tries `PATH` and platform-specific common locations. |
| `BOSSCLI_BIN` | Specifies the boss-cli executable path; if unset, the `boss` command on `PATH` is used. |
| `ZHAOPINCLI_BIN` | Specifies the zhaopin-cli executable path; if unset, the `zhaopin` command on `PATH` is used. |
| `CHROME_PATH` | Read by boss-cli / zhaopin-cli to locate the local browser executable; if unset, common install locations are probed automatically. |
| `BENATS_TARGET_JOB` | Sets the BOSS Zhipin target position; an alternative to the "target position" selector in the UI. When both are empty, all positions are processed. |
| `BENATS_ZHAOPIN_TARGET_JOB` | Sets the Zhaopin target position; an alternative to the "Zhaopin target position" selector in the UI. When both are empty, no position filter is applied. |

## Feishu push setup (optional)

When enabled, each resume-screening run pushes one business overview, while phone screening pushes one organized record per candidate. Appended work notifies only new results that have not been pushed successfully; a full re-screen after criteria changes is notified as a new version.

The bot-creation labels below belong to Feishu and may vary with its client interface. Talent Hub only consumes the resulting Webhook URL and optional signature secret.

1. In the Feishu desktop client, open the target group → top-right settings → Group bots → Add bot → **Custom bot** → set a name and add it.
2. Copy the generated **Webhook URL** (e.g. `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`).
3. In the app's Settings dialog, under the "Feishu push" section: check "Automatically push results to the Feishu group when tasks finish", paste the Webhook URL, click "Test Feishu link" to verify, then save.

> [!TIP]
>
> - If signature verification is not enabled, leave the signature secret blank. If it is enabled, enter the generated secret in "Feishu push · Signature secret" on Windows; on macOS, set `TALENT_HUB_FEISHU_SIGN_SECRET` before launch.
> - Talent Hub does not automatically insert custom keywords or adapt to an IP allowlist. Before enabling either Feishu-side rule, ensure that the app's messages and request source satisfy it.
> - Resume messages contain statistics, candidate names, conclusions, and one-line judgments. Phone messages contain the organized record shown in the app, but do not separately append the raw transcript, facts list, or citation fields. Oversized phone messages are truncated with a prompt to view the full record in Talent Hub.
> - Before sending, the app replaces common mobile-number, landline, and email formats. Unusual formats may not be detected, so confirm that the message content is suitable for the target group before enabling push.

## Windows build

1. Install build dependencies:

   ```powershell
   python -m pip install -r requirements-build.txt
   ```

2. Run the build script:

   ```powershell
   .\scripts\build_windows.ps1
   ```

The portable executable is written to `release\<version>\portable\TalentHub\TalentHub.exe`; end users do not need Python. When Inno Setup 7 or 6 is detected, the installer is written to `release\<version>\TalentHub-Setup-<version>.exe`; otherwise only the portable build is produced. The script generates the icon, version information, and third-party notices, then runs health-check and startup smoke tests on the portable app.

## macOS build

macOS artifacts must be built on macOS. Install build dependencies explicitly before building:

```bash
python -m pip install -r requirements-build.txt
bash scripts/build_macos.sh
```

The script creates `dist/TalentHub.app` and `release/<version>/macos/TalentHub-macOS-<version>.zip`, then runs a local startup smoke test. The macOS package is currently unsigned and not notarized; organization distribution should add Apple Developer ID signing and notarization.

## Data & security

- App data is stored by default on the user's machine (Windows: `%LOCALAPPDATA%\TalentHub`; macOS: `~/.local/share/TalentHub`), including settings, original task materials, parsed JD text, result files, phone transcripts, and organized records. Parsed resume text is saved only when the setting "Keep parsed resume text in the local task folder" is enabled.
- On Windows, API keys, ASR keys, and Feishu signature secrets are encrypted with DPAPI; on macOS, secrets are provided through environment variables. Plaintext secrets are never returned by the API.
- The service listens only on the loopback address, and all API requests require a session token.
- When criteria are generated, the job description is sent to the configured model service; when candidates are evaluated, parsed resume text is sent to that model service. For phone screening, original recording content is sent to Volcano Engine ASR, and the transcript is sent to the model service. Evaluate each provider's data handling and compliance before use.
- Feishu push sends the configured message content to Feishu servers through a Webhook. Keep the Webhook URL private; the masking boundary for outbound content is described in the Feishu push setup tip.
- With Boss Connect enabled, the app uses a signed-in BOSS Zhipin session through the local boss-cli to read open positions, candidates, and attached resumes; HR-approved outreach actions are sent to BOSS Zhipin by boss-cli in the local Chrome and go through no other third-party service. This data is likewise stored in the local data directory.
- Attendance and candidate data are stored in local SQLite (`attendance.db`, `recruitment.db`). With automatic Feishu sync enabled, the app pulls check-in results from the Feishu attendance API as a custom enterprise app; the Feishu app secret is stored in the local SQLite database.
- Keep manual review for critical roles, campus hires, scarce talent, and high-risk rejections.

## Project layout

| Directory | Description |
| --- | --- |
| `app/` | FastAPI service, screening & phone pipelines, model client, runtime tools, `connectors/` recruiting-platform integrations, `attendance/` attendance management, and `recruitment/` candidate follow-up |
| `frontend/` | React + TypeScript frontend project (Vite build) |
| `boss-cli/` | BOSS Zhipin automation CLI source (deployed separately, invoked as a subprocess) |
| `zhaopin-cli/` | Zhaopin automation CLI source (deployed separately, invoked as a subprocess) |
| `packaging/` | PyInstaller and Inno Setup packaging config |
| `scripts/` | Build and release verification scripts |
| `docs/references/` | External background reference files (not part of the app) |
| `assets/` | Application icon assets |

## Limitations

Screening and phone-summarization accuracy depends on JD clarity, resume/recording parsability, model and ASR capability, service stability, and role criteria. Resume screening uses evidence checks for tiering; phone summaries use a senior-recruiter prompt, produce structured results, and remain subject to HR review. Protected-attribute filtering, prompt constraints, and format-based redaction do not replace human review. The system cannot promise zero errors, and final hiring decisions must be made by authorized personnel.

Boss Connect operates a BOSS Zhipin account through automation, so it may be affected by that platform's terms of service, account risk controls, and interface changes. Message wording, sending frequency, and candidate communication are the operator's responsibility; the app only guarantees that approved actions are executed as reviewed and makes no commitment about platform-side delivery or account status. Candidate information obtained from the platform is personal information, and its use must comply with applicable laws and your organization's policies.
