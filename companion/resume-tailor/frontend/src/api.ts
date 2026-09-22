// Thin fetch layer. Every candidate-scoped call names the candidate explicitly —
// the backend has no ambient "active candidate".

export type Candidate = { id: string; name: string; archived: boolean; base_resumes: number; applications: number };
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
  config: (cid: string) => j<any>(`/api/candidates/${cid}/config`),
  settings: (cid: string) => j<any>(`/api/candidates/${cid}/settings`),
  patchSettings: (cid: string, body: unknown) => j<any>(`/api/candidates/${cid}/settings`, { ...post(body), method: "PATCH" }),
  experience: (cid: string, advanced: boolean) => j<any>(`/api/candidates/${cid}/experience?advanced=${advanced ? 1 : 0}`),
  conflicts: (cid: string) => j<any[]>(`/api/candidates/${cid}/conflicts`),
  confirmConflict: (cid: string, id: string, body: { choice?: string; value?: string; note?: string }) =>
    j(`/api/candidates/${cid}/conflicts/${id}/confirm`, post(body)),
  decisions: (cid: string, advanced: boolean) => j<any[]>(`/api/candidates/${cid}/decisions?advanced=${advanced ? 1 : 0}`),
  editDecision: (cid: string, ref: string, body: { value: string; note?: string }) =>
    j(`/api/candidates/${cid}/decisions/${ref}`, { ...post(body), method: "PATCH" }),
  reopenDecision: (cid: string, ref: string) => j(`/api/candidates/${cid}/decisions/${ref}/reopen`, post()),
  confirmDetail: (cid: string, ref: string, body: { text?: string; note?: string }) =>
    j<{ status: string; topic: string }>(`/api/candidates/${cid}/experience/${ref}/confirm`, post(body)),
  acceptSourceDetail: (cid: string, sid: string, n: number, body: { company?: string; title?: string; start?: string; end?: string | null }) =>
    j<{ added: string; status: string }>(`/api/candidates/${cid}/sources/${sid}/details/${n}/accept`, post(body)),
  resumes: (cid: string) => j<any[]>(`/api/candidates/${cid}/resumes`),
  setDefaultResume: (cid: string, rid: string) => j(`/api/candidates/${cid}/resumes/${rid}/default`, post()),
  duplicateResume: (cid: string, rid: string) => j(`/api/candidates/${cid}/resumes/${rid}/duplicate`, post()),
  sources: (cid: string) => j<any[]>(`/api/candidates/${cid}/sources`),
  sourceDetails: (cid: string, sid: string) => j<any[]>(`/api/candidates/${cid}/sources/${sid}/details`),
  applications: (cid: string) => j<any[]>(`/api/candidates/${cid}/applications`),
  application: (cid: string, id: string, advanced: boolean) =>
    j<any>(`/api/candidates/${cid}/applications/${id}?advanced=${advanced ? 1 : 0}`),
  setApplicationStatus: (cid: string, id: string, status: string) =>
    j(`/api/candidates/${cid}/applications/${id}`, { ...post({ status }), method: "PATCH" }),
  startTailor: (cid: string, jd_text: string, resume_id: string, options: Record<string, unknown>) =>
    j<{ application_id: string }>(`/api/candidates/${cid}/tailor`, post({ jd_text, resume_id, options })),
  draft: (cid: string, id: string) => j<any>(`/api/candidates/${cid}/applications/${id}/draft`),
  draftEdit: (cid: string, id: string, body: unknown, advanced = false) =>
    j<any>(`/api/candidates/${cid}/applications/${id}/draft/edit?advanced=${advanced ? 1 : 0}`, post(body)),
  draftUndo: (cid: string, id: string) => j<any>(`/api/candidates/${cid}/applications/${id}/draft/undo`, post()),
  draftRestore: (cid: string, id: string) => j<any>(`/api/candidates/${cid}/applications/${id}/draft/restore`, post()),
  uploadResume: async (cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return j<any>(`/api/candidates/${cid}/resumes/upload`, { method: "POST", body: form });
  },
  addSource: async (cid: string, file: File, kind: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    return j<any>(`/api/candidates/${cid}/sources`, { method: "POST", body: form });
  },
  exportUrl: (cid: string, id: string, fmt: string) => `/api/candidates/${cid}/applications/${id}/export/${fmt}`,

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
  exportBackup: async (cid: string) => (await api.download(`/api/candidates/${cid}/backup`)).name,
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
