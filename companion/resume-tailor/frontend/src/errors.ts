// Added for the Career Agent public edition (2026-09-26). See NOTICE.
// What a person reads when something goes wrong, in their language.
//
// The server sends `{code, message, params}` (api/errors.py). The page words
// the CODE here, in English or Portuguese, filling in the params; the
// server's English `message` is used only for a code this catalogue does not
// know. Nothing technical is ever shown: an internal failure is a generic
// sentence, and its detail stays in the local log.

export type Locale = "en" | "pt-BR";

const KEY = "rt.locale";

/** The reader's language: a `lang` the page was opened with (Career Agent
 *  passes its own), else the one chosen here before, else English. Never
 *  guessed from the browser. */
export function initialLocale(): Locale {
  try {
    const asked = new URLSearchParams(window.location.search).get("lang");
    if (asked === "pt-BR" || asked === "en") {
      localStorage.setItem(KEY, asked);
      return asked;
    }
    return localStorage.getItem(KEY) === "pt-BR" ? "pt-BR" : "en";
  } catch {
    return "en";
  }
}

let current: Locale = initialLocale();

export function getLocale(): Locale {
  return current;
}

export function setLocale(locale: Locale): void {
  current = locale;
  try { localStorage.setItem(KEY, locale); } catch { /* the choice lasts this visit */ }
}

type Params = Record<string, unknown>;
type Words = Record<string, (p: Params) => string>;

const q = (p: Params) => `“${String(p.file ?? "")}”`;

const EN: Words = {
  unsupported_format: () => "Upload a PDF, Word (.docx) or Markdown (.md) file. Other files are not read.",
  empty_file: (p) => `${q(p)} is empty.`,
  too_large: (p) => `${q(p)} is larger than ${p.mb ?? 10} MB. Upload a smaller copy.`,
  type_mismatch: (p) => `${q(p)} is not a real ${p.ext ?? ""} file. Save it again and retry.`,
  not_really: (p) => `${q(p)} is not really a ${p.ext ?? ""} file.`,
  not_text: (p) => `${q(p)} is not a text file.`,
  not_utf8: (p) => `${q(p)} is not UTF-8 text. Save it as UTF-8 and retry.`,
  unreadable: (p) => `${q(p)} could not be read. Is it a complete, unprotected file?`,
  no_text: (p) => `No text was found in ${q(p)}. A scanned PDF has no text to read; upload the Word or Markdown version instead.`,
  no_candidate: () => "No candidate is selected yet. Reload Resume Tailor and try again.",
  stale_profile: () => "Career Agent switched to another local profile. Reload Resume Tailor to follow it.",
  no_base_resume: () => "There is no base resume yet. Create one from your Career Profile or upload one.",
  backup_this_profile_only: () => "A backup can only be restored into this profile's Resume Tailor data.",
  profile_needs_dates: () => "Your confirmed experience needs its dates first: add the start and end months in Career Agent, then try again.",
  profile_empty: () => "Your Career Profile has no confirmed experience yet. Confirm some in Career Agent, or upload a resume here.",
  posting_not_found: () => "This posting is not in the active Career Agent profile.",
  not_connected: () => "Resume Tailor is not connected to Career Agent. Open it from Career Agent.",
  career_unavailable: () => "Career Agent could not answer. Try again in a moment.",
  busy: () => "Resume Tailor is busy with another task. Try again in a moment.",
  conflict: () => "That could not be done right now. Reload and try again.",
  not_found: () => "That is no longer here. Reload Resume Tailor.",
  not_available: () => "That is not available on this computer yet.",
  invalid: () => "That could not be done. Check what you entered and try again.",
  invalid_request: () => "That request was not complete. Reload Resume Tailor and try again.",
  refused: () => "Resume Tailor only answers its own page on this computer.",
  local_only: () => "Resume Tailor only answers its own page on this computer.",
  internal: () => "Something went wrong in Resume Tailor. Nothing was changed; try again.",
  network: () => "Resume Tailor is not answering. Is it still running?",
  backup_too_new: () => "This backup was made by a newer version of Resume Tailor. Update Resume Tailor, then try again.",
  pdf_unavailable: () => "PDF export isn't available on this computer yet: it needs Microsoft Word or LibreOffice installed. Word and Markdown export still work.",
};

const PT: Words = {
  unsupported_format: () => "Envie um arquivo PDF, Word (.docx) ou Markdown (.md). Outros arquivos não são lidos.",
  empty_file: (p) => `${q(p)} está vazio.`,
  too_large: (p) => `${q(p)} tem mais de ${p.mb ?? 10} MB. Envie uma cópia menor.`,
  type_mismatch: (p) => `${q(p)} não é um arquivo ${p.ext ?? ""} de verdade. Salve de novo e tente outra vez.`,
  not_really: (p) => `${q(p)} não é de fato um arquivo ${p.ext ?? ""}.`,
  not_text: (p) => `${q(p)} não é um arquivo de texto.`,
  not_utf8: (p) => `${q(p)} não está em UTF-8. Salve como UTF-8 e tente de novo.`,
  unreadable: (p) => `Não foi possível ler ${q(p)}. O arquivo está completo e sem proteção?`,
  no_text: (p) => `Nenhum texto foi encontrado em ${q(p)}. Um PDF escaneado não tem texto; envie a versão Word ou Markdown.`,
  no_candidate: () => "Nenhum candidato selecionado ainda. Recarregue o Resume Tailor e tente de novo.",
  stale_profile: () => "O Career Agent mudou para outro perfil local. Recarregue o Resume Tailor para acompanhar.",
  no_base_resume: () => "Ainda não há currículo base. Crie um a partir do seu Perfil de Carreira ou envie um.",
  backup_this_profile_only: () => "Um backup só pode ser restaurado nos dados do Resume Tailor deste perfil.",
  profile_needs_dates: () => "Sua experiência confirmada precisa das datas: adicione os meses de início e fim no Career Agent e tente de novo.",
  profile_empty: () => "Seu Perfil de Carreira ainda não tem experiência confirmada. Confirme alguma no Career Agent ou envie um currículo aqui.",
  posting_not_found: () => "Esta vaga não está no perfil ativo do Career Agent.",
  not_connected: () => "O Resume Tailor não está conectado ao Career Agent. Abra-o pelo Career Agent.",
  career_unavailable: () => "O Career Agent não respondeu. Tente de novo em instantes.",
  busy: () => "O Resume Tailor está ocupado com outra tarefa. Tente de novo em instantes.",
  conflict: () => "Não foi possível fazer isso agora. Recarregue e tente de novo.",
  not_found: () => "Isso não está mais aqui. Recarregue o Resume Tailor.",
  not_available: () => "Isso ainda não está disponível neste computador.",
  invalid: () => "Não foi possível fazer isso. Confira o que foi preenchido e tente de novo.",
  invalid_request: () => "A solicitação veio incompleta. Recarregue o Resume Tailor e tente de novo.",
  refused: () => "O Resume Tailor só responde à própria página neste computador.",
  local_only: () => "O Resume Tailor só responde à própria página neste computador.",
  internal: () => "Algo deu errado no Resume Tailor. Nada foi alterado; tente de novo.",
  network: () => "O Resume Tailor não está respondendo. Ele ainda está aberto?",
  backup_too_new: () => "Este backup foi feito por uma versão mais nova do Resume Tailor. Atualize o Resume Tailor e tente de novo.",
  pdf_unavailable: () => "A exportação em PDF ainda não está disponível neste computador: ela precisa do Microsoft Word ou do LibreOffice. As exportações Word e Markdown continuam funcionando.",
};

//: Codes that only say what KIND of failure it was. When the server sent a
//: sentence of its own with one of these, that sentence is more useful (it
//: names what to do), so it is shown as written.
const GENERIC = new Set(["not_found", "conflict", "invalid", "not_available"]);

export const CATALOGUES: Record<Locale, Words> = { en: EN, "pt-BR": PT };

/** An error the page can show: its code, and the sentence in the reader's language. */
export class ApiProblem extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

/** The sentence for a server error body (`detail`), in the current language. */
export function sentenceFor(detail: unknown, status: number): { code: string; text: string } {
  const words = CATALOGUES[current];
  if (detail && typeof detail === "object" && "code" in (detail as object)) {
    const d = detail as { code: string; message?: string; params?: Params };
    if (GENERIC.has(d.code) && d.message) return { code: d.code, text: d.message };
    const known = words[d.code];
    if (known) return { code: d.code, text: known(d.params ?? {}) };
    // A code this page does not know yet: the server's own sentence.
    return { code: d.code, text: d.message || words.internal({}) };
  }
  const code = status >= 500 ? "internal" : status === 404 ? "not_found" : "invalid";
  // A plain sentence from the server (below 500) is shown as written.
  if (typeof detail === "string" && detail && status < 500) return { code, text: detail };
  return { code, text: words[code]({}) };
}

/** A fixed sentence by code (for messages the page itself raises). */
export function sentence(code: string, params: Params = {}): string {
  return (CATALOGUES[current][code] ?? CATALOGUES[current].internal)(params);
}
