// Added for the Career Agent public edition (2026-09-26). See NOTICE.
// Getting a base resume: from the Career Agent profile, or by upload. One
// component for the Tailor empty state and the Base resumes screen, so the
// two can never disagree about which files work or what an error says.
import { useRef, useState } from "react";
import { api } from "./api";
import { Card } from "./components";
import { STR } from "./labels";
import { useApp } from "./state";

/** The files a base resume may be. The server checks the same list. */
export const RESUME_ACCEPT = ".pdf,.docx,.md,.markdown,.txt";

type Progress = { state: "idle" | "busy" | "ok" | "err"; text: string };

export function useResumeUpload(onDone: () => void) {
  const app = useApp();
  const [progress, setProgress] = useState<Progress>({ state: "idle", text: "" });
  const upload = async (file: File) => {
    setProgress({ state: "busy", text: STR.uploading(file.name) });
    try {
      const out = await api.uploadResume(app.candidateId, file);
      setProgress({
        state: "ok",
        text: `Added “${out.name}”: ${out.extracted.roles} roles, ${out.extracted.details} experience details, ${out.extracted.skills} skills. ${STR.uploadReassurance}`,
      });
      onDone();
    } catch (e: any) {
      setProgress({ state: "err", text: e?.message || STR.uploadFailed });
    }
  };
  const fromProfile = async () => {
    setProgress({ state: "busy", text: STR.fromProfileBusy });
    try {
      const out = await api.careerBaseResume();
      setProgress({ state: "ok", text: STR.fromProfileDone(out.roles, out.details) });
      onDone();
    } catch (e: any) {
      setProgress({ state: "err", text: e?.message || STR.fromProfileFailed });
    }
  };
  return { progress, upload, fromProfile };
}

export function ProgressLine(props: { progress: Progress }) {
  const p = props.progress;
  if (p.state === "idle") return null;
  return (
    <p role={p.state === "err" ? "alert" : "status"} aria-live="polite" data-state={p.state} className="upload-progress"
       style={{ margin: "10px 0 0", fontSize: 12, padding: 8, borderRadius: 8, border: "1.5px solid var(--line)",
                background: p.state === "err" ? "var(--yellow-soft)" : p.state === "ok" ? "var(--mint-soft)" : "var(--surface-alt)" }}>
      {p.text}
    </p>
  );
}

/** "No base resume yet": the two ways to get one, side by side. */
export function BaseResumeStart(props: { onDone: () => void; compact?: boolean }) {
  const app = useApp();
  const input = useRef<HTMLInputElement>(null);
  const { progress, upload, fromProfile } = useResumeUpload(props.onDone);
  const busy = progress.state === "busy";
  return (
    <Card className="base-start">
      <div className="eyebrow">BASE RESUME</div>
      <h3>{STR.noBaseResume}</h3>
      <p style={{ margin: "0 0 10px", fontSize: 12, color: "var(--body)" }}>{STR.noBaseResumeWhy}</p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {app.mode === "profile" && (
          <button className="btn sm" id="base-from-profile" disabled={busy} onClick={() => void fromProfile()}>
            {STR.fromProfile}
          </button>
        )}
        <button className={app.mode === "profile" ? "btn2 sm" : "btn sm"} id="base-upload" disabled={busy}
                onClick={() => input.current?.click()}>
          {STR.uploadResume}
        </button>
        <input ref={input} type="file" accept={RESUME_ACCEPT} style={{ display: "none" }} aria-label={STR.uploadResume}
               onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) void upload(f); }} />
      </div>
      <p className="faint" style={{ margin: "8px 0 0" }}>{STR.uploadFormats}</p>
      <ProgressLine progress={progress} />
    </Card>
  );
}
