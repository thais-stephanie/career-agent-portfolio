// Resume export: one "Export ▾" control offering Word, PDF and Markdown. Every format is
// produced by the backend from the same saved final draft, so this component never
// decides content — it only picks a format, shows progress, and relays the outcome in
// plain language (including "PDF isn't available on this computer" from the server).
import { useEffect, useRef, useState } from "react";
import { api, ExportFormat } from "./api";
import { Notice, Toast } from "./components";
import { STR } from "./labels";

export const EXPORT_FORMATS: [ExportFormat, string, string][] = [
  ["docx", STR.exportWord, "Word"],
  ["pdf", STR.exportPdf, "PDF"],
  ["md", STR.exportMarkdown, "Markdown"],
];

export function useExportNotice() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = (n: Notice) => {
    setNotice(n);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setNotice(null), 9000);
  };
  return { notice, show, dismiss: () => setNotice(null) };
}

export function ExportMenu(props: {
  candidateId: string;
  applicationId: string;
  /** When set, the formats are disabled and this sentence explains why (e.g. an
   *  unsaved line edit): an export must never silently ship an older version. */
  blockedReason?: string;
  compact?: boolean;
  onNotice: (n: Notice) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onClick); };
  }, [open]);

  const run = async (fmt: ExportFormat, human: string) => {
    setOpen(false);
    setBusy(human);
    try {
      const { name, pages } = await api.exportResume(props.candidateId, props.applicationId, fmt);
      props.onNotice({ kind: "ok", text: STR.exportSaved(name, fmt === "pdf" ? pages : undefined) });
    } catch (e: any) {
      props.onNotice({ kind: "err", text: e?.message || "The file could not be created." });
    } finally {
      setBusy(null);
    }
  };

  const cls = props.compact ? "btn2 sm" : "btn";
  return (
    <div className="menu-anchor" ref={ref}>
      <button className={cls} aria-haspopup="menu" aria-expanded={open} disabled={busy !== null}
              onClick={() => setOpen(!open)}>
        {busy ? STR.exportPreparing(busy) : (props.label ?? STR.exportMenu)}
      </button>
      {open && (
        <div className="popover menu-right" role="menu" aria-label="Export format">
          {props.blockedReason && <div className="hint" role="note">{props.blockedReason}</div>}
          {EXPORT_FORMATS.map(([fmt, label, human]) => (
            <button key={fmt} role="menuitem" disabled={!!props.blockedReason} onClick={() => void run(fmt, human)}>
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export { Toast as ExportToast };
