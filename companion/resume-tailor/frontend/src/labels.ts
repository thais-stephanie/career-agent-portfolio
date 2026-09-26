// Modified for the Career Agent public edition (2026-09-26). See NOTICE.
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
  // Career Agent profile (2026-09-26)
  profile: "PROFILE",
  followsCareerAgent: "Follows the active Career Agent profile",
  noBaseResume: "No base resume yet",
  noBaseResumeWhy: "Tailoring starts from a base resume. Make one from your confirmed Career Profile, or upload one.",
  fromProfile: "Use my Career Profile",
  fromProfileBusy: "Making a base resume from your confirmed Career Profile…",
  fromProfileDone: (roles: number, details: number) =>
    `Base resume made from your Career Profile: ${roles} role${roles === 1 ? "" : "s"}, ${details} confirmed detail${details === 1 ? "" : "s"}. Nothing was added that you have not confirmed.`,
  fromProfileFailed: "A base resume could not be made from your Career Profile.",
  uploadResume: "Upload a resume",
  uploadFormats: "PDF, Word (.docx) or Markdown (.md). It stays on this computer.",
  uploading: (name: string) => `Uploading “${name}”…`,
  uploadFailed: "The resume could not be uploaded.",
  fromCareerAgent: "FROM CAREER AGENT",
  handoffLoading: "Reading the posting from Career Agent…",
  handoffOtherProfile: "This link was opened for another Career Agent profile. It shows the active profile's data.",
  careerStatus: "Status in Career Agent",
  tailorThis: "Tailor",
  noTrackedJobs: "No postings are marked yet. In Career Agent, mark a posting Interested and it appears here.",
  otherResumes: "Other tailored resumes",
  importEvidence: "Update from Career Profile",
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
