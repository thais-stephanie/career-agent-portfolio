// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
import { useEffect, useRef, useState } from "react";
import { BackupToast, ImportBackupDialog, useBackup } from "./backup";
import { Segmented } from "./components";
import { STR } from "./labels";
import { Screen, useApp } from "./state";

const NAV: { group: string; items: [Screen, string, string, string][] }[] = [
  { group: STR.navWork, items: [
    ["overview", "◱", STR.overview, "var(--blue)"],
    ["tailor", "✎", STR.tailor, "var(--mint)"],
    ["applications", "▤", STR.applications, "var(--lilac)"],
  ]},
  { group: STR.navMaterial, items: [
    ["base", "▢", STR.baseResumes, "var(--blue)"],
    ["experience", "◈", STR.experience, "var(--mint)"],
    ["sources", "❏", STR.sources, "var(--orange)"],
  ]},
  { group: STR.navAccount, items: [
    ["settings", "⚙", STR.settings, "var(--stone)"],
  ]},
];

export function Sidebar() {
  const app = useApp();
  const [menuOpen, setMenuOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const backup = useBackup();
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    const onClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onClick); };
  }, [menuOpen]);

  const newCandidate = async () => {
    const name = window.prompt("Candidate name");
    if (name?.trim()) await app.createCandidate(name.trim());
    setMenuOpen(false);
  };

  return (
    <nav className="sidebar" aria-label="Main">
      <div className="sb-brand">
        <img className="px-icon" src="/assets/px-star-16.png" width={16} height={16} alt="" />
        <span className="name"><b>Resume</b> <span>Tailor</span></span>
        <span className="ver">{STR.local}</span>
      </div>

      {app.mode === "profile" && app.profile ? (
        // One profile, one workspace: nothing to switch here. Switching
        // happens in Career Agent, and this page follows it.
        <>
          <div className="sb-label">{STR.profile}</div>
          <div className="sb-cand sb-profile" data-profile-id={app.profile.id}>
            <div className="nm" style={{ fontWeight: 700 }}>{app.profile.label}</div>
            <div className="note" style={{ fontSize: 11, opacity: 0.8 }}>{STR.followsCareerAgent}</div>
          </div>
        </>
      ) : (<>
      <div className="sb-label">{STR.candidate}</div>
      <div className="sb-cand" ref={menuRef}>
        <button aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen(!menuOpen)}>
          <span className="nm">{app.candidateName}</span>
          <span aria-hidden>{"▾"}</span>
        </button>
        {menuOpen && (
          <div className="popover" role="menu">
            <div className="lab">{STR.switchTo}</div>
            {app.candidates.map((c) => (
              <button key={c.id} role="menuitem" className={c.id === app.candidateId ? "current" : ""}
                      onClick={() => { app.switchCandidate(c.id); setMenuOpen(false); }}>
                <span>{c.name}</span>
                <span className="note">{c.applications ? `${c.applications} apps` : ""}</span>
              </button>
            ))}
            <hr className="hair" />
            <button role="menuitem" onClick={newCandidate}>{STR.newCandidate}</button>
            <button role="menuitem" onClick={() => { setMenuOpen(false); setImportOpen(true); }}>{STR.importBackup}</button>
            <button role="menuitem" onClick={() => { setMenuOpen(false); void backup.exportNow(); }}>{STR.exportBackup}</button>
          </div>
        )}
      </div>
      </>)}

      {NAV.map((g) => (
        <div key={g.group}>
          <div className="sb-label">{g.group}</div>
          {g.items.map(([screen, glyph, label, color]) => (
            <button key={screen} className={`nav-item ${app.screen === screen ? "on" : ""}`}
                    onClick={() => app.setScreen(screen)}>
              <span className="glyph" style={{ color }}>{glyph}</span>
              {label}
            </button>
          ))}
        </div>
      ))}

      <div className="sb-label">{STR.detailLevel}</div>
      <Segmented dark ariaLabel="Detail level" value={app.advanced ? "adv" : "simple"}
                 options={[["simple", STR.simple], ["adv", STR.advanced]]}
                 onChange={(v) => app.setAdvanced(v === "adv")} />

      <div className="sb-label">Appearance</div>
      <Segmented dark ariaLabel="Appearance" value={app.theme}
                 options={[["light", STR.light], ["dark", STR.dark]]}
                 onChange={(v) => app.setTheme(v)} />

      <div className="sb-priv"><span className="dot" /> {STR.privacyLine}</div>

      <ImportBackupDialog open={importOpen} onClose={() => setImportOpen(false)} onDone={backup.show} />
      <BackupToast notice={backup.notice} onDismiss={backup.dismiss} />
    </nav>
  );
}
