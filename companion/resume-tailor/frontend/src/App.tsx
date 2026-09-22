import { Sidebar } from "./Sidebar";
import { AppProvider, useApp } from "./state";
import { Overview } from "./screens/Overview";
import { Tailor } from "./screens/Tailor";
import { Experience } from "./screens/Experience";
import { Applications, BaseResumes, Settings, Sources } from "./screens/Material";

function Screens() {
  const app = useApp();
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
