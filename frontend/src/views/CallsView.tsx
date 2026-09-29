// 电话约谈视图：对 S/A 级候选人，按 AI 生成的必问问题逐个打分，系统上完成电话沟通评估。

import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { Button } from "../ui/Button";

export interface CallsViewProps {
  onToast: (message: string) => void;
}

interface Candidate {
  id: number;
  name: string;
  job_keyword: string;
  stage: string;
  pre_score: string;
  pre_score_reason: string;
  score_detail: string;
  call_score: string;
}

interface CallQ {
  question: string;
  score: number;
  note: string;
}

interface ScoreDetail {
  highlight?: string;
  risk?: string;
  phone_questions?: string[];
}

const SCORE_OPTIONS = [1, 2, 3, 4, 5];
const SCORE_LABEL: Record<number, string> = { 1: "很差", 2: "较差", 3: "一般", 4: "良好", 5: "优秀" };

export function CallsView({ onToast }: CallsViewProps) {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [current, setCurrent] = useState<Candidate | null>(null);
  const [questions, setQuestions] = useState<CallQ[]>([]);
  const [conclusion, setConclusion] = useState("pass");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api<{ candidates: Candidate[] }>("/api/recruitment/candidates?stage=screening&limit=500");
      setCandidates(r.candidates || []);
    } catch (error) {
      onToast((error as Error).message);
    }
  }, [onToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCandidate = (c: Candidate) => {
    setCurrent(c);
    let detail: ScoreDetail = {};
    try {
      detail = c.score_detail ? (JSON.parse(c.score_detail) as ScoreDetail) : {};
    } catch {
      detail = {};
    }
    try {
      const saved = c.call_score ? (JSON.parse(c.call_score) as { questions?: CallQ[]; conclusion?: string; note?: string }) : null;
      if (saved && Array.isArray(saved.questions) && saved.questions.length > 0) {
        setQuestions(saved.questions);
        setConclusion(saved.conclusion || "pass");
        setNote(saved.note || "");
        return;
      }
    } catch {
      /* 忽略已存评分解析失败 */
    }
    setQuestions((detail.phone_questions || []).map((q) => ({ question: q, score: 0, note: "" })));
    setConclusion("pass");
    setNote("");
  };

  const setQ = (index: number, patch: Partial<CallQ>) => {
    setQuestions((prev) => prev.map((q, i) => (i === index ? { ...q, ...patch } : q)));
  };

  const save = async () => {
    if (!current) return;
    setBusy(true);
    try {
      await api(`/api/recruitment/candidates/${current.id}/call-score`, {
        method: "POST",
        body: JSON.stringify({ questions, conclusion, note }),
      });
      onToast("电话评分已保存");
      setCurrent(null);
      await load();
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const avg = questions.length
    ? (questions.reduce((s, q) => s + (q.score || 0), 0) / questions.length).toFixed(1)
    : "0";

  return (
    <div className="calls-view">
      <div className="calls-list boss-panel">
        <div className="subsection-heading">
          <h3>待电话约谈（S / A 级）</h3>
          <span>{candidates.length} 人</span>
        </div>
        {candidates.length === 0 ? (
          <p className="boss-empty">暂无待约谈候选人，评分后 S/A 级会自动进入这里。</p>
        ) : (
          <ul className="boss-outreach-list">
            {candidates.map((c) => (
              <li className="boss-outreach-item" key={c.id}>
                <div className="boss-outreach-main">
                  <div className="boss-outreach-title">
                    <strong>{c.name}</strong>
                    <span className="rec-score">{c.pre_score}</span>
                  </div>
                  {c.job_keyword ? <span className="boss-outreach-meta">岗位：{c.job_keyword}</span> : null}
                  {c.call_score ? <span className="boss-outreach-meta">已评分 · 点开可修改</span> : null}
                </div>
                <Button variant="primary" onClick={() => openCandidate(c)}>
                  {c.call_score ? "查看/修改" : "开始评分"}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {current && (
        <div className="calls-detail boss-panel">
          <div className="subsection-heading">
            <h3>电话沟通评分 · {current.name}</h3>
            <Button variant="secondary" onClick={() => setCurrent(null)}>关闭</Button>
          </div>
          <CallHighlight candidate={current} />
          <div className="subsection-heading"><h3>必问问题（逐项打分）</h3></div>
          {questions.length === 0 ? (
            <p className="boss-empty">该候选人暂无 AI 生成的必问问题，可在下方直接填结论。</p>
          ) : (
            <ul className="call-question-list">
              {questions.map((q, i) => (
                <li className="call-question" key={i}>
                  <div className="call-question-text">{i + 1}. {q.question}</div>
                  <div className="call-question-score">
                    {SCORE_OPTIONS.map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={q.score === s ? "call-score-btn active" : "call-score-btn"}
                        title={SCORE_LABEL[s]}
                        onClick={() => setQ(i, { score: s })}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  <input
                    className="call-question-note"
                    value={q.note}
                    onChange={(e) => setQ(i, { note: e.target.value })}
                    placeholder="记录回答要点（可选）"
                  />
                </li>
              ))}
            </ul>
          )}
          <div className="call-conclusion">
            <div className="call-avg">平均分：<strong>{avg}</strong> / 5</div>
            <div className="call-conclusion-row">
              <span>结论：</span>
              {[
                { value: "pass", label: "通过（进面试）" },
                { value: "pending", label: "待定" },
                { value: "reject", label: "淘汰" },
              ].map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  className={conclusion === opt.value ? `call-concl-btn active is-${opt.value}` : "call-concl-btn"}
                  onClick={() => setConclusion(opt.value)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <textarea
              className="call-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="总体备注（可选）"
            />
          </div>
          <div className="boss-actions">
            <Button variant="primary" busy={busy} onClick={() => void save()}>保存评分</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function CallHighlight({ candidate }: { candidate: Candidate }) {
  let detail: ScoreDetail = {};
  try {
    detail = candidate.score_detail ? (JSON.parse(candidate.score_detail) as ScoreDetail) : {};
  } catch {
    detail = {};
  }
  if (!detail.highlight && !detail.risk) return null;
  return (
    <div className="call-highlight">
      {detail.highlight ? <p><strong>亮点：</strong>{detail.highlight}</p> : null}
      {detail.risk ? <p><strong>风险：</strong>{detail.risk}</p> : null}
    </div>
  );
}
