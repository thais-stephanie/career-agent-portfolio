import { useEffect, useState } from "react";
import { api } from "../api";
import { Banner, Eyebrow, Pill, Swatch } from "../components";
import { APP_STATUS_TONES, STR } from "../labels";
import { useApp } from "../state";

// Candidate home: who, the one primary action, what needs attention, recent work.
// Counts live in the header line and the Experience group — never as a row of
// stat cards ahead of the things that actually need a decision.
export function Overview() {
  const app = useApp();
  const [config, setConfig] = useState<any>(null);
  const [exp, setExp] = useState<any>(null);
  const [apps, setApps] = useState<any[]>([]);

  useEffect(() => {
    if (!app.candidateId) return;
    void api.config(app.candidateId).then(setConfig).catch(() => setConfig(null));
    void api.experience(app.candidateId, false).then(setExp).catch(() => setExp(null));
    void api.applications(app.candidateId).then(setApps).catch(() => setApps([]));
  }, [app.candidateId]);

  const c = config?.candidate;
  const needsReview = (exp?.needs_review ?? 0) + (exp?.conflicting ?? 0);
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>CANDIDATE HOME</Eyebrow>
          <h1 className="page-title">{c?.name ?? app.candidateName}</h1>
          <div className="page-sub">
            {[c?.location,
              plural(config?.base_resumes?.length ?? 0, "base resume"),
              exp ? plural(exp.total, "experience detail") : null,
              plural(apps.length, "application")].filter(Boolean).join(" · ")}
          </div>
        </div>
        <button className="btn" onClick={() => app.setScreen("tailor")}>{STR.tailorCta}</button>
      </header>

      <div className="page-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {needsReview > 0 && (
          <Banner tone="yellow-soft"
                  action={<button className="btn2 sm" onClick={() => app.setScreen("experience")}>Review them</button>}>
            <b>{plural(needsReview, "detail")} need{needsReview === 1 ? "s" : ""} your review.</b>{" "}
            <span style={{ color: "var(--body)" }}>Your sources disagree, or a detail is unconfirmed.</span>
          </Banner>
        )}

        <div style={{ display: "flex", flexWrap: "wrap", gap: 14, alignItems: "flex-start" }}>
          <div className="rows" style={{ flex: "2 1 380px" }}>
            <div className="rhead">
              <span>Recent applications</span>
              <button className="quiet" onClick={() => app.setScreen("applications")}>See all {"→"}</button>
            </div>
            {apps.slice(0, 5).map((a) => (
              <div key={a.id} className="rrow" style={{ alignItems: "center" }}>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{a.role || "Untitled role"}</div>
                  <div style={{ fontSize: 11, color: "var(--muted)" }}>
                    {[a.company, a.date?.slice(0, 10), a.base_resume].filter(Boolean).join(" · ")}
                  </div>
                </div>
                {a.match != null && <span className="mono">{Math.round(a.match * 100)}%</span>}
                <Pill px tone={APP_STATUS_TONES[a.status] ?? "yellow"}>{a.status}</Pill>
              </div>
            ))}
            {!apps.length && (
              <div className="rrow" style={{ color: "var(--muted)" }}>No applications yet. Tailor your first resume to start.</div>
            )}
          </div>

          <div className="rows" style={{ flex: "1 1 260px" }}>
            <div className="rhead"><span>Experience & evidence</span><span className="mono">{exp?.total ?? "–"}</span></div>
            {([
              ["Confirmed by you", exp?.confirmed, "mint"],
              ["Supported by sources", exp?.supported, "mint-soft"],
              ["Needs review", exp?.needs_review, "yellow"],
              ["Conflicting", exp?.conflicting, "pink-soft"],
            ] as const).map(([label, n, tone]) => (
              <div key={label} className="rrow" style={{ alignItems: "center", padding: "6px 12px" }}>
                <Swatch tone={tone} />
                <span style={{ flex: 1, fontSize: 12 }}>{label}</span>
                <span className="mono">{n ?? "–"}</span>
              </div>
            ))}
            <div className="rfoot">
              <span className="faint">{STR.filesNote}</span>
              <button className="btn2 sm" onClick={() => app.setScreen("experience")}>Open</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
