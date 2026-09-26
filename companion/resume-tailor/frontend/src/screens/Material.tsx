// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
import { useEffect, useRef, useState } from "react";
import { api, CareerJob, StatusOption } from "../api";
import { BaseResumeStart, ProgressLine, RESUME_ACCEPT, useResumeUpload } from "../resumeStart";
import { BackupToast, ImportBackupDialog, useBackup } from "../backup";
import { Card, Chip, Eyebrow, Pill } from "../components";
import { ExportMenu, ExportToast, useExportNotice } from "../export";
import { APP_STATUS_TONES, SOURCE_KIND_TONES, STR } from "../labels";
import { useApp } from "../state";

// ------------------------------------------------------------- Base resumes

export function BaseResumes() {
  const app = useApp();
  const [rows, setRows] = useState<any[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    if (app.candidateId) void api.resumes(app.candidateId).then(setRows).catch(() => setRows([]));
  };
  useEffect(refresh, [app.candidateId]);
  const { progress, upload, fromProfile } = useResumeUpload(refresh);

  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>YOUR MATERIAL</Eyebrow>
          <h1 className="page-title">Base resumes</h1>
          <div className="page-sub">Starting points for tailoring. Uploading a resume never changes your confirmed experience.</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {app.mode === "profile" && (rows?.length ?? 0) > 0 && (
            <button className="btn2" disabled={progress.state === "busy"} onClick={() => void fromProfile()}>
              {STR.fromProfile}
            </button>
          )}
          <button className="btn" disabled={progress.state === "busy"} onClick={() => fileRef.current?.click()}>+ Add base resume</button>
        </div>
        <input ref={fileRef} type="file" accept={RESUME_ACCEPT} style={{ display: "none" }}
               aria-label="Upload base resume"
               onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
      </header>
      <div className="page-body">
        {rows !== null && rows.length === 0 && <div style={{ maxWidth: 560, marginBottom: 12 }}><BaseResumeStart onDone={refresh} /></div>}
        {(rows?.length ?? 0) > 0 && <div style={{ marginBottom: 12 }}><ProgressLine progress={progress} /></div>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {(rows ?? []).map((r) => (
            <Card key={r.id}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                <h3 style={{ fontSize: 18 }}>{r.name}</h3>
                {r.default && <Pill px tone="mint">DEFAULT</Pill>}
              </div>
              <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--body)" }}>{r.headline}</p>
              <div style={{ display: "flex", gap: 6 }}>
                <Chip>{r.skills} skills</Chip>
              </div>
              <hr className="hair" />
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {!r.default && (
                  <button className="btn2 sm" onClick={async () => { await api.setDefaultResume(app.candidateId, r.id); refresh(); }}>
                    Set default
                  </button>
                )}
                <button className="btn2 sm" onClick={async () => { await api.duplicateResume(app.candidateId, r.id); refresh(); }}>
                  Duplicate
                </button>
              </div>
            </Card>
          ))}
          {(rows?.length ?? 0) > 0 && (
            <button className="dropzone" onClick={() => fileRef.current?.click()} style={{ minHeight: 140 }}>
              <b>Add a PDF, Word or Markdown resume</b>
              <br />{STR.uploadReassurance}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Sources

export function Sources() {
  const app = useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [details, setDetails] = useState<{ id: string; items: any[] } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    if (app.candidateId) void api.sources(app.candidateId).then(setRows).catch(() => setRows([]));
  };
  useEffect(refresh, [app.candidateId]);

  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>YOUR MATERIAL</Eyebrow>
          <h1 className="page-title">Sources</h1>
          <div className="page-sub">The documents your experience details come from.</div>
        </div>
        <button className="btn" onClick={() => fileRef.current?.click()}>+ Add source</button>
        <input ref={fileRef} type="file" accept=".docx,.pdf,.txt,.md" style={{ display: "none" }}
               aria-label="Upload source document"
               onChange={async (e) => {
                 const f = e.target.files?.[0];
                 if (f) { await api.addSource(app.candidateId, f, "other"); refresh(); }
               }} />
      </header>
      <div className="page-body">
        {rows.map((s) => (
          <div key={s.id} className="row-card" style={{ marginBottom: 10, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontWeight: 600 }}>{s.name}</span>
                <Pill tone={SOURCE_KIND_TONES[s.kind] ?? "surface-alt"}>{s.kind}</Pill>
                {s.conflicts > 0 && <Pill tone="yellow">{s.conflicts} conflict{s.conflicts > 1 ? "s" : ""}</Pill>}
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>
                Added {s.added?.slice(0, 10)} {"·"} {s.details} detail{s.details === 1 ? "" : "s"} came from this
              </div>
            </div>
            <button className="btn2 sm" onClick={async () => setDetails({ id: s.id, items: await api.sourceDetails(app.candidateId, s.id) })}>
              View details
            </button>
          </div>
        ))}
        {!rows.length && <Card><p style={{ margin: 0, color: "var(--muted)" }}>No sources yet. Add a resume, LinkedIn PDF or project document to extract experience details you can review.</p></Card>}
        {details && (
          <Card style={{ marginTop: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <h3>Extracted details</h3>
              <button className="quiet" onClick={() => setDetails(null)}>{"✕"} Close</button>
            </div>
            {details.items.map((d) => (
              <ExtractedDetailRow key={d.n} detail={d} sourceId={details.id}
                                  onAdded={async () => setDetails({ id: details.id, items: await api.sourceDetails(app.candidateId, details.id) })} />
            ))}
            {!details.items.length && <p style={{ color: "var(--muted)" }}>Nothing was extracted from this document.</p>}
          </Card>
        )}
      </div>
    </div>
  );
}

/** One extracted detail: added already, or waiting, with the facts it still needs
 *  (company, role title, start month) asked for in plain words before it is added. */
function ExtractedDetailRow(props: { detail: any; sourceId: string; onAdded: () => Promise<void> }) {
  const app = useApp();
  const d = props.detail;
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ company: d.company ?? "", title: d.title ?? "", start: d.start ?? "", end: d.end ?? "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    setError("");
    try {
      await api.acceptSourceDetail(app.candidateId, props.sourceId, d.n,
        { ...form, end: form.end.trim() ? form.end.trim() : null });
      await props.onAdded();
    } catch (e: any) { setError(e?.message || "This detail could not be added."); }
    finally { setBusy(false); }
  };
  const field = (key: keyof typeof form, label: string, placeholder: string) => (
    <label style={{ fontSize: 11, fontWeight: 600, flex: "1 1 120px" }}>
      {label}
      <input type="text" value={form[key]} placeholder={placeholder} aria-label={label}
             onChange={(e) => setForm({ ...form, [key]: e.target.value })} style={{ marginTop: 3, padding: "5px 8px", fontSize: 12 }} />
    </label>
  );
  return (
    <div style={{ padding: "8px 0", borderBottom: "1px solid var(--rule)" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <div style={{ flex: 1, fontSize: 12 }}>{d.text}</div>
        {d.state === "accepted"
          ? <Pill tone="mint-soft">Added</Pill>
          : <>
              <Pill tone="yellow">{STR.waitingForReview}</Pill>
              {!open && <button className="btn2 sm" onClick={() => setOpen(true)}>{STR.reviewDetail}</button>}
            </>}
      </div>
      {open && d.state !== "accepted" && (
        <div className="inner-box" style={{ marginTop: 6, background: "var(--yellow-soft)" }}>
          <div style={{ fontSize: 12, color: "var(--body)" }}>
            Check the company, role and dates, then add it to your experience. It counts as coming from this document, not as confirmed by you.
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 6 }}>
            {field("company", "Company", "e.g. Cobalt Peak Software")}
            {field("title", "Role title", "e.g. Automation Specialist")}
            {field("start", "Start month", "e.g. 2021-02")}
            {field("end", "End month (empty if current)", "e.g. 2022-12")}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn sm" disabled={busy} onClick={add}>{STR.addToExperience}</button>
            <button className="quiet" onClick={() => setOpen(false)}>{STR.leaveForLater}</button>
          </div>
          {error && <p role="alert" style={{ margin: "8px 0 0", fontSize: 12 }}>{error}</p>}
        </div>
      )}
    </div>
  );
}

// -------------------------------------------------------------- Applications

export function Applications() {
  const app = useApp();
  return app.mode === "profile" ? <CareerApplications /> : <TailorApplications />;
}

/** With Career Agent: its tracked postings are the applications. Status is
 *  Career Agent's and is changed there, through the bridge; each tailored
 *  resume sits under the posting it was made for. */
function CareerApplications() {
  const app = useApp();
  const [jobs, setJobs] = useState<CareerJob[] | null>(null);
  const [statuses, setStatuses] = useState<StatusOption[]>(app.statuses);
  const [error, setError] = useState("");
  const exportNotice = useExportNotice();
  const refresh = () => {
    void api.careerApplications().then((out) => { setJobs(out.jobs); setStatuses(out.statuses); setError(""); })
      .catch((e: Error) => { setJobs([]); setError(e.message || "Career Agent could not be read."); });
  };
  useEffect(refresh, []);
  const change = async (jobId: string, status: string) => {
    try {
      await api.setCareerStatus(jobId, status);
      refresh();
    } catch (e: any) { setError(e?.message || "The status could not be changed."); }
  };
  const cols = "2fr 1.4fr 150px 1.2fr 90px";
  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>WORK</Eyebrow>
          <h1 className="page-title">Applications</h1>
          <div className="page-sub">The postings you marked in Career Agent. A status changed here is changed there. {STR.exportUsesSaved}</div>
        </div>
      </header>
      <ExportToast notice={exportNotice.notice} onDismiss={exportNotice.dismiss} />
      <div className="page-body">
        {error && <p role="alert" style={{ background: "var(--yellow-soft)", border: "1.5px solid var(--line)", borderRadius: 8, padding: 8, fontSize: 12 }}>{error}</p>}
        <div className="table career-applications">
          <div className="thead" style={{ gridTemplateColumns: cols }}>
            <span>Role</span><span>Company</span><span>{STR.careerStatus}</span><span>Tailored resumes</span><span></span>
          </div>
          {(jobs ?? []).map((job) => (
            <div key={job.job_id} className="trow" data-job-id={job.job_id} style={{ gridTemplateColumns: cols }}>
              <div style={{ fontWeight: 600 }}>{job.title}</div>
              <span style={{ fontSize: 12 }}>{job.company}</span>
              <select value={job.status} aria-label={`${STR.careerStatus}: ${job.title}`} className="career-status-select"
                      style={{ padding: "4px 6px", fontSize: 11 }}
                      onChange={(e) => void change(job.job_id, e.target.value)}>
                {statuses.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {(job.tailored ?? []).length === 0 && <span style={{ fontSize: 11, color: "var(--muted)" }}>None yet</span>}
                {(job.tailored ?? []).map((t) => (
                  <div key={t.id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 11 }}>
                    <span>{t.date?.slice(0, 10)}</span>
                    {t.state === "done"
                      ? <ExportMenu compact candidateId={app.candidateId} applicationId={t.id} onNotice={exportNotice.show} />
                      : <span style={{ color: "var(--muted)" }}>{t.state === "error" ? "Did not finish" : "Still working…"}</span>}
                  </div>
                ))}
              </div>
              <button className="btn2 sm" onClick={() => app.tailorJob(job.job_id)}>{STR.tailorThis}</button>
            </div>
          ))}
          {jobs !== null && !jobs.length && <div className="trow" style={{ gridTemplateColumns: "1fr" }}>
            <span style={{ color: "var(--muted)" }}>{STR.noTrackedJobs}</span></div>}
        </div>
        <h3 style={{ marginTop: 20 }}>{STR.otherResumes}</h3>
        <TailorApplications embedded />
      </div>
    </div>
  );
}

/** Tailored resumes as Resume Tailor records them. Standalone, this is the
 *  Applications screen; with Career Agent, it lists only the resumes that
 *  were not made for one of its postings. */
function TailorApplications(props: { embedded?: boolean }) {
  const app = useApp();
  const [rows, setRows] = useState<any[]>([]);
  const exportNotice = useExportNotice();
  const refresh = () => {
    if (app.candidateId) void api.applications(app.candidateId)
      .then((all) => setRows(props.embedded ? all.filter((a) => !a.career_job_id) : all))
      .catch(() => setRows([]));
  };
  useEffect(refresh, [app.candidateId]);
  const cols = "2fr 1.4fr 1fr 70px 130px 100px";
  const table = (
        <div className="table">
          <div className="thead" style={{ gridTemplateColumns: cols }}>
            <span>Role</span><span>Company</span><span>Base resume</span><span>Match</span><span>Status</span><span>Export</span>
          </div>
          {rows.map((a) => (
            <div key={a.id} className="trow" style={{ gridTemplateColumns: cols }}>
              <div>
                <div style={{ fontWeight: 600 }}>{a.role || "Untitled role"}</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>{a.date?.slice(0, 10)}</div>
              </div>
              <span style={{ fontSize: 12 }}>{a.company || "—"}</span>
              <span style={{ fontSize: 12 }}>{a.base_resume}</span>
              <span className="mono">{a.match != null ? `${Math.round(a.match * 100)}%` : "—"}</span>
              <select value={a.status} aria-label="Application status"
                      style={{ padding: "4px 6px", fontSize: 11 }}
                      onChange={async (e) => { await api.setApplicationStatus(app.candidateId, a.id, e.target.value); refresh(); }}>
                {["Considering", "Applied", "Interviewing", "Closed"].map((s) => <option key={s}>{s}</option>)}
              </select>
              {a.state === "done"
                ? <ExportMenu compact candidateId={app.candidateId} applicationId={a.id} onNotice={exportNotice.show} />
                : <span style={{ fontSize: 11, color: "var(--muted)" }}>{a.state === "error" ? "Didn't finish" : "Still working…"}</span>}
            </div>
          ))}
          {!rows.length && <div className="trow" style={{ gridTemplateColumns: "1fr" }}>
            <span style={{ color: "var(--muted)" }}>No applications yet.</span></div>}
        </div>
  );
  if (props.embedded) return <>{table}<ExportToast notice={exportNotice.notice} onDismiss={exportNotice.dismiss} /></>;
  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>WORK</Eyebrow>
          <h1 className="page-title">Applications</h1>
          <div className="page-sub">Every tailored resume, with where it went. {STR.exportUsesSaved}</div>
        </div>
      </header>
      <ExportToast notice={exportNotice.notice} onDismiss={exportNotice.dismiss} />
      <div className="page-body">{table}</div>
    </div>
  );
}

// ------------------------------------------------------------------ Settings

export function Settings() {
  const app = useApp();
  const backup = useBackup();
  const [importOpen, setImportOpen] = useState(false);
  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>ACCOUNT</Eyebrow>
          <h1 className="page-title">Settings</h1>
        </div>
      </header>
      <div className="page-body narrow" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <Card>
          <h3>{STR.detailLevel}</h3>
          <p style={{ margin: "0 0 8px", fontSize: 12, color: "var(--body)" }}>
            Advanced adds technical diagnostics for developers.
          </p>
          <div className="seg" role="group" aria-label="Detail level">
            <button className={!app.advanced ? "on" : ""} onClick={() => app.setAdvanced(false)}>{STR.simple}</button>
            <button className={app.advanced ? "on" : ""} onClick={() => app.setAdvanced(true)}>{STR.advanced} (technical)</button>
          </div>
        </Card>
        <Card>
          <h3>Backup</h3>
          <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--body)" }}>
            Save this candidate to a file you control, or bring one back.
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn2 sm" disabled={!app.candidateId} onClick={() => void backup.exportNow()}>{STR.exportBackup}</button>
            <button className="btn2 sm" disabled={app.mode === "loading"} onClick={() => setImportOpen(true)}>{STR.importBackup}</button>
          </div>
          <ImportBackupDialog open={importOpen} onClose={() => setImportOpen(false)} onDone={backup.show} />
          <BackupToast notice={backup.notice} onDismiss={backup.dismiss} />
          <div className="inner-box" style={{ background: "var(--tint-warm)", marginTop: 10, fontSize: 12 }}>
            This backup may contain personal and professional information. Keep it somewhere safe.
          </div>
        </Card>
        <Card>
          <h3>Privacy</h3>
          {["Your documents never leave this computer.",
            "No account, sign-in or tracking.",
            "Nothing is sent to an AI provider unless you set one up yourself."].map((t) => (
            <div key={t} style={{ display: "flex", gap: 8, padding: "5px 0", fontSize: 13 }}>
              <span style={{ width: 18, height: 18, border: "1.5px solid var(--line)", borderRadius: 5, background: "var(--mint)", display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700 }}>{"✓"}</span>
              {t}
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}
