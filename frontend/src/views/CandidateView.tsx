// 候选人跟进看板：按阶段分列，支持添加、评分、批量打招呼、阶段推进。

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { t } from "../i18n";
import { Button } from "../ui/Button";

export interface CandidateViewProps {
  onToast: (message: string) => void;
}

interface Candidate {
  id: number;
  name: string;
  job_keyword: string;
  job_id: string;
  stage: string;
  stage_label: string;
  pre_score: string;
  pre_score_reason: string;
  score_detail: string;
  phone: string;
  note: string;
}

interface StageItem {
  value: string;
  label: string;
}

const COLUMNS: { key: string; label: string; stages: string[] }[] = [
  { key: "greeting_pending", label: "待打招呼", stages: ["discovered", "greeting_pending"] },
  { key: "greeted", label: "已打招呼", stages: ["greeted"] },
  { key: "resume_received", label: "待评分", stages: ["resume_received"] },
  { key: "screening", label: "筛选中", stages: ["screening", "screened"] },
  { key: "interviewing", label: "面试中", stages: ["interviewing"] },
  { key: "done", label: "已结束", stages: ["offered", "rejected", "skipped"] },
];

const SCORE_SHORT: Record<string, string> = {
  "S电话沟通": "S",
  "A优先约面": "A",
  "B电话确认": "B",
  "C不推进": "C",
};

function scoreClass(score: string): string {
  if (!score) return "";
  return `is-${SCORE_SHORT[score]?.toLowerCase() ?? "b"}`;
}

function CandidateCard({
  candidate,
  stages,
  selected,
  onToggle,
  onMove,
  onDelete,
}: {
  candidate: Candidate;
  stages: StageItem[];
  selected: boolean;
  onToggle: (id: number) => void;
  onMove: (id: number, stage: string) => void;
  onDelete: (candidate: Candidate) => void;
}) {
  const [open, setOpen] = useState(false);
  let detail: Record<string, unknown> | null = null;
  try {
    detail = candidate.score_detail ? (JSON.parse(candidate.score_detail) as Record<string, unknown>) : null;
  } catch {
    detail = null;
  }
  const questions = Array.isArray(detail?.phone_questions) ? (detail!.phone_questions as string[]) : [];
  return (
    <li className={`rec-card ${scoreClass(candidate.pre_score)}`}>
      <div className="rec-card-head" onClick={() => setOpen(!open)} role="button" tabIndex={0}>
        {candidate.stage === "greeting_pending" && (
          <input
            type="checkbox"
            checked={selected}
            onChange={(e) => { e.stopPropagation(); onToggle(candidate.id); }}
            onClick={(e) => e.stopPropagation()}
            aria-label={candidate.name}
          />
        )}
        <strong>{candidate.name}</strong>
        {candidate.pre_score && <span className="rec-score">{candidate.pre_score}</span>}
        {detail && <span className="rec-score rec-score-total">{String(detail.total ?? "")}</span>}
      </div>
      <div className="rec-card-meta">
        <span>{candidate.job_keyword || "未关联岗位"}</span>
      </div>
      {candidate.pre_score_reason && <p className="rec-card-reason">{candidate.pre_score_reason}</p>}
      {open && detail && (
        <div className="rec-card-detail">
          {detail.highlight ? <p className="rec-detail-line"><strong>亮点：</strong>{String(detail.highlight)}</p> : null}
          {detail.risk ? <p className="rec-detail-line"><strong>风险：</strong>{String(detail.risk)}</p> : null}
          {questions.length > 0 && (
            <div className="rec-detail-questions">
              <strong>电话必问：</strong>
              <ol>{questions.map((q, i) => <li key={i}>{q}</li>)}</ol>
            </div>
          )}
        </div>
      )}
      <div className="rec-card-actions">
        <select
          value={candidate.stage}
          onChange={(e) => onMove(candidate.id, e.target.value)}
          aria-label={t("recMoveStage")}
        >
          {stages.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <button type="button" className="rec-delete" onClick={() => onDelete(candidate)} title={t("delete")}>
          ×
        </button>
      </div>
    </li>
  );
}

export function CandidateView({ onToast }: CandidateViewProps) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [stages, setStages] = useState<StageItem[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([
        api<{ candidates: Candidate[] }>("/api/recruitment/candidates"),
        api<{ stages: StageItem[] }>("/api/recruitment/stages"),
      ]);
      setCandidates(c.candidates);
      setStages(s.stages);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const scoreAll = async () => {
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; detail?: string; scored?: number; failed?: number }>(
        "/api/recruitment/candidates/score",
        { method: "POST", body: JSON.stringify({ candidate_ids: null }) },
      );
      if (r.ok) onToast(t("recScoreDone", { scored: r.scored ?? 0, passed: 0, rejected: 0, failed: r.failed ?? 0 }));
      else onToast(r.detail || t("recScoreFailed"));
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const greetSelected = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBusy(true);
    try {
      await api("/api/recruitment/candidates/greet", { method: "POST", body: JSON.stringify({ ids }) });
      setSelected(new Set());
      onToast(t("recGreetDone", { count: ids.length }));
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const move = async (id: number, stage: string) => {
    try {
      await api(`/api/recruitment/candidates/${id}`, { method: "PATCH", body: JSON.stringify({ stage }) });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  const remove = async (candidate: Candidate) => {
    if (!window.confirm(t("recDeleteConfirm", { name: candidate.name }))) return;
    try {
      await api(`/api/recruitment/candidates/${candidate.id}`, { method: "DELETE" });
      await load();
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  const pendingCount = candidates.filter((c) => c.stage === "greeting_pending").length;

  return (
    <div className="rec-view">
      <div className="rec-toolbar">
        <Button variant="primary" busy={busy} onClick={() => void scoreAll()}>
          {t("recScoreAll")}
        </Button>
        <Button variant="secondary" disabled={selected.size === 0} busy={busy} onClick={() => void greetSelected()}>
          {t("recGreetSelected", { count: selected.size })}
        </Button>
        <Button variant="secondary" onClick={() => setAdding(true)}>
          {t("recAddCandidate")}
        </Button>
        <span className="rec-pending-count">{t("recPendingCount", { count: pendingCount })}</span>
      </div>

      <div className="rec-board">
        {COLUMNS.map((col) => {
          const items = candidates.filter((c) => col.stages.includes(c.stage));
          return (
            <div key={col.key} className="rec-column">
              <div className="rec-column-head">
                <span>{t(`recCol_${col.key}`)}</span>
                <span className="rec-column-count">{items.length}</span>
              </div>
              <ul className="rec-card-list">
                {items.map((c) => (
                  <CandidateCard
                    key={c.id}
                    candidate={c}
                    stages={stages}
                    selected={selected.has(c.id)}
                    onToggle={toggle}
                    onMove={(id, s) => void move(id, s)}
                    onDelete={remove}
                  />
                ))}
                {items.length === 0 && <li className="rec-empty">{t("recEmpty")}</li>}
              </ul>
            </div>
          );
        })}
      </div>

      {adding && (
        <AddCandidateDialog
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            void load();
          }}
          onToast={onToast}
        />
      )}
    </div>
  );
}

function AddCandidateDialog({ onClose, onSaved, onToast }: { onClose: () => void; onSaved: () => void; onToast: (m: string) => void }) {
  const [name, setName] = useState("");
  const [jobKeyword, setJobKeyword] = useState("");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      await api("/api/recruitment/candidates", {
        method: "POST",
        body: JSON.stringify({ name, job_keyword: jobKeyword, phone }),
      });
      onSaved();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="att-modal-backdrop" role="presentation" onClick={onClose}>
      <div className="att-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{t("recAddCandidate")}</h3>
        <label className="field">
          <span>{t("recName")}</span>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("recJob")}</span>
          <input value={jobKeyword} onChange={(e) => setJobKeyword(e.target.value)} />
        </label>
        <label className="field">
          <span>{t("recPhone")}</span>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} />
        </label>
        <div className="att-modal-actions">
          <Button variant="secondary" onClick={onClose}>{t("cancel")}</Button>
          <Button variant="primary" busy={busy} disabled={!name} onClick={() => void save()}>{t("save")}</Button>
        </div>
      </div>
    </div>
  );
}
