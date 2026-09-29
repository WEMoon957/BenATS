// 招聘工作台：把候选人、触达审核、电话约谈、评分标准整合到一个入口，Tab 切换。

import { useEffect, useState } from "react";
import { registerView } from "../router";
import { CandidateView } from "./CandidateView";
import { BossView } from "./BossView";
import { CallsView } from "./CallsView";
import { RubricView } from "./RubricView";
import { RecruitmentWizard } from "./RecruitmentWizard";

export interface RecruitmentWorkbenchProps {
  onToast: (message: string) => void;
}

type TabKey = "wizard" | "candidates" | "outreach" | "calls" | "rubric";

const TABS: { key: TabKey; label: string }[] = [
  { key: "wizard", label: "作业台" },
  { key: "outreach", label: "触达审核" },
  { key: "candidates", label: "候选人" },
  { key: "calls", label: "电话约谈" },
  { key: "rubric", label: "评分标准" },
];

export function RecruitmentWorkbench({ onToast }: RecruitmentWorkbenchProps) {
  const [tab, setTab] = useState<TabKey>("wizard");

  useEffect(() => {
    registerView("recruitment", {});
  }, []);

  return (
    <section id="recruitmentView" className="recruitment-view">
      <nav className="wb-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={tab === t.key}
            className={tab === t.key ? "wb-tab active" : "wb-tab"}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      <div className="wb-body">
        {tab === "wizard" && <RecruitmentWizard onToast={onToast} />}
        {tab === "candidates" && <CandidateView onToast={onToast} />}
        {tab === "outreach" && <BossView onToast={onToast} />}
        {tab === "calls" && <CallsView onToast={onToast} />}
        {tab === "rubric" && <RubricView onToast={onToast} />}
      </div>
    </section>
  );
}
