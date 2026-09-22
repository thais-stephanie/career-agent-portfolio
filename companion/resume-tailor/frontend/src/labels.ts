// Modified for the Career Agent public edition (2026-09-22). See NOTICE.
// Central display-label helpers (Phase 20). Every user-visible status string and
// status color comes from here — components never hardcode internal vocabulary.
// All strings live in one dictionary so a PT-BR translation can be added without
// touching components.

export type StatusTone =
  | "mint" | "mint-soft" | "yellow" | "yellow-soft" | "pink-soft"
  | "blue" | "blue-soft" | "lilac" | "orange" | "stone" | "surface-alt";

export const toneVar = (t: StatusTone) => `var(--${t})`;

export const STR = {
  appName: "Resume Tailor",
  local: "BETA",
  privacyLine: "Local storage. Optional AI can send content.",
  navWork: "WORK",
  navMaterial: "YOUR MATERIAL",
  navAccount: "ACCOUNT",
  overview: "Overview",
  tailor: "Tailor resume",
  applications: "Applications",
  baseResumes: "Base resumes",
  experience: "Experience",
  sources: "Sources",
  settings: "Settings",
  detailLevel: "Detail level",
  simple: "Simple",
  advanced: "Advanced",
  light: "Light",
  dark: "Dark",
  candidate: "CANDIDATE",
  switchTo: "SWITCH TO",
  newCandidate: "+ New candidate",
  importBackup: "↓ Import backup",
  exportBackup: "↑ Export backup",
  importBackupTitle: "Import a backup",
  tailorCta: "Tailor a resume →",
  analyze: "Analyze & tailor",
  saveToApplications: "Save to applications",
  exportMenu: "Export ▾",
  exportWord: "Word (.docx)",
  exportPdf: "PDF (.pdf)",
  exportMarkdown: "Markdown (.md)",
  exportPreparing: (format: string) => `Preparing ${format}…`,
  exportSaved: (name: string, pages?: number) =>
    pages ? `Saved as “${name}” · ${pages} page${pages === 1 ? "" : "s"}` : `Saved as “${name}”`,
  exportBlockedEditing: "Save or cancel the line you're editing first, so the export matches what you see.",
  exportUsesSaved: "Exports use the saved version of each resume, with all your edits.",
  pagesVerified: (n: number) => `${n} page${n === 1 ? "" : "s"} · verified`,
  editCheckTitle: "CHECK THIS EDIT",
  editCheckBody: "Your wording goes beyond what your sources show.",
  supported: "SUPPORTED",
  notEvidenced: "NOT EVIDENCED",
  undoEdit: "Undo edit",
  reviewEvidence: "Review evidence",
  whyTitle: "Why this bullet is here",
  backedBy: "BACKED BY",
  gapsReassurance: "Nothing here is written into your resume.",
  trimReassurance: "Nothing was deleted from your experience — lines that did not fit stayed in your library.",
  uploadReassurance: "You choose afterwards whether it also counts as a source for your experience.",
  filesNote: "Sources stay in your local workspace. Configured AI operations can send content. Back up from the candidate menu.",
  confirmHint: "Confirming a detail makes it usable in every resume.",
  reviewDetail: "Review",
  confirmDetail: "Confirm this detail",
  editBeforeConfirm: "Edit before confirming",
  leaveForLater: "Leave for later",
  addToExperience: "Add to experience",
  waitingForReview: "Waiting for review",
} as const;

// verdict labels arrive from the API already humanized; tones live here
export const RESULT_TONES: Record<string, StatusTone> = {
  "Strong match": "mint",
  "Related experience": "blue",
  "Partial match": "yellow",
  "Not evidenced": "pink-soft",
};

export const STATUS_TONES: Record<string, StatusTone> = {
  "Confirmed by you": "mint",
  "Supported by sources": "mint-soft",
  "Supported by project documentation": "mint-soft",
  "Supported by multiple sources": "mint-soft",
  "Supported by your own materials": "mint-soft",
  "From your portfolio": "lilac",
  "From your LinkedIn": "blue-soft",
  "From a resume": "surface-alt",
  "Needs review": "yellow",
  Conflicting: "yellow",
};

export const APP_STATUS_TONES: Record<string, StatusTone> = {
  Applied: "blue",
  Interviewing: "mint",
  Considering: "yellow",
  Closed: "stone",
};

export const SOURCE_KIND_TONES: Record<string, StatusTone> = {
  "Project documentation": "mint",
  "LinkedIn profile": "blue",
  Resume: "stone",
  Portfolio: "lilac",
  Document: "surface-alt",
  "Details you confirmed": "mint",
};

export function resultTone(label: string): StatusTone {
  return RESULT_TONES[label] ?? "surface-alt";
}

export function statusTone(label: string): StatusTone {
  return STATUS_TONES[label] ?? "surface-alt";
}
