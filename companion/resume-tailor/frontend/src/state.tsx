import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { api, Candidate } from "./api";

export type Screen = "overview" | "tailor" | "applications" | "base" | "experience" | "sources" | "settings";

type AppState = {
  screen: Screen;
  setScreen: (s: Screen) => void;
  advanced: boolean;
  setAdvanced: (v: boolean) => void;
  theme: "light" | "dark";
  setTheme: (t: "light" | "dark") => void;
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

export function AppProvider(props: { children: ReactNode }) {
  const [screen, setScreen] = useState<Screen>("overview");
  const [advanced, setAdvancedState] = useState(localStorage.getItem("rt.advanced") === "1");
  const [theme, setThemeState] = useState<"light" | "dark">(
    localStorage.getItem("rt.theme") === "dark" ? "dark" : "light");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [candidateId, setCandidateId] = useState(localStorage.getItem("rt.candidate") ?? "");

  useEffect(() => { document.body.dataset.theme = theme; }, [theme]);

  const refreshCandidates = async () => {
    try {
      const list = await api.candidates();
      setCandidates(list);
      if (list.length && !list.some((c) => c.id === candidateId)) {
        setCandidateId(list[0].id);
        localStorage.setItem("rt.candidate", list[0].id);
      }
    } catch { /* backend not up yet */ }
  };

  useEffect(() => { void refreshCandidates(); }, []);

  const value: AppState = {
    screen, setScreen,
    advanced,
    setAdvanced: (v) => { setAdvancedState(v); localStorage.setItem("rt.advanced", v ? "1" : "0"); },
    theme,
    setTheme: (t) => { setThemeState(t); localStorage.setItem("rt.theme", t); },
    candidates,
    candidateId,
    candidateName: candidates.find((c) => c.id === candidateId)?.name ?? "…",
    switchCandidate: (id) => {
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
