/**
 * The frontend gate. Syntax, imports, safety, hygiene.
 *
 * This project has no bundler and no TypeScript, and that is a decision rather
 * than an omission -- `docs/architecture/milestone-3-local-product.md` explains
 * why adding a toolchain to serve one page to one person would have been the
 * largest dependency decision in the project's history. But "we chose not to
 * install a linter" is not the same as "nothing checks this code", and the
 * difference is what this file closes.
 *
 * Four passes, all dependency-free, all runnable with the `node` already on the
 * machine:
 *
 *   1. PARSE      every module through `node --check`.
 *   2. RESOLVE    every relative import against the filesystem, so a renamed
 *                 file fails here instead of as a blank page. This is the
 *                 honest equivalent of a build: a bundler's real service is
 *                 proving the module graph closes, and so is this.
 *   3. SAFETY     the rules that matter for a page rendering third-party job
 *                 text -- no HTML-string sinks, no eval, no inline handlers,
 *                 no remote origins, nothing the CSP would reject at runtime.
 *   4. HYGIENE    what a linter would catch: leftover console.log, debugger,
 *                 var, loose equality, tabs, trailing whitespace, long lines.
 *
 * Exit 0 means clean; anything else prints file:line and why.
 * `tests/unit/test_frontend_gate.py` runs this so it cannot rot.
 */

import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..');
const STATIC = join(ROOT, 'src', 'career_agent', 'web', 'static');
const JS = join(STATIC, 'js');

// Chosen from the code's own practice, not imposed on it: p95 is 90 columns
// and p99 is 115, so 120 codifies what these modules already do and catches
// the outliers. A limit nobody meets is a limit that gets deleted.
const MAX_LINE = 120;
const NEWLINE = String.fromCharCode(10);
const CARRIAGE = String.fromCharCode(13);
const TAB = String.fromCharCode(9);

const problems = [];
const note = (file, line, rule, message) =>
  problems.push({ file: relative(ROOT, file).split(String.fromCharCode(92)).join('/'), line, rule, message });

function jsFiles() {
  return readdirSync(JS).filter((n) => n.endsWith('.js')).map((n) => join(JS, n));
}

function htmlFiles() {
  return readdirSync(STATIC).filter((n) => n.endsWith('.html')).map((n) => join(STATIC, n));
}

/**
 * Lines, with the line ENDING normalised away.
 *
 * The repository is developed on Windows and `.gitattributes` owns the CRLF
 * question, so a trailing carriage return is a checkout artefact rather than
 * something a person typed. Flagging it would make this gate fail on every file
 * on one platform and pass on the other, which is how a check gets switched off.
 */
function linesOf(file) {
  return readFileSync(file, 'utf8')
    .split(NEWLINE)
    .map((line) => (line.endsWith(CARRIAGE) ? line.slice(0, -1) : line));
}

function isComment(line) {
  const t = line.trim();
  return t.startsWith('//') || t.startsWith('*') || t.startsWith('/*');
}

// ---------------------------------------------------------------- 1. PARSE
//
// AS A MODULE, AND ON STDIN. `node --check some-file.js` parses the file as a
// SCRIPT, and for a file holding `export` it exits 0 without ever parsing the
// body: a catalogue entry with a real line break inside a quoted string --
// which is a syntax error in every JavaScript there is -- passed this gate and
// then broke the whole interface in the browser, where the same file is loaded
// as a module.
//
// Renaming everything to `.mjs` would fix it and would also change every
// `<script type="module" src>` in the page for the sake of a checker. Passing
// the source on stdin under `--input-type=module` is the same parse the
// browser does, with no rename.
//
// The cost is that the error says `[stdin]:781` instead of naming the file.
// The line number is what matters and it is recovered below; the file name is
// already on the finding.
function parsePass(files) {
  for (const file of files) {
    try {
      execFileSync(process.execPath, ['--check', '--input-type=module'], {
        input: readFileSync(file),
        stdio: 'pipe',
      });
    } catch (error) {
      const output = (error.stderr || Buffer.from('')).toString();
      const lines = output.split(NEWLINE).map((line) => line.trim()).filter(Boolean);
      const where = lines.find((line) => line.startsWith('[stdin]:'));
      const what = lines.find((line) => line.includes('Error')) || 'node --check failed';
      note(file, where ? Number(where.slice('[stdin]:'.length)) || 0 : 0, 'parse', what);
    }
  }
}

// -------------------------------------------------------------- 2. RESOLVE
function resolvePass(files) {
  const IMPORT = /(?:^|\n)\s*(?:import|export)[^'"\n]*?from\s*['"]([^'"]+)['"]/g;
  const DYNAMIC = /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g;
  for (const file of files) {
    const source = readFileSync(file, 'utf8');
    for (const pattern of [IMPORT, DYNAMIC]) {
      pattern.lastIndex = 0;
      let match = pattern.exec(source);
      while (match !== null) {
        const specifier = match[1];
        const line = source.slice(0, match.index).split(NEWLINE).length;
        if (!specifier.startsWith('.')) {
          note(file, line, 'import/bare', 'bare specifier "' + specifier + '" -- there is no bundler');
        } else {
          const target = resolve(dirname(file), specifier);
          if (!existsSync(target) || !statSync(target).isFile()) {
            note(file, line, 'import/missing', '"' + specifier + '" does not resolve to a file');
          }
        }
        match = pattern.exec(source);
      }
    }
  }
}

// --------------------------------------------------------------- 3. SAFETY
const SINKS = [
  [/\.innerHTML\s*=/, 'innerHTML assignment'],
  [/\.outerHTML\s*=/, 'outerHTML assignment'],
  [/insertAdjacentHTML\s*\(/, 'insertAdjacentHTML'],
  [/document\.write\s*\(/, 'document.write'],
  [/[^.\w]eval\s*\(/, 'eval'],
  [/new\s+Function\s*\(/, 'new Function'],
];

// http(s) to anywhere but this origin. `example.invalid` is the demo corpus's
// reserved TLD and appears in fixture data, which is data and not a request.
const REMOTE = /['"`]https?:\/\/(?!127\.0\.0\.1|localhost)[^'"`]+['"`]/;

// XML NAMESPACES ARE NAMES, NOT ADDRESSES.
//
// `document.createElementNS('http://www.w3.org/2000/svg', 'path')` fetches
// nothing; the string is an identifier the DOM compares for equality, and the
// SVG specification fixes its spelling. Nothing may be added to this list
// that a browser would ever RESOLVE -- an image, a stylesheet, a font, an
// API. Those are exactly what the rule above exists to stop.
const NAMESPACES = ['http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xhtml'];

function isNamespaceOnly(line) {
  let rest = line;
  for (const namespace of NAMESPACES) rest = rest.split(namespace).join('');
  return !REMOTE.test(rest);
}

function safetyPass(files) {
  for (const file of files) {
    linesOf(file).forEach((line, index) => {
      if (isComment(line)) return;
      for (const [pattern, label] of SINKS) {
        if (pattern.test(line)) {
          note(file, index + 1, 'safety/sink', label + ' -- job text is untrusted; build DOM instead');
        }
      }
      if (REMOTE.test(line) && !line.includes('example.invalid') && !isNamespaceOnly(line)) {
        note(file, index + 1, 'safety/remote', 'remote origin -- the CSP forbids it, and the app is offline');
      }
    });
  }
}

function htmlPass(files) {
  const INLINE_HANDLER = /\son[a-z]+\s*=\s*["']/i;
  const REMOTE_ASSET = /(?:src|href)\s*=\s*["']https?:\/\//i;
  for (const file of files) {
    linesOf(file).forEach((line, index) => {
      if (INLINE_HANDLER.test(line)) {
        note(file, index + 1, 'safety/inline-handler', 'inline handler -- the CSP has no unsafe-inline');
      }
      if (REMOTE_ASSET.test(line)) {
        note(file, index + 1, 'safety/remote-asset', 'remote asset -- everything ships in the repo');
      }
    });
  }
}

// -------------------------------------------------------------- 4. HYGIENE
const HYGIENE = [
  [/\bconsole\.log\s*\(/, 'hygiene/console', 'console.log left in'],
  [/\bdebugger\b/, 'hygiene/debugger', 'debugger statement'],
  [/(?:^|[^.\w])var\s+[A-Za-z_$]/, 'hygiene/var', 'var -- use const or let'],
  [/[^=!<>]==[^=]/, 'hygiene/eq', 'loose equality -- use ==='],
  [/[^!=<>]!=[^=]/, 'hygiene/eq', 'loose inequality -- use !=='],
];

function hygienePass(files) {
  for (const file of files) {
    linesOf(file).forEach((line, index) => {
      const at = index + 1;
      if (line.includes(TAB)) note(file, at, 'hygiene/tab', 'tab character');
      if (/\s$/.test(line)) note(file, at, 'hygiene/trailing', 'trailing whitespace');
      if (line.length > MAX_LINE) {
        note(file, at, 'hygiene/length', line.length + ' > ' + MAX_LINE + ' columns');
      }
      if (isComment(line)) return;
      for (const [pattern, rule, message] of HYGIENE) {
        if (pattern.test(line)) note(file, at, rule, message);
      }
    });
  }
}

// ------------------------------------------------------------------- run
const modules = jsFiles();
const pages = htmlFiles();

if (modules.length === 0) {
  console.error('no modules found -- is the static directory where this expects it?');
  process.exit(2);
}

parsePass(modules);
resolvePass(modules);
safetyPass(modules);
htmlPass(pages);
hygienePass(modules);

if (problems.length === 0) {
  console.log('frontend check: ' + modules.length + ' modules, ' + pages.length + ' pages -- clean');
  console.log('  parse . resolve . safety . hygiene');
  process.exit(0);
}

problems.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line);
for (const p of problems) {
  console.error(p.file + ':' + p.line + ': ' + p.rule + ': ' + p.message);
}
console.error(NEWLINE + problems.length + ' problem(s)');
process.exit(1);
