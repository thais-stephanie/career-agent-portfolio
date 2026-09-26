// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
// Candidate backup: export the selected candidate to a ZIP the user keeps, and
// bring one back. Following a Career Agent profile, a backup can only be
// restored INTO that profile's workspace: a second candidate beside it would
// be invisible here and easy to mistake for the profile. All copy stays in product language — what the file contains
// and what will happen, never how it is stored.
import { useRef, useState } from "react";
import { api } from "./api";
import { Notice, Toast } from "./components";
import { STR } from "./labels";
import { useApp } from "./state";

export type BackupNotice = Notice;

export function useBackup() {
  const app = useApp();
  const [notice, setNotice] = useState<BackupNotice | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const show = (n: BackupNotice) => {
    setNotice(n);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setNotice(null), 9000);
  };

  const exportNow = async () => {
    try {
      const name = await api.exportBackup(app.candidateId);
      show({ kind: "ok", text: `Backup saved as “${name}”. It can contain personal information — keep it somewhere safe.` });
    } catch (e: any) {
      show({ kind: "err", text: e?.message || "The backup could not be created." });
    }
  };

  return { notice, show, exportNow, dismiss: () => setNotice(null) };
}

export function BackupToast(props: { notice: BackupNotice | null; onDismiss: () => void }) {
  return <Toast notice={props.notice} onDismiss={props.onDismiss} />;
}

export function ImportBackupDialog(props: { open: boolean; onClose: () => void; onDone: (n: BackupNotice) => void }) {
  const app = useApp();
  const [file, setFile] = useState<File | null>(null);
  const profileMode = app.mode === "profile";
  const [mode, setMode] = useState<"new" | "replace">(profileMode ? "replace" : "new");
  const [replaceId, setReplaceId] = useState("");
  const [confirmName, setConfirmName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!props.open) return null;

  const reset = () => { setFile(null); setMode(profileMode ? "replace" : "new"); setReplaceId(""); setConfirmName(""); setError(""); };
  const close = () => { reset(); props.onClose(); };

  const doImport = async () => {
    if (!file) { setError("Choose a backup file first."); return; }
    const target = profileMode ? app.candidateId : replaceId;
    if (mode === "replace" && !target) { setError("Choose which candidate to replace."); return; }
    setBusy(true);
    setError("");
    try {
      const res = await api.importBackup(file, mode === "replace" ? { id: target, confirmName } : undefined);
      if (profileMode) {
        props.onDone({ kind: "ok", text: "The backup was restored into this profile. Reload to see everything it brought." });
      } else {
        await app.refreshCandidates();
        app.switchCandidate(res.id);
        props.onDone({ kind: "ok", text: `“${res.name}” was imported and is now selected.` });
      }
      close();
    } catch (e: any) {
      setError(e?.message || "This backup could not be imported.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label={STR.importBackupTitle} onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div className="card modal">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <h3>{STR.importBackupTitle}</h3>
          <button className="quiet" aria-label="Close" onClick={close}>{"✕"}</button>
        </div>
        <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--body)" }}>
          Choose a backup file you exported earlier ({"“"}Name - Resume Tailor Backup.zip{"”"}).
        </p>
        <label className="dropzone" style={{ display: "block", cursor: "pointer", marginBottom: 12 }}>
          {file ? <b>{file.name}</b> : <span><b>Choose the backup file</b><br />or drop it here</span>}
          <input type="file" accept=".zip" style={{ display: "none" }} aria-label="Backup file"
                 onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>

        {profileMode ? (
          <div className="inner-box" style={{ background: "var(--tint-warm)", marginBottom: 12, fontSize: 12 }}>
            The backup replaces this profile{"’"}s Resume Tailor data ({app.profile?.label}). Your Career Agent data is not touched.
          </div>
        ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
          <label className="check">
            <input type="radio" name="import-mode" checked={mode === "new"} onChange={() => setMode("new")} />
            <span><span className="t">Add as a new candidate</span><br />
              <span className="h">Nothing you already have changes. A name that already exists is imported as {"“"}Name (imported){"”"}.</span></span>
          </label>
          <label className="check">
            <input type="radio" name="import-mode" checked={mode === "replace"} onChange={() => setMode("replace")} />
            <span><span className="t">Replace an existing candidate</span><br />
              <span className="h">Everything that candidate has now is replaced by the backup.</span></span>
          </label>
        </div>
        )}

        {mode === "replace" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 12 }}>
            {!profileMode && <label style={{ fontSize: 12, fontWeight: 600 }}>
              Candidate to replace
              <select value={replaceId} onChange={(e) => setReplaceId(e.target.value)} style={{ marginTop: 4 }}>
                <option value="">Choose…</option>
                {app.candidates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>}
            <label style={{ fontSize: 12, fontWeight: 600 }}>
              {profileMode ? `Type “${app.candidateName}” to confirm` : "Type their name to confirm"}
              <input type="text" value={confirmName} onChange={(e) => setConfirmName(e.target.value)}
                     placeholder="Their exact name" style={{ marginTop: 4 }} />
            </label>
          </div>
        )}

        {error && <p role="alert" style={{ background: "var(--yellow-soft)", border: "1.5px solid var(--line)", borderRadius: 8, padding: 8, fontSize: 12, margin: "0 0 10px" }}>{error}</p>}

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn2 sm" onClick={close}>Cancel</button>
          <button className="btn sm" disabled={busy} onClick={doImport}>{busy ? "Importing…" : "Import"}</button>
        </div>
      </div>
    </div>
  );
}
