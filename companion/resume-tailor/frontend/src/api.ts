// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
// Thin fetch layer. Every candidate-scoped call names the candidate explicitly —
// the backend has no ambient "active candidate".

export type Candidate = { id: string; name: string; archived: boolean; base_resumes: number; applications: number };
export type StatusOption = { value: string; label: string };
/** Where this Tailor stands: following a Career Agent local profile, or on its own. */
export type Workspace =
  | { mode: "standalone" }
  | { mode: "profile"; profile: { id: string; label: string }; candidate_id: string;
      candidate_name: string; statuses: StatusOption[] };
export type CareerJob = {
  job_id: string; title: string; company: string; url: string; closed: boolean;
  status: string; status_label: string; applied_at: string | null; updated_at: string | null;
  description?: string; description_cut?: boolean;
  tailored?: { id: string; date: string; state: string; match: number | null }[];
};

/** The path of one candidate's API. An empty id is a page bug: it used to
 *  produce `/api/candidates//resumes/upload`, which failed with nothing on
 *  screen. It now fails here, with a sentence, before any request. */
export function candidatePath(cid: string): string {
  if (!cid || !cid.trim()) throw new Error("No candidate is selected yet. Reload Resume Tailor and try again.");
  return `/api/candidates/${encodeURIComponent(cid)}`;
}
export type ExportFormat = "docx" | "pdf" | "md";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return r.json() as Promise<T>;
}

const post = (body?: unknown): RequestInit =>
  ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

export const api = {
  candidates: () => j<Candidate[]>("/api/candidates"),
  createCandidate: (name: string) => j<{ id: string }>("/api/candidates", post({ name })),
  select: (id: string) => j("/api/candidates/select", post({ id })),
  config: (cid: string) => j<any>(`${candidatePath(cid)}/config`),
  settings: (cid: string) => j<any>(`${candidatePath(cid)}/settings`),
  patchSettings: (cid: string, body: unknown) => j<any>(`${candidatePath(cid)}/settings`, { ...post(body), method: "PATCH" }),
  experience: (cid: string, advanced: boolean) => j<any>(`${candidatePath(cid)}/experience?advanced=${advanced ? 1 : 0}`),
  conflicts: (cid: string) => j<any[]>(`${candidatePath(cid)}/conflicts`),
  confirmConflict: (cid: string, id: string, body: { choice?: string; value?: string; note?: string }) =>
    j(`${candidatePath(cid)}/conflicts/${id}/confirm`, post(body)),
  decisions: (cid: string, advanced: boolean) => j<any[]>(`${candidatePath(cid)}/decisions?advanced=${advanced ? 1 : 0}`),
  editDecision: (cid: string, ref: string, body: { value: string; note?: string }) =>
    j(`${candidatePath(cid)}/decisions/${ref}`, { ...post(body), method: "PATCH" }),
  reopenDecision: (cid: string, ref: string) => j(`${candidatePath(cid)}/decisions/${ref}/reopen`, post()),
  confirmDetail: (cid: string, ref: string, body: { text?: string; note?: string }) =>
    j<{ status: string; topic: string }>(`${candidatePath(cid)}/experience/${ref}/confirm`, post(body)),
  acceptSourceDetail: (cid: string, sid: string, n: number, body: { company?: string; title?: string; start?: string; end?: string | null }) =>
    j<{ added: string; status: string }>(`${candidatePath(cid)}/sources/${sid}/details/${n}/accept`, post(body)),
  resumes: (cid: string) => j<any[]>(`${candidatePath(cid)}/resumes`),
  setDefaultResume: (cid: string, rid: string) => j(`${candidatePath(cid)}/resumes/${rid}/default`, post()),
  duplicateResume: (cid: string, rid: string) => j(`${candidatePath(cid)}/resumes/${rid}/duplicate`, post()),
  sources: (cid: string) => j<any[]>(`${candidatePath(cid)}/sources`),
  sourceDetails: (cid: string, sid: string) => j<any[]>(`${candidatePath(cid)}/sources/${sid}/details`),
  applications: (cid: string) => j<any[]>(`${candidatePath(cid)}/applications`),
  application: (cid: string, id: string, advanced: boolean) =>
    j<any>(`${candidatePath(cid)}/applications/${id}?advanced=${advanced ? 1 : 0}`),
  setApplicationStatus: (cid: string, id: string, status: string) =>
    j(`${candidatePath(cid)}/applications/${id}`, { ...post({ status }), method: "PATCH" }),
  startTailor: (cid: string, jd_text: string, resume_id: string, options: Record<string, unknown>, career_job_id = "") =>
    j<{ application_id: string }>(`${candidatePath(cid)}/tailor`, post({ jd_text, resume_id, options, career_job_id })),
  draft: (cid: string, id: string) => j<any>(`${candidatePath(cid)}/applications/${id}/draft`),
  draftEdit: (cid: string, id: string, body: unknown, advanced = false) =>
    j<any>(`${candidatePath(cid)}/applications/${id}/draft/edit?advanced=${advanced ? 1 : 0}`, post(body)),
  draftUndo: (cid: string, id: string) => j<any>(`${candidatePath(cid)}/applications/${id}/draft/undo`, post()),
  draftRestore: (cid: string, id: string) => j<any>(`${candidatePath(cid)}/applications/${id}/draft/restore`, post()),
  uploadResume: async (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return j<any>(`${candidatePath(cid)}/resumes/upload`, { method: "POST", body: form });
  },
  addSource: async (cid: string, file: File, kind: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    return j<any>(`${candidatePath(cid)}/sources`, { method: "POST", body: form });
  },
  workspace: () => j<Workspace>("/api/workspace"),
  careerJob: (jobId: string) => j<CareerJob>(`/api/career/jobs/${encodeURIComponent(jobId)}`),
  careerApplications: () => j<{ statuses: StatusOption[]; jobs: CareerJob[] }>("/api/career/applications"),
  setCareerStatus: (jobId: string, status: string) =>
    j<CareerJob>(`/api/career/applications/${encodeURIComponent(jobId)}`, { ...post({ status }), method: "PATCH" }),
  careerEvidence: () => j<{ experiences: number; details: number; waiting_or_empty: number }>("/api/career/evidence"),
  importCareerEvidence: () => j<{ experiences: number; details: number }>("/api/career/evidence/import", post()),
  careerBaseResume: () => j<{ id: string; name: string; roles: number; details: number }>("/api/career/base-resume", post()),
  exportUrl: (cid: string, id: string, fmt: string) => `${candidatePath(cid)}/applications/${id}/export/${fmt}`,

  /** Download a file from an endpoint, surfacing backend errors as readable messages.
   *  Returns the friendly filename the server chose and, for PDFs, the page count the
   *  local renderer produced (informational — the Word measurement stays canonical). */
  download: async (path: string): Promise<{ name: string; pages?: number }> => {
    const r = await fetch(path);
    if (!r.ok) {
      let detail = "The file could not be created.";
      try { detail = (await r.json()).detail ?? detail; } catch { /* keep default */ }
      throw new Error(detail);
    }
    const blob = await r.blob();
    const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "");
    const name = m?.[1] ?? "download";
    const pagesRaw = parseInt(r.headers.get("X-Resume-Pages") ?? "", 10);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    return Number.isFinite(pagesRaw) && pagesRaw > 0 ? { name, pages: pagesRaw } : { name };
  },
  exportResume: (cid: string, id: string, fmt: ExportFormat) => api.download(api.exportUrl(cid, id, fmt)),
  exportBackup: async (cid: string) => (await api.download(`${candidatePath(cid)}/backup`)).name,
  importBackup: async (file: File, replace?: { id: string; confirmName: string }) => {
    const form = new FormData();
    form.append("file", file);
    if (replace) {
      form.append("replace_id", replace.id);
      form.append("confirm_name", replace.confirmName);
    }
    return j<{ id: string; name: string }>("/api/candidates/import", { method: "POST", body: form });
  },
};
