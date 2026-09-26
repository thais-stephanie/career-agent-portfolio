// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, Candidate, followProfile, StatusOption, Workspace } from "./api";

export type Screen = "overview" | "tailor" | "applications" | "base" | "experience" | "sources" | "settings";

/** A posting handed over from Career Agent (`?job=<id>`): its id only. The
 *  text is read from Career Agent through the profile bridge. */
export type Handoff = { jobId: string; profileId: string };

type AppState = {
  screen: Screen;
  setScreen: (s: Screen) => void;
  advanced: boolean;
  setAdvanced: (v: boolean) => void;
  theme: "light" | "dark";
  setTheme: (t: "light" | "dark") => void;
  /** "loading" until /api/workspace answers; then the mode it reported. */
  mode: "loading" | "standalone" | "profile";
  /** Set when the workspace could not be read: shown instead of a guess. */
  workspaceError: string;
  profile: { id: string; label: string } | null;
  statuses: StatusOption[];
  handoff: Handoff | null;
  /** Tailor this Career Agent posting (from Applications). */
  tailorJob: (jobId: string) => void;
  clearHandoff: () => void;
  candidates: Candidate[];
  candidateId: string;
  candidateName: string;
  switchCandidate: (id: string) => void;
  refreshCandidates: () => Promise<void>;
  createCandidate: (name: string) => Promise<void>;
};

const Ctx = createContext<AppState | null>(null);

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside provider");
  return v;
}

function readHandoff(): Handoff | null {
  try {
    const q = new URLSearchParams(window.location.search);
    const jobId = (q.get("job") ?? "").trim();
    return /^[A-Za-z0-9_-]{1,64}$/.test(jobId) ? { jobId, profileId: q.get("profile") ?? "" } : null;
  } catch {
    return null;
  }
}

export function AppProvider(props: { children: ReactNode }) {
  const [handoff, setHandoff] = useState<Handoff | null>(readHandoff);
  const [screen, setScreen] = useState<Screen>(handoff ? "tailor" : "overview");
  const [advanced, setAdvancedState] = useState(localStorage.getItem("rt.advanced") === "1");
  const [theme, setThemeState] = useState<"light" | "dark">(
    localStorage.getItem("rt.theme") === "dark" ? "dark" : "light");
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [workspaceError, setWorkspaceError] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [candidateId, setCandidateId] = useState("");

  useEffect(() => { document.body.dataset.theme = theme; }, [theme]);

  const profileMode = workspace?.mode === "profile";

  const refreshCandidates = async () => {
    if (profileMode) return; // one candidate, the profile's: nothing to choose
    try {
      const list = await api.candidates();
      setCandidates(list);
      const remembered = localStorage.getItem("rt.candidate") ?? "";
      const chosen = list.some((c) => c.id === remembered) ? remembered : (list[0]?.id ?? "");
      setCandidateId(chosen);
      if (chosen) localStorage.setItem("rt.candidate", chosen);
    } catch { /* backend not up yet */ }
  };

  useEffect(() => {
    void api.workspace().then((answer) => {
      // Only a clear profile answer means profile mode; anything else is the
      // standalone app it has always been.
      const ws: Workspace = answer?.mode === "profile" && answer.candidate_id ? answer : { mode: "standalone" };
      setWorkspace(ws);
      if (ws.mode === "profile") {
        followProfile(ws.profile.id);
        setCandidateId(ws.candidate_id);
        setCandidates([{ id: ws.candidate_id, name: ws.candidate_name, archived: false, base_resumes: 0, applications: 0 }]);
      }
    }).catch((e: Error) => {
      // An older backend has no /api/workspace: behave as the standalone app.
      if (/not found/i.test(e.message)) setWorkspace({ mode: "standalone" });
      else setWorkspaceError(e.message || "Resume Tailor could not read its workspace.");
    });
  }, []);

  useEffect(() => {
    if (workspace?.mode === "standalone") void refreshCandidates();
  }, [workspace?.mode]);

  const value: AppState = {
    screen, setScreen,
    advanced,
    setAdvanced: (v) => { setAdvancedState(v); localStorage.setItem("rt.advanced", v ? "1" : "0"); },
    theme,
    setTheme: (t) => { setThemeState(t); localStorage.setItem("rt.theme", t); },
    mode: workspace ? workspace.mode : "loading",
    workspaceError,
    profile: workspace?.mode === "profile" ? workspace.profile : null,
    statuses: workspace?.mode === "profile" ? workspace.statuses : [],
    handoff,
    tailorJob: (jobId) => {
      setHandoff({ jobId, profileId: workspace?.mode === "profile" ? workspace.profile.id : "" });
      setScreen("tailor");
    },
    clearHandoff: () => {
      setHandoff(null);
      try { window.history.replaceState(null, "", window.location.pathname); } catch { /* keep the URL */ }
    },
    candidates,
    candidateId,
    candidateName: candidates.find((c) => c.id === candidateId)?.name ?? "…",
    switchCandidate: (id) => {
      if (profileMode || !id) return;
      setCandidateId(id);
      localStorage.setItem("rt.candidate", id);
      void api.select(id);
      setScreen("overview"); // switching clears any in-memory tailor state via remount
    },
    refreshCandidates,
    createCandidate: async (name) => {
      await api.createCandidate(name);
      await refreshCandidates();
    },
  };
  return <Ctx.Provider value={value}>{props.children}</Ctx.Provider>;
}
