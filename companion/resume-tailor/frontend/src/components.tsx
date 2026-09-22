import { ReactNode, useState } from "react";
import { StatusTone, toneVar } from "./labels";

export function Card(props: { tone?: StatusTone; style?: React.CSSProperties; className?: string; children: ReactNode }) {
  return (
    <div className={`card ${props.className ?? ""}`}
         style={{ background: props.tone ? toneVar(props.tone) : undefined, ...props.style }}>
      {props.children}
    </div>
  );
}

export function Stat(props: { k: string; n: ReactNode; l: string; s?: string; tone: StatusTone }) {
  return (
    <div className="stat" style={{ background: toneVar(props.tone) }}>
      <div className="k">{props.k}</div>
      <div className="n">{props.n}</div>
      <div className="l">{props.l}</div>
      {props.s && <div className="s">{props.s}</div>}
    </div>
  );
}

export function Pill(props: { tone?: StatusTone; px?: boolean; children: ReactNode; title?: string }) {
  return (
    <span className={`pill ${props.px ? "px" : ""}`} title={props.title}
          style={{ background: props.tone ? toneVar(props.tone) : "var(--surface-alt)" }}>
      {props.children}
    </span>
  );
}

export function Chip(props: { tone?: StatusTone; children: ReactNode }) {
  return (
    <span className="chip" style={props.tone ? { background: toneVar(props.tone) } : undefined}>
      {props.children}
    </span>
  );
}

export function Segmented<T extends string>(props: {
  value: T; options: [T, string][]; onChange: (v: T) => void; dark?: boolean; ariaLabel?: string;
}) {
  return (
    <div className={props.dark ? "sb-seg" : "seg"} role="group" aria-label={props.ariaLabel}>
      {props.options.map(([v, label]) => (
        <button key={v} className={v === props.value ? "on" : ""} onClick={() => props.onChange(v)}>
          {label}
        </button>
      ))}
    </div>
  );
}

export function CheckOption(props: { on: boolean; onToggle: () => void; label: string; hint: string }) {
  return (
    <button type="button" className="check" style={{ background: "none", border: 0, padding: 0, textAlign: "left" }}
            onClick={props.onToggle} aria-pressed={props.on}>
      <span className={`checkbox ${props.on ? "on" : ""}`}>{props.on ? "✓" : ""}</span>
      <span>
        <span className="t">{props.label}</span>
        <br />
        <span className="h">{props.hint}</span>
      </span>
    </button>
  );
}

export function PixelIcon(props: { name: string; size?: number }) {
  const size = props.size ?? 16;
  return <img className="px-icon" src={`/assets/px-${props.name}-16.png`} width={size} height={size} alt="" />;
}

export function Swatch(props: { tone: StatusTone }) {
  return <span className="swatch" style={{ background: toneVar(props.tone) }} />;
}

export function Eyebrow(props: { children: ReactNode }) {
  return <div className="eyebrow">{"◈"} {props.children}</div>;
}

export type Notice = { kind: "ok" | "err"; text: string };

export function Toast(props: { notice: Notice | null; onDismiss: () => void }) {
  if (!props.notice) return null;
  return (
    <div className="toast card" role="status"
         style={{ background: props.notice.kind === "ok" ? "var(--mint-soft)" : "var(--yellow-soft)" }}>
      <span style={{ flex: 1 }}>{props.notice.text}</span>
      <button className="quiet" aria-label="Dismiss" onClick={props.onDismiss}>{"✕"}</button>
    </div>
  );
}

/** One line: the answer and one action. Replaces a card whose only job was a sentence. */
export function Banner(props: { tone?: StatusTone; children: ReactNode; action?: ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="banner" role="note" style={{ background: props.tone ? toneVar(props.tone) : "var(--surface)", ...props.style }}>
      <span>{props.children}</span>
      {props.action && <><span className="spacer" />{props.action}</>}
    </div>
  );
}

/** Inline counts in one bordered strip; each segment keeps its pastel status fill. */
export function Strip(props: { items: { n: ReactNode; l: string; tone: StatusTone }[] }) {
  return (
    <div className="strip">
      {props.items.map((it) => (
        <div key={it.l} style={{ background: toneVar(it.tone) }}>
          <span className="n">{it.n}</span>
          <span className="l">{it.l}</span>
        </div>
      ))}
    </div>
  );
}

/** Details on demand: a small underlined toggle, content only when asked. */
export function Disclosure(props: { label: string; openLabel?: string; children: ReactNode; defaultOpen?: boolean; className?: string }) {
  const [open, setOpen] = useState(!!props.defaultOpen);
  return (
    <div className={props.className}>
      <button type="button" className="disclosure" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? (props.openLabel ?? props.label) : props.label}
      </button>
      {open && <div className="disclosure-body">{props.children}</div>}
    </div>
  );
}
