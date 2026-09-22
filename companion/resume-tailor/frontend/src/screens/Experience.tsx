import { useEffect, useState } from "react";
import { api } from "../api";
import { Pill } from "../components";
import { statusTone, STR } from "../labels";
import { useApp } from "../state";

type Filter = "all" | "confirmed" | "supported" | "review";

function bucketOf(status: string): Filter {
  if (status === "Confirmed by you") return "confirmed";
  if (status === "Needs review") return "review";
  return "supported";
}

/** Inline editor for "the correct information is something else". */
function CorrectValueEditor(props: {
  initialValue?: string; initialNote?: string; busy: boolean;
  onConfirm: (value: string, note: string) => void; onCancel: () => void;
}) {
  const [value, setValue] = useState(props.initialValue ?? "");
  const [note, setNote] = useState(props.initialNote ?? "");
  return (
    <div className="inner-box" style={{ marginTop: 8 }}>
      <label style={{ fontSize: 12, fontWeight: 600, display: "block" }}>
        Correct information
        <input type="text" value={value} onChange={(e) => setValue(e.target.value)}
               placeholder="e.g. September 2026" style={{ marginTop: 4 }} />
      </label>
      <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginTop: 8 }}>
        Optional note
        <input type="text" value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="e.g. The resume was outdated." style={{ marginTop: 4 }} />
      </label>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button className="btn sm" disabled={props.busy || !value.trim()}
                onClick={() => props.onConfirm(value.trim(), note.trim())}>Confirm</button>
        <button className="btn2 sm" onClick={props.onCancel}>Cancel</button>
      </div>
    </div>
  );
}

function ConflictRow(props: { conflict: any; onDone: () => void }) {
  const app = useApp();
  const c = props.conflict;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);

  const confirm = async (body: { choice?: string; value?: string; note?: string }) => {
    setBusy(true);
    try { await api.confirmConflict(app.candidateId, c.id, body); props.onDone(); }
    finally { setBusy(false); }
  };

  return (
    <div className="rrow" style={{ display: "block", background: "var(--yellow-soft)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>{c.topic}</span>
        <span style={{ fontSize: 12, color: "var(--body)" }}>Which one is correct?</span>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
        {c.versions.map((v: any, i: number) => (
          <div key={i} className="inner-box" style={{ flex: "1 1 200px", background: "var(--surface)" }}>
            <div className="eyebrow" style={{ marginBottom: 4 }}>{v.source}</div>
            <div style={{ fontSize: 12 }}>{v.statement}</div>
            <button className="btn2 sm" style={{ marginTop: 8 }} disabled={busy}
                    onClick={() => confirm({ choice: v.statement })}>This one is correct</button>
          </div>
        ))}
      </div>
      {editing ? (
        <CorrectValueEditor busy={busy} onCancel={() => setEditing(false)}
                            onConfirm={(value, note) => confirm({ value, note })} />
      ) : (
        <button className="quiet" style={{ marginTop: 6 }} onClick={() => setEditing(true)}>
          Neither — enter the correct information
        </button>
      )}
    </div>
  );
}

/** A detail that needs the user's say-so and has no competing source: confirm it as
 *  written, correct it first, or leave it. The reason is stated in plain words. */
function ReviewActions(props: { item: any; onDone: () => void }) {
  const app = useApp();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const confirm = async (body: { text?: string; note?: string }) => {
    setBusy(true);
    setError("");
    try { await api.confirmDetail(app.candidateId, props.item.ref, body); props.onDone(); }
    catch (e: any) { setError(e?.message || "This detail could not be confirmed."); }
    finally { setBusy(false); }
  };
  if (!open) return <button className="btn2 sm" style={{ marginTop: 6 }} onClick={() => setOpen(true)}>{STR.reviewDetail}</button>;
  return (
    <div className="inner-box" style={{ marginTop: 6, background: "var(--yellow-soft)" }}>
      <div style={{ fontSize: 12, color: "var(--body)" }}>{props.item.review.why}</div>
      {editing ? (
        <CorrectValueEditor initialValue={props.item.text} busy={busy} onCancel={() => setEditing(false)}
                            onConfirm={(value, note) => confirm({ text: value, note })} />
      ) : (
        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
          <button className="btn sm" disabled={busy} onClick={() => confirm({})}>{STR.confirmDetail}</button>
          <button className="btn2 sm" disabled={busy} onClick={() => setEditing(true)}>{STR.editBeforeConfirm}</button>
          <button className="quiet" onClick={() => setOpen(false)}>{STR.leaveForLater}</button>
        </div>
      )}
      {error && <p role="alert" style={{ margin: "8px 0 0", fontSize: 12 }}>{error}</p>}
    </div>
  );
}

function DecisionRow(props: { decision: any; onDone: () => void }) {
  const app = useApp();
  const d = props.decision;
  const [editing, setEditing] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [busy, setBusy] = useState(false);

  const reopen = async () => {
    const ok = window.confirm(
      `Review “${d.topic}” again? Your decision is set aside and the different versions from your sources come back for review.`);
    if (!ok) return;
    setBusy(true);
    try { await api.reopenDecision(app.candidateId, d.ref); props.onDone(); }
    finally { setBusy(false); }
  };

  return (
    <div className="rrow" style={{ display: "block" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{d.topic}:</span>
        <span style={{ fontSize: 13 }}>{d.value}</span>
        <Pill tone="mint">{d.status}</Pill>
        <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
          <button className="quiet" onClick={() => setEditing(!editing)}>Edit</button>
          <button className="quiet" disabled={busy} onClick={reopen}>Review this again</button>
        </span>
      </div>
      {d.note && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 3 }}>{d.note}</div>}
      {d.versions?.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <button className="quiet" onClick={() => setShowSources(!showSources)}>
            {showSources ? "Hide what the sources said" : "What the sources said"}
          </button>
          {showSources && d.versions.map((v: any, i: number) => (
            <div key={i} style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              {v.source}: {v.statement}
            </div>
          ))}
        </div>
      )}
      {app.advanced && d.advanced && (
        <div className="mono" style={{ color: "var(--muted)", marginTop: 4 }}>
          {d.advanced.override_id} {"·"} {d.advanced.conflict_id} {"·"} {d.advanced.resolved_by}
        </div>
      )}
      {editing && (
        <CorrectValueEditor initialValue={d.value} initialNote={d.note} busy={busy}
                            onCancel={() => setEditing(false)}
                            onConfirm={async (value, note) => {
                              setBusy(true);
                              try { await api.editDecision(app.candidateId, d.ref, { value, note }); setEditing(false); props.onDone(); }
                              finally { setBusy(false); }
                            }} />
      )}
    </div>
  );
}

export function Experience() {
  const app = useApp();
  const [exp, setExp] = useState<any>(null);
  const [conflicts, setConflicts] = useState<any[]>([]);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [filter, setFilter] = useState<Filter>("all");

  const refresh = () => {
    if (!app.candidateId) return;
    void api.experience(app.candidateId, app.advanced).then(setExp).catch(() => setExp(null));
    void api.conflicts(app.candidateId).then(setConflicts).catch(() => setConflicts([]));
    void api.decisions(app.candidateId, app.advanced).then(setDecisions).catch(() => setDecisions([]));
  };
  useEffect(refresh, [app.candidateId, app.advanced]);

  const needsReview = (exp?.needs_review ?? 0) + (exp?.conflicting ?? 0);
  const counts: Record<Filter, number> = {
    all: exp?.total ?? 0,
    confirmed: exp?.confirmed ?? 0,
    supported: exp?.supported ?? 0,
    review: needsReview,
  };
  const items = (exp?.items ?? []).filter((i: any) => filter === "all" || bucketOf(i.status) === filter);

  return (
    <div>
      <header className="page-head">
        <div>
          <h1 className="page-title">Experience & evidence</h1>
          <div className="page-sub">
            {exp ? `${exp.total} details${needsReview ? ` · ${needsReview} need${needsReview === 1 ? "s" : ""} review` : ""}` : "…"}
          </div>
        </div>
      </header>
      <div className="page-body">
        {conflicts.length > 0 && (
          <div className="rows" style={{ marginBottom: 14 }}>
            <div className="rhead">
              <span>Needs review</span>
              <Pill px tone="yellow">{conflicts.length}</Pill>
            </div>
            {conflicts.map((c) => <ConflictRow key={c.id} conflict={c} onDone={refresh} />)}
          </div>
        )}

        {decisions.length > 0 && (
          <div className="rows" style={{ marginBottom: 14 }}>
            <div className="rhead"><span>Details you confirmed</span><Pill px tone="mint">{decisions.length}</Pill></div>
            {decisions.map((d) => <DecisionRow key={d.ref} decision={d} onDone={refresh} />)}
          </div>
        )}

        <div className="seg" role="group" aria-label="Filter details" style={{ marginBottom: 12 }}>
          {([["all", "All"], ["confirmed", "Confirmed"], ["supported", "Supported"], ["review", "Needs review"]] as [Filter, string][]).map(([f, label]) => (
            <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
              {label} {counts[f] ? `(${counts[f]})` : ""}
            </button>
          ))}
        </div>

        <div className="rows">
          {items.map((item: any, i: number) => (
            <div key={i} className="rrow">
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span className="mono" style={{ color: "var(--muted)" }}>{item.company}</span>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{item.title}</span>
                  {[...new Set(item.sources)].map((s: any) => <span key={s} className="chip">{s}</span>)}
                </div>
                <div style={{ fontSize: 12, color: "var(--body)", marginTop: 2 }}>{item.text}</div>
                {item.ref && item.review && <ReviewActions item={item} onDone={refresh} />}
                {app.advanced && item.advanced && (
                  <div className="mono" style={{ color: "var(--muted)", marginTop: 3 }}>
                    {item.advanced.evidence_id} {"·"} {item.advanced.source_strength} {"·"} {item.advanced.verification}
                  </div>
                )}
              </div>
              <Pill tone={statusTone(item.status)}>{item.status}</Pill>
            </div>
          ))}
          {!items.length && <div className="rrow" style={{ color: "var(--muted)" }}>Nothing here. {STR.confirmHint}</div>}
        </div>
      </div>
    </div>
  );
}
