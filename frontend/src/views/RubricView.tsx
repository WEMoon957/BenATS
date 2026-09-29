// 岗位专属加权评分视图：粘贴 JD → 生成评分标准 → 上传简历 → 自动打分 → 导出 Excel。

import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Button } from "../ui/Button";

export interface RubricViewProps {
  onToast: (message: string) => void;
}

interface RubricItem {
  id: string;
  name: string;
  weight: number;
  rubric: string;
}
interface RubricGroup {
  name: string;
  items: RubricItem[];
}
interface Rubric {
  job_title: string;
  job_context: string;
  groups: RubricGroup[];
  veto_rules: { name: string; criterion: string }[];
  warning_rules: { name: string; criterion: string }[];
  bonus_rules: { name: string; points: number; criterion: string }[];
}
interface ScoreResult {
  candidate_name: string;
  base_score: number;
  total: number;
  grade: string;
  action: string;
  priority: string;
  veto_hits: string[];
  warnings: string[];
  highlight: string;
  risk: string;
  phone_questions: string[];
  error?: string;
}

const GRADE_COLOR: Record<string, string> = { S: "#315fae", A: "#1e7a35", B: "#a15c05", D: "#aa3a30" };

interface JobItem {
  id: string;
  title: string;
}

export function RubricView({ onToast }: RubricViewProps) {
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [rubric, setRubric] = useState<Rubric | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [results, setResults] = useState<ScoreResult[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<{ jobs: JobItem[] }>("/api/jobs").then((r) => setJobs(r.jobs || [])).catch(() => {});
  }, []);

  const generate = async () => {
    if (!selectedJobId) return;
    setBusy(true);
    try {
      const r = await api<{ rubric: Rubric }>("/api/rubric/generate-by-job", {
        method: "POST",
        body: JSON.stringify({ job_id: selectedJobId }),
      });
      setRubric(r.rubric);
      setResults([]);
      onToast("评分标准已生成");
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const score = async () => {
    if (!rubric || files.length === 0) return;
    setBusy(true);
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      const parsed = await api<{ name: string; text: string }[]>("/api/rubric/parse", { method: "POST", body: formData });
      const scored = await api<ScoreResult[]>("/api/rubric/score", {
        method: "POST",
        body: JSON.stringify({ rubric, resumes: parsed }),
      });
      setResults(scored);
    } catch (error) {
      onToast((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const exportExcel = async () => {
    if (!rubric || results.length === 0) return;
    try {
      const res = await api<Response>("/api/rubric/export", {
        method: "POST",
        body: JSON.stringify({ rubric, results }),
      });
      const blob = await (res as unknown as Blob);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${rubric.job_title}-简历初筛分析表.xlsx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onToast((error as Error).message);
    }
  };

  const ordered = [...results].sort((a, b) => b.total - a.total);

  return (
    <div className="rubric-view">
      <div className="boss-panel">
        <div className="subsection-heading"><h3>选择岗位</h3></div>
        <select
          className="rubric-select"
          value={selectedJobId}
          onChange={(e) => setSelectedJobId(e.target.value)}
        >
          <option value="">选择岗位…</option>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>{j.title}</option>
          ))}
        </select>
        <div className="boss-actions" style={{ marginTop: 10 }}>
          <Button variant="primary" busy={busy} disabled={!selectedJobId} onClick={() => void generate()}>
            AI 生成评分标准
          </Button>
        </div>
      </div>

      {rubric && (
        <div className="boss-panel">
          <div className="subsection-heading">
            <h3>{rubric.job_title} · 评分标准</h3>
            <span>{rubric.groups.reduce((s, g) => s + g.items.length, 0)} 项 · 100 分</span>
          </div>
          {rubric.job_context && <p className="boss-lead">{rubric.job_context}</p>}
          <table className="att-table">
            <thead>
              <tr><th>维度</th><th>评分项</th><th>权重</th><th>判分规则</th></tr>
            </thead>
            <tbody>
              {rubric.groups.map((g) =>
                g.items.map((it) => (
                  <tr key={it.id}>
                    <td>{g.name}</td>
                    <td>{it.id} {it.name}</td>
                    <td>{it.weight}</td>
                    <td>{it.rubric}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          {rubric.veto_rules.length > 0 && (
            <p className="rubric-veto">
              <strong>一票否决：</strong>
              {rubric.veto_rules.map((v) => v.name).join("、")}
            </p>
          )}
        </div>
      )}

      <div className="boss-panel">
        <div className="subsection-heading"><h3>上传简历 · 自动打分</h3></div>
        <input
          type="file"
          multiple
          accept=".pdf,.doc,.docx,.txt,.md,.png,.jpg,.jpeg"
          onChange={(e) => setFiles(Array.from(e.target.files || []))}
        />
        <div className="boss-actions" style={{ marginTop: 10 }}>
          <Button variant="primary" busy={busy} disabled={!rubric || files.length === 0} onClick={() => void score()}>
            自动打分（{files.length} 份）
          </Button>
          {results.length > 0 && (
            <Button variant="secondary" onClick={() => void exportExcel()}>导出 Excel</Button>
          )}
        </div>
      </div>

      {results.length > 0 && (
        <div className="boss-panel">
          <div className="subsection-heading"><h3>评分结果</h3></div>
          <table className="att-table">
            <thead>
              <tr><th>排名</th><th>姓名</th><th>基础分</th><th>加分</th><th>总分</th><th>等级</th><th>处置</th><th>一句话</th></tr>
            </thead>
            <tbody>
              {ordered.map((r, i) => (
                <tr key={r.candidate_name + i}>
                  <td>{i + 1}</td>
                  <td>{r.candidate_name}</td>
                  <td>{r.base_score}</td>
                  <td>{r.total - r.base_score}</td>
                  <td>{r.total}</td>
                  <td><span className="rubric-grade" style={{ background: GRADE_COLOR[r.grade] || "#888" }}>{r.grade}</span></td>
                  <td>{r.error || r.action}</td>
                  <td>{r.error || r.highlight}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
