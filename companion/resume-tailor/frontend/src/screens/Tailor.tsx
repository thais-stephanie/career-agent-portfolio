import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Card, CheckOption, Chip, Disclosure, Eyebrow, Pill, Strip } from "../components";
import { ExportMenu, ExportToast, useExportNotice } from "../export";
import { resultTone, STR } from "../labels";
import { useApp } from "../state";

// Tabs: the answer first (Match), then the posting, then how the resume was checked
// and built. Plan / evidence / checks live in ONE tab behind disclosures so nobody
// has to hold six concepts at once. Developer data exists only in Advanced mode.
type Tab = "match" | "job" | "checks" | "dev";

const ROWS_FIRST = 8;

export function Tailor() {
  const app = useApp();
  const [resumes, setResumes] = useState<any[]>([]);
  const [jd, setJd] = useState("");
  const [resumeId, setResumeId] = useState("");
  const [opts, setOpts] = useState({ evidence_only_claims: true, max_two_pages: true, use_llm: false });
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [appId, setAppId] = useState("");
  const [view, setView] = useState<any>(null);
  const [advData, setAdvData] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("match");
  const [showAllRows, setShowAllRows] = useState(false);
  const [draft, setDraft] = useState<any>(null);
  const [whyPanel, setWhyPanel] = useState<{ bulletId: string; text: string } | null>(null);
  const [editWarning, setEditWarning] = useState<any>(null);
  const [editing, setEditing] = useState<{ bulletId: string; text: string } | null>(null);
  const exportNotice = useExportNotice();

  useEffect(() => {
    if (!app.candidateId) return;
    void api.resumes(app.candidateId).then((rows) => {
      setResumes(rows);
      const def = rows.find((r) => r.default) ?? rows[0];
      if (def) setResumeId(def.id);
    }).catch(() => setResumes([]));
  }, [app.candidateId]);

  const refreshDraft = (id = appId) => {
    if (id) void api.draft(app.candidateId, id).then(setDraft).catch(() => setDraft(null));
  };

  const analyze = async () => {
    setError("");
    if (jd.trim().length < 40) { setError("Paste the job description first."); return; }
    setRunning(true);
    try {
      const { application_id } = await api.startTailor(app.candidateId, jd, resumeId, opts);
      setAppId(application_id);
      for (let i = 0; i < 240; i++) {
        const d = await api.application(app.candidateId, application_id, app.advanced);
        if (d.status.status === "done") {
          setView(d.view);
          setAdvData(d.advanced ?? null);
          refreshDraft(application_id);
          setRunning(false);
          return;
        }
        if (d.status.status === "error") throw new Error("Something went wrong while tailoring. " + (d.status.error ?? ""));
        await new Promise((r) => setTimeout(r, 700));
      }
      throw new Error("This is taking unusually long. Check the Applications screen in a moment.");
    } catch (e: any) {
      setError(e.message);
      setRunning(false);
    }
  };

  const edit = async (body: any) => {
    const out = await api.draftEdit(app.candidateId, appId, body, app.advanced);
    setDraft(out);
    if (out.edit_check && !out.edit_check.ok) setEditWarning({ ...out.edit_check, bulletId: body.bullet_id });
    return out;
  };

  const undoEdit = async () => {
    const out = await api.draftUndo(app.candidateId, appId);
    setDraft(out);
    setEditWarning(null);
  };

  const reset = () => { setView(null); setDraft(null); setAppId(""); setEditing(null); setEditWarning(null); setWhyPanel(null); };

  const tabs: [Tab, string][] = useMemo(() => {
    const base: [Tab, string][] = [["match", "Match"], ["job", "Job posting"], ["checks", "Checks"]];
    // the Developer tab only EXISTS in Advanced mode
    return app.advanced ? [...base, ["dev", "Developer data"]] : base;
  }, [app.advanced]);

  const rows = view?.match?.rows ?? [];
  const visibleRows = showAllRows ? rows : rows.slice(0, ROWS_FIRST);
  const resume = draft?.resume;
  const baseName = resumes.find((r) => r.id === resumeId)?.name ?? "your base resume";
  const checksOk = view ? view.checks.filter((c: any) => c.level === "ok").length : 0;
  const pagesLine = view
    ? (view.pages.verified ? `${STR.pagesVerified(view.pages.actual)} · ${view.pages.how}` : `About ${view.pages.estimate} pages (estimated)`)
    : "";

  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>TAILOR</Eyebrow>
          <h1 className="page-title">{view?.job?.role ?? "Tailor a resume"}</h1>
          <div className="page-sub">
            {view ? [view.job.company, `from ${baseName}`].filter(Boolean).join(" · ")
              : "Paste a job description to match it against your experience."}
          </div>
        </div>
        {view && (
          <div style={{ display: "flex", gap: 10 }}>
            <button className="btn2" onClick={() => app.setScreen("applications")}>{STR.saveToApplications}</button>
            <ExportMenu candidateId={app.candidateId} applicationId={appId} onNotice={exportNotice.show}
                        blockedReason={editing ? STR.exportBlockedEditing : undefined} />
          </div>
        )}
      </header>
      <ExportToast notice={exportNotice.notice} onDismiss={exportNotice.dismiss} />

      <div className="page-body" style={{ maxWidth: 1700, display: "flex", flexWrap: "wrap", gap: 14 }}>
        {/* ------------------------------------------------------------ setup */}
        <div style={{ flex: view ? "0 1 230px" : "1 1 250px", minWidth: 230 }}>
          {view ? (
            // after a run the setup folds to a summary: the results are the point now
            <div className="rows">
              <div className="rhead"><span>Setup</span><button className="quiet" onClick={reset}>Change</button></div>
              <div className="rrow" style={{ display: "block" }}>
                <div className="eyebrow" style={{ marginBottom: 3 }}>JOB DESCRIPTION</div>
                <div style={{ fontSize: 12, color: "var(--body)", maxHeight: 72, overflow: "hidden" }}>{jd.slice(0, 220)}{jd.length > 220 ? "…" : ""}</div>
              </div>
              <div className="rrow" style={{ display: "block" }}>
                <div className="eyebrow" style={{ marginBottom: 3 }}>BASE RESUME</div>
                <div style={{ fontSize: 12 }}>{baseName}</div>
              </div>
              <div className="rrow" style={{ display: "block", fontSize: 12, color: "var(--body)" }}>
                {opts.evidence_only_claims ? "✓ Only evidenced wording" : "– Any wording"}
                <br />{opts.max_two_pages ? "✓ Two pages" : "– No page limit"}
              </div>
            </div>
          ) : (
            <Card>
              <div className="eyebrow">SETUP</div>
              <h3>Job description</h3>
              <textarea rows={8} value={jd} onChange={(e) => setJd(e.target.value)}
                        placeholder="Paste the job posting here" aria-label="Job description" />
              <hr className="hair" />
              <label style={{ fontSize: 12, fontWeight: 600 }}>
                Base resume
                <select value={resumeId} onChange={(e) => setResumeId(e.target.value)} style={{ marginTop: 4 }}>
                  {resumes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </select>
              </label>
              <hr className="hair" />
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <CheckOption on={opts.evidence_only_claims}
                             onToggle={() => setOpts({ ...opts, evidence_only_claims: !opts.evidence_only_claims })}
                             label="Only use evidenced wording" hint="Every sentence must be backed by your experience." />
                <CheckOption on={opts.max_two_pages}
                             onToggle={() => setOpts({ ...opts, max_two_pages: !opts.max_two_pages })}
                             label="Keep to two pages" hint="Checked in Word when it is installed." />
              </div>
              <button className="btn" style={{ width: "100%", marginTop: 12 }} onClick={analyze} disabled={running}>
                {running ? "Working…" : STR.analyze}
              </button>
              {error && <p role="alert" style={{ color: "var(--ink)", background: "var(--yellow-soft)", border: "1.5px solid var(--line)", borderRadius: 8, padding: 8, fontSize: 12, margin: "10px 0 0" }}>{error}</p>}
            </Card>
          )}
        </div>

        {/* ----------------------------------------------------------- center */}
        <div style={{ flex: "2 1 400px", minWidth: 340 }}>
          {!view ? (
            <Card><p style={{ color: "var(--muted)", margin: 0 }}>Results appear here after you analyze a job.</p></Card>
          ) : (
            <>
              <div className="tabs" style={{ marginBottom: 12 }}>
                {tabs.map(([t, label]) => (
                  <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{label}</button>
                ))}
              </div>

              {tab === "match" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <Strip items={[
                    { n: view.match.strong, l: "Strong matches", tone: "mint" },
                    { n: view.match.related, l: "Related experience", tone: "blue" },
                    { n: view.match.gaps.length, l: "Gaps", tone: "pink-soft" },
                  ]} />
                  {view.match.gaps.length > 0 && (
                    <div className="inner-box" style={{ background: "var(--tint-warm)" }}>
                      <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                        <span style={{ fontWeight: 700, fontSize: 12 }}>Gaps:</span>
                        {view.match.gaps.map((g: string) => <Chip key={g}>{g}</Chip>)}
                      </div>
                      <div className="faint" style={{ marginTop: 4 }}>{STR.gapsReassurance}</div>
                    </div>
                  )}
                  <div className="rows">
                    <div className="rhead">
                      <span>What this job asks for</span>
                      <span className="mono">{rows.length} asks</span>
                    </div>
                    {visibleRows.map((m: any, i: number) => (
                      <div key={i} className="rrow">
                        <Pill tone={resultTone(m.result)}>{m.result}</Pill>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontWeight: 600, fontSize: 13 }}>
                            {m.requirement}
                            {m.kind === "Preferred" && <span className="faint" style={{ marginLeft: 6 }}>preferred</span>}
                          </div>
                          <div style={{ fontSize: 12, color: "var(--body)" }}>{m.why}</div>
                          {(m.backed_by?.length > 0 || (app.advanced && advData)) && (
                            <Disclosure label="Why?" openLabel="Why? (hide)">
                              {(m.backed_by ?? []).map((b: any, j: number) => (
                                <div key={j} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                                  <span className="mono" style={{ color: "var(--muted)", flexShrink: 0 }}>{b.company}</span>
                                  <span>{b.text}</span>
                                </div>
                              ))}
                              {app.advanced && advData && <AdvancedRow advData={advData} requirement={m.requirement} />}
                            </Disclosure>
                          )}
                        </div>
                      </div>
                    ))}
                    {rows.length > ROWS_FIRST && (
                      <div className="rfoot">
                        <button className="quiet" onClick={() => setShowAllRows(!showAllRows)}>
                          {showAllRows ? "Show fewer" : `Show ${rows.length - ROWS_FIRST} more…`}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {tab === "job" && (
                <div className="rows">
                  <div className="rhead">
                    <span>{view.job.role}</span>
                    <span className="mono">{view.job.company || "company not named"}</span>
                  </div>
                  {view.job.conditions?.length > 0 && (
                    <div className="rrow" style={{ display: "block", fontSize: 12, color: "var(--body)" }}>
                      {view.job.conditions.map((c: string) => <div key={c}>{"◈"} {c}</div>)}
                    </div>
                  )}
                  {([["Required", view.job.required], ["Preferred", view.job.preferred]] as const).map(([k, list]) => (
                    <div key={k} className="rrow" style={{ display: "block" }}>
                      <div className="eyebrow" style={{ marginBottom: 4 }}>{k.toUpperCase()} · {list.length}</div>
                      {list.map((t: string) => <div key={t} style={{ fontSize: 12, padding: "2px 0" }}>{t}</div>)}
                      {!list.length && <div className="faint">Nothing listed as {k.toLowerCase()}.</div>}
                    </div>
                  ))}
                </div>
              )}

              {tab === "checks" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  <div className="rows">
                    <div className="rhead" style={{ background: checksOk === view.checks.length ? "var(--mint-soft)" : "var(--yellow-soft)" }}>
                      <span>{checksOk} / {view.checks.length} checks passed</span>
                      <span className="mono">{pagesLine}</span>
                    </div>
                    {view.checks.map((c: any, i: number) => (
                      <div key={i} className="rrow" style={{ alignItems: "center" }}>
                        <span style={{ width: 18, height: 18, flexShrink: 0, border: "1.5px solid var(--line)", borderRadius: 5, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700, background: c.level === "ok" ? "var(--mint)" : "var(--yellow)" }}>
                          {c.level === "ok" ? "✓" : "!"}
                        </span>
                        <span style={{ fontWeight: 600, fontSize: 12, flexShrink: 0 }}>{c.name}</span>
                        <span style={{ fontSize: 12, color: "var(--body)" }}>{c.note}</span>
                      </div>
                    ))}
                    <div className="rfoot"><span>Saves as “{view.filename}”</span></div>
                  </div>

                  <div className="rows">
                    <div className="rrow" style={{ display: "block" }}>
                      <Disclosure label="How this resume was put together" openLabel="How this resume was put together (hide)">
                        {[
                          ["mint", "Started from your base resume", `Structure and starting wording came from “${baseName}”.`],
                          ["blue", "Led with your strongest proof", "The experience most relevant to this posting moved to the top."],
                          ["yellow", "Trimmed to fit the page limit", STR.trimReassurance],
                          ["lilac", "Left gaps out on purpose", "Nothing was claimed without evidence; gaps stay visible in Match."],
                        ].map(([tone, h, body], n) => (
                          <div key={h} style={{ display: "flex", gap: 8, padding: "5px 0", borderTop: n ? "1px solid var(--rule)" : 0 }}>
                            <Pill px tone={tone as any}>{String(n + 1).padStart(2, "0")}</Pill>
                            <div><b style={{ fontSize: 12 }}>{h}</b> <span style={{ fontSize: 12 }}>{body}</span></div>
                          </div>
                        ))}
                      </Disclosure>
                    </div>
                    <div className="rrow" style={{ display: "block" }}>
                      <Disclosure label={`Every sentence, checked (${(view.resume.experience ?? []).reduce((n: number, e: any) => n + e.bullets.length, 0)} lines)`}
                                  openLabel="Every sentence, checked (hide)">
                        {(view.resume.experience ?? []).flatMap((e: any) => e.bullets.map((b: string, i: number) => (
                          <div key={e.company + i} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "4px 0", borderBottom: "1px solid var(--rule)" }}>
                            <Pill tone="mint-soft">Supported</Pill>
                            <span style={{ fontSize: 12, flex: 1 }}>{b}</span>
                            <span className="faint" style={{ flexShrink: 0 }}>{e.company}</span>
                          </div>
                        )))}
                      </Disclosure>
                    </div>
                  </div>
                </div>
              )}

              {tab === "dev" && app.advanced && (
                <Card>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <h3 style={{ margin: 0 }}>Developer data</h3>
                    <Pill px tone="lilac">TECHNICAL</Pill>
                  </div>
                  <pre className="mono" style={{ background: "var(--surface-alt)", padding: 10, borderRadius: 8, overflow: "auto", maxHeight: 500, marginBottom: 0 }}>
                    {JSON.stringify(advData ?? {}, null, 2).slice(0, 40000)}
                  </pre>
                </Card>
              )}
            </>
          )}
        </div>

        {/* ---------------------------------------------------------- preview */}
        {view && resume && (
          <div style={{ flex: "1 1 330px", minWidth: 300, display: "flex", flexDirection: "column", gap: 12 }}>
            <Card style={{ padding: 0, overflow: "hidden" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", borderBottom: "2px solid var(--line)" }}>
                <span className="eyebrow" style={{ margin: 0 }}>RESUME PREVIEW</span>
                {view.pages.verified && <Chip tone="mint">{STR.pagesVerified(view.pages.actual)}</Chip>}
              </div>
              <div className="resume-doc" style={{ padding: 12 }}>
                <div className="rname">{app.candidateName}</div>
                <div className="rrole">{resume.headline}</div>
                <div className="rrule" />
                <div className="rlabel">SUMMARY</div>
                <p style={{ margin: 0, fontSize: 12, color: "var(--body)" }}>{resume.summary.join(" ")}</p>
                <div className="rlabel">EXPERIENCE</div>
                {resume.experience.map((e: any) => (
                  <div key={e.position_id} style={{ marginBottom: 6 }}>
                    <div style={{ fontWeight: 700, fontSize: 12 }}>{e.title} {"·"} {e.company}</div>
                    {e.bullets.map((b: any) => (
                      <div key={b.id}
                           className={`bullet-box ${!b.supported ? "unsupported" : ""} ${b.hidden ? "hidden-b" : ""}`}>
                        {editing !== null && editing.bulletId === b.id ? (
                          <div>
                            <textarea rows={3} value={editing.text} aria-label="Edit resume line"
                                      onChange={(e2) => setEditing({ bulletId: b.id, text: e2.target.value })} />
                            <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                              <button className="btn sm" onClick={async () => { const txt = editing.text; await edit({ op: "bullet_text", bullet_id: b.id, text: txt }); setEditing(null); }}>Save</button>
                              <button className="btn2 sm" onClick={() => setEditing(null)}>Cancel</button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <span style={{ fontSize: 12 }}>{b.text}</span>
                            <div className="acts">
                              <button className="quiet" onClick={() => setEditing({ bulletId: b.id, text: b.text })}>Edit</button>
                              <button className="quiet" onClick={() => edit({ op: "bullet_hide", bullet_id: b.id, hidden: !b.hidden })}>
                                {b.hidden ? "Show" : "Hide"}
                              </button>
                              <button className="quiet" title={STR.whyTitle} aria-label={STR.whyTitle}
                                      onClick={() => setWhyPanel({ bulletId: b.id, text: b.text })}>Why?</button>
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 12px", background: "var(--surface-alt)", borderTop: "2px solid var(--line)", fontSize: 12 }}>
                <span>{view.pages.actual ?? Math.ceil(view.pages.estimate)} page{(view.pages.actual ?? Math.ceil(view.pages.estimate)) === 1 ? "" : "s"}</span>
                <span style={{ display: "flex", gap: 8 }}>
                  <button className="quiet" onClick={undoEdit} disabled={!draft?.can_undo}>Undo</button>
                  <button className="quiet" onClick={async () => setDraft(await api.draftRestore(app.candidateId, appId))}>
                    Restore automatic
                  </button>
                </span>
              </div>
            </Card>

            {editWarning && (
              <div className="warn-panel" style={{ background: "var(--tint-red)" }}>
                <div className="head">
                  <span>{"⚠"} {STR.editCheckTitle}</span>
                  <button className="quiet" aria-label="Dismiss" onClick={() => setEditWarning(null)}>{"✕"}</button>
                </div>
                <p style={{ margin: "0 0 8px", fontSize: 13 }}>{STR.editCheckBody}</p>
                <div className="inner-box" style={{ marginBottom: 6 }}>
                  <div className="eyebrow">{STR.supported}</div>
                  {editWarning.supported.map((s: string) => <div key={s} style={{ fontSize: 12 }}>{s}</div>)}
                </div>
                <div className="inner-box" style={{ background: "var(--pink-soft)", marginBottom: 8 }}>
                  <div className="eyebrow">{STR.notEvidenced}</div>
                  {editWarning.not_evidenced.map((s: string) => <div key={s} style={{ fontSize: 12 }}>{s}</div>)}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn sm" onClick={undoEdit}>{STR.undoEdit}</button>
                  <button className="btn2 sm" onClick={() => { setEditWarning(null); app.setScreen("experience"); }}>{STR.reviewEvidence}</button>
                </div>
              </div>
            )}

            {whyPanel && (
              <div className="warn-panel" style={{ background: "var(--lilac)" }}>
                <div className="head">
                  <span>{STR.whyTitle.toUpperCase()}</span>
                  <button className="quiet" aria-label="Dismiss" onClick={() => setWhyPanel(null)}>{"✕"}</button>
                </div>
                <p style={{ margin: "0 0 8px", fontSize: 13 }}>
                  This line answers what the posting asks for and is backed by your own experience.
                </p>
                <div className="inner-box">
                  <div className="eyebrow">{STR.backedBy}</div>
                  <div style={{ fontSize: 12 }}>Your experience library {"·"} Supported by source</div>
                </div>
                {app.advanced && advData && (
                  <pre className="mono" style={{ background: "var(--surface)", border: "1.5px solid var(--rule2)", borderRadius: 8, padding: 8, marginTop: 8, maxHeight: 140, overflow: "auto" }}>
                    {JSON.stringify(findBulletDiagnostics(advData, whyPanel.bulletId), null, 2)}
                  </pre>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function AdvancedRow(props: { advData: any; requirement: string }) {
  const m = props.advData?.evidence_matches?.matches?.find((x: any) => x.requirement_text === props.requirement);
  if (!m) return null;
  const top = m.evidence?.[0];
  return (
    <div className="mono" style={{ color: "var(--muted)", marginTop: 3 }}>
      {m.match_type} {top ? `· ${top.evidence_id} · score ${top.score}` : ""}
    </div>
  );
}

function findBulletDiagnostics(advData: any, bulletId: string) {
  for (const e of advData?.generated_resume?.experience ?? []) {
    for (const b of e.bullets ?? []) {
      if (b.id === bulletId) return { evidence_ids: b.evidence_ids, requirement_ids: b.requirement_ids, origin: b.origin };
    }
  }
  return {};
}
