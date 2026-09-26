// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
import { Sidebar } from "./Sidebar";
import { AppProvider, useApp } from "./state";
import { Card, Eyebrow } from "./components";
import { Overview } from "./screens/Overview";
import { Tailor } from "./screens/Tailor";
import { Experience } from "./screens/Experience";
import { Applications, BaseResumes, Settings, Sources } from "./screens/Material";

/** What shows while there is no candidate to work on. No screen renders
 *  without one: every candidate-scoped request needs its id. */
function NoCandidate() {
  const app = useApp();
  const [title, body] = app.workspaceError
    ? ["Resume Tailor could not open", `${app.workspaceError} Reload the page to try again.`]
    : app.mode === "loading"
      ? ["Opening your workspace…", ""]
      : ["No candidate yet", "Create a candidate from the menu on the left, or import a backup."];
  return (
    <div>
      <header className="page-head"><div><Eyebrow>RESUME TAILOR</Eyebrow><h1 className="page-title">{title}</h1></div></header>
      {body && <div className="page-body narrow"><Card><p role={app.workspaceError ? "alert" : undefined} style={{ margin: 0 }}>{body}</p></Card></div>}
    </div>
  );
}

function Screens() {
  const app = useApp();
  if (!app.candidateId) return <main className="main"><NoCandidate /></main>;
  // keying by candidate remounts every screen on switch: no stale tailor state survives
  return (
    <main className="main" key={app.candidateId}>
      {app.screen === "overview" && <Overview />}
      {app.screen === "tailor" && <Tailor />}
      {app.screen === "applications" && <Applications />}
      {app.screen === "base" && <BaseResumes />}
      {app.screen === "experience" && <Experience />}
      {app.screen === "sources" && <Sources />}
      {app.screen === "settings" && <Settings />}
    </main>
  );
}

export default function App() {
  return (
    <AppProvider>
      <div className="shell">
        <Sidebar />
        <Screens />
      </div>
    </AppProvider>
  );
}
