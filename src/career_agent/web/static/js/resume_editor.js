/**
 * resume_editor.js -- the Resume Workspace editor's state rules, with no DOM.
 *
 * HISTORY. Whole-document snapshots. A burst of typing in one field within
 * 500 ms is ONE undo step; at most 100 steps are kept; any new edit clears
 * redo; undo then redo gives back exactly the undone state. The page clears
 * the history when the document is replaced from outside (a reload after a
 * conflict, another document).
 *
 * AUTOSAVE. Saves the working copy 600 ms after the last edit. One save in
 * flight at a time; edits made meanwhile wait as ONE pending copy (the newest)
 * and go next. Every save names the hash it edits, so a second window cannot
 * be overwritten: a 409 stops saving and says so, and nothing is retried
 * over it. Any other failure keeps the pending copy and retries. A copy the
 * server refuses as invalid waits for the next edit. `flush()` finishes every
 * pending save before anything that must see the saved document.
 *
 * EDITS. A line that came from evidence keeps its origin and evidence when
 * the person rewords it: it becomes an override with the wording it replaced
 * kept, and putting the old words back makes it untouched again. Nothing
 * here can write Career Evidence; the only thing saved is this document.
 */

const CROCKFORD = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';

/** A ULID: 48 bits of time, 80 random bits, Crockford base32. */
export function ulid(now = Date.now()) {
  let out = '';
  let time = now;
  for (let i = 0; i < 10; i += 1) {
    out = CROCKFORD[time % 32] + out;
    time = Math.floor(time / 32);
  }
  const random = crypto.getRandomValues(new Uint8Array(16));
  for (let i = 0; i < 16; i += 1) out += CROCKFORD[random[i] % 32];
  return out;
}

export function createHistory({ cap = 100, coalesceMs = 500, now = () => Date.now() } = {}) {
  let past = [];
  let future = [];
  let lastKey = null;
  let lastAt = 0;
  return {
    /** Record the state BEFORE an edit. `key` names the field being typed in. */
    record(before, key = null) {
      const at = now();
      future = [];
      if (key && key === lastKey && at - lastAt < coalesceMs) {
        lastAt = at;
        return;
      }
      past.push(before);
      if (past.length > cap) past.shift();
      lastKey = key;
      lastAt = at;
    },
    undo(current) {
      if (!past.length) return null;
      future.push(current);
      lastKey = null;
      return past.pop();
    },
    redo(current) {
      if (!future.length) return null;
      past.push(current);
      lastKey = null;
      return future.pop();
    },
    clear() { past = []; future = []; lastKey = null; },
    get canUndo() { return past.length > 0; },
    get canRedo() { return future.length > 0; },
    get size() { return past.length; },
  };
}

/**
 * @param {object} options
 * @param {(doc: object, sha: string) => Promise<string>} options.save
 *   resolves to the new hash; rejects with `{status}` (409 stale, 400 invalid)
 * @param {(state: string) => void} options.onState
 *   'saved' | 'pending' | 'saving' | 'retrying' | 'invalid' | 'conflict'
 */
export function createAutosave({ save, sha, delay = 600, retryMs = 3000, onState = () => {} }) {
  let hash = sha;
  let pending = null;
  let timer = null;
  let inflight = null;
  let state = 'saved';
  const set = (next) => { state = next; onState(next); };

  function wait(ms) {
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; void run(); }, ms);
  }

  function run() {
    if (inflight || state === 'conflict' || !pending) return inflight;
    const doc = pending;
    pending = null;
    set('saving');
    inflight = save(doc, hash).then((next) => {
      hash = next;
    }, (error) => {
      // A newer copy, if one arrived meanwhile, is what goes next.
      pending = pending || doc;
      if (error && error.status === 409) set('conflict');
      else if (error && error.status === 400) set('invalid');
      else set('retrying');
    }).finally(() => {
      inflight = null;
      if (state === 'conflict' || state === 'invalid') return;
      if (state === 'retrying') wait(retryMs);
      else if (pending) void run();
      else set('saved');
    });
    return inflight;
  }

  return {
    schedule(doc) {
      if (state === 'conflict') { pending = doc; return; }
      pending = doc;
      if (state !== 'saving') set('pending');
      wait(delay);
    },
    /** Save everything now; true when nothing is left unsaved. */
    async flush() {
      clearTimeout(timer);
      timer = null;
      if (inflight) await inflight;
      if (pending && state !== 'conflict') await run();
      while (inflight) await inflight;
      return state === 'saved' && !pending;
    },
    /** A document replaced from outside: its hash, nothing pending. */
    reset(nextSha) {
      clearTimeout(timer);
      timer = null;
      pending = null;
      hash = nextSha;
      set('saved');
    },
    get state() { return state; },
    get sha() { return hash; },
    get dirty() { return Boolean(pending || inflight) || state !== 'saved'; },
  };
}

const EVIDENCED = new Set(['EVIDENCE_VERBATIM', 'RULE_REWRITE', 'AI_REWRITE']);

/** Reword a line. Evidence, origin and the replaced wording are kept. */
export function rewordLine(line, text) {
  if (line.override === 'NONE' && (EVIDENCED.has(line.origin) || line.origin === 'IMPORTED')) {
    line.override = 'EDITED';
    line.original_text = line.text;
  }
  line.text = text;
  if (line.original_text !== null && line.original_text !== undefined && text === line.original_text) {
    line.override = 'NONE';
    line.original_text = null;
  }
  return line;
}

/** A line the person types into this resume. It is not evidence. */
export function newLine(text = '') {
  return {
    id: ulid(), text, origin: 'USER_AUTHORED', evidence_ids: [], requirement_ids: [],
    override: 'NONE', original_text: null, hidden: false, flags: [],
  };
}

/** A confirmed Career Evidence statement, word for word, citing its claim. */
export function evidenceLine(claimKey, text) {
  return { ...newLine(text), origin: 'EVIDENCE_VERBATIM', evidence_ids: [claimKey] };
}

/** Move `list[index]` by `step` (-1 up, +1 down), in place. */
export function move(list, index, step) {
  const to = index + step;
  if (to < 0 || to >= list.length) return list;
  const [item] = list.splice(index, 1);
  list.splice(to, 0, item);
  return list;
}
