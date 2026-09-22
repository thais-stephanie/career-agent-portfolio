import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { BackupToast, ImportBackupDialog, useBackup } from "../backup";
import { Card, Chip, Eyebrow, Pill } from "../components";
import { ExportMenu, ExportToast, useExportNotice } from "../export";
import { APP_STATUS_TONES, SOURCE_KIND_TONES, STR } from "../labels";
import { useApp } from "../state";

// ------------------------------------------------------------- Base resumes

export function BaseResumes() {
  const app = useApp();
  const [rows, setRows] = useState<any[]>([]);
  const [notice, setNotice] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    if (app.candidateId) void api.resumes(app.candidateId).then(setRows).catch(() => setRows([]));
  };
  useEffect(refresh, [app.candidateId]);

  const upload = async (file: File) => {
    const out = await api.uploadResume(app.candidateId, file);
    setNotice(`Added “${out.name}” — found ${out.extracted.roles} roles, ${out.extracted.details} experience details, ${out.extracted.skills} skills. ${STR.uploadReassurance}`);
    refresh();
  };

  return (
    <div>
      <header className="page-head">
        <div>
          <Eyebrow>YOUR MATERIAL</Eyebrow>
          <h1 className="page-title">Base resumes</h1>
          <div className="page-sub">Starting points for tailoring. Uploading a resume never changes your confirmed experience.</div>
        </div>
        <button className="btn" onClick={() => fileRef.current?.click()}>+ Add base resume</button>
        <input ref={fileRef} type="file" accept=".docx,.pdf,.txt" style={{ display: "none" }}
               aria-label="Upload base resume"
               onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
      </header>
      <div className="page-body">
        {notice && <Card tone="mint-soft" style={{ marginBottom: 12 }}><p style={{ margin: 0 }}>{notice}</p></Card>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {rows.map((r) => (
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
          <button className="dropzone" onClick={() => fileRef.current?.click()} style={{ minHeight: 140 }}>
            <b>Add a Word or PDF resume</b>
            <br />{STR.uploadReassurance}
          </button>
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
  const [rows, setRows] = useState<any[]>([]);
  const exportNotice = useExportNotice();
  const refresh = () => {
    if (app.candidateId) void api.applications(app.candidateId).then(setRows).catch(() => setRows([]));
  };
  useEffect(refresh, [app.candidateId]);
  const cols = "2fr 1.4fr 1fr 70px 130px 100px";
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
      <div className="page-body">
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
      </div>
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
            <button className="btn2 sm" onClick={() => void backup.exportNow()}>{STR.exportBackup}</button>
            <button className="btn2 sm" onClick={() => setImportOpen(true)}>{STR.importBackup}</button>
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
