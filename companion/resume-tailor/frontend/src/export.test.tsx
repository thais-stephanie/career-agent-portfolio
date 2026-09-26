// Multi-format export controls: one Export menu offering Word, PDF and Markdown in the
// Tailor screen and on every saved Application; downloads carry the friendly server
// filename; an unavailable PDF is explained in plain language; an open line edit blocks
// export so an older version is never silently shipped. No internal vocabulary.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Applications } from "./screens/Material";
import { Tailor } from "./screens/Tailor";
import { AppProvider } from "./state";
import { STR } from "./labels";

const PDF_UNAVAILABLE =
  "PDF export isn't available on this computer yet: it needs Microsoft Word or LibreOffice installed. Word and Markdown export still work.";

const VIEW = {
  job: { role: "Automation Engineer", company: "Initech", required: [], preferred: [], conditions: [] },
  match: { strong: 1, related: 0, gaps: [], rows: [] },
  resume: { experience: [] },
  checks: [],
  pages: { verified: false, estimate: 1, how: "estimated" },
  filename: "Alex Morgan - Automation Engineer.docx",
};
const DRAFT = {
  can_undo: false,
  resume: {
    headline: "Automation Engineer", summary: ["Builds automation."], edited: false,
    experience: [{ position_id: "p1", title: "Engineer", company: "Initech", bullets: [
      { id: "b1", text: "Built the billing automation.", auto_text: "Built the billing automation.", supported: true, hidden: false, locked: false, edited: false },
    ] }],
    skills: [], certifications: [], note: "", unsupported_bullets: [],
  },
};

function file(name: string, extra: Record<string, string> = {}) {
  const headers: Record<string, string> = { "Content-Disposition": `attachment; filename="${name}"`, ...extra };
  return {
    ok: true, status: 200,
    blob: async () => new Blob(["bytes"]),
    headers: { get: (k: string) => headers[k] ?? null },
  } as unknown as Response;
}

function stubFetch(opts: { pdfOk: boolean }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = (data: unknown) => ({ ok: true, status: 200, json: async () => data }) as Response;
    if (url === "/api/candidates") return body([{ id: "alex-1", name: "Alex Morgan", archived: false, base_resumes: 1, applications: 1 }]);
    if (url.endsWith("/export/docx")) return file("Alex Morgan - Automation Engineer.docx");
    if (url.endsWith("/export/md")) return file("Alex Morgan - Automation Engineer.md");
    if (url.endsWith("/export/pdf")) {
      return opts.pdfOk
        ? file("Alex Morgan - Automation Engineer.pdf", { "X-Resume-Pages": "2" })
        : ({ ok: false, status: 501, json: async () => ({ detail: { code: "pdf_unavailable", message: PDF_UNAVAILABLE, params: {} } }) } as Response);
    }
    if (url.endsWith("/tailor")) return body({ application_id: "app-1" });
    if (url.includes("/applications/app-1/draft")) return body(DRAFT);
    if (url.includes("/applications/app-1")) return body({ status: { status: "done" }, view: VIEW });
    if (url.endsWith("/applications")) return body([
      { id: "app-1", role: "Automation Engineer", company: "Initech", status: "Applied", date: "2026-09-20", base_resume: "Main", match: 0.8, state: "done" },
      { id: "app-2", role: "Data Engineer", company: "Globex", status: "Considering", date: "2026-09-21", base_resume: "Main", match: null, state: "running" },
    ]);
    if (url.includes("/resumes")) return body([{ id: "r1", name: "Main", headline: "Engineer", roles: 0, skills: 4, default: true }]);
    return body({});
  }));
  return calls;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:test"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

const openMenu = async () => {
  if (!screen.queryByRole("menu")) fireEvent.click(screen.getByText(STR.exportMenu));
  await waitFor(() => expect(screen.getByRole("menu")).toBeInTheDocument());
};

describe("saved applications", () => {
  it("offers Word, PDF and Markdown for every finished application, using its saved draft", async () => {
    const calls = stubFetch({ pdfOk: true });
    render(<AppProvider><Applications /></AppProvider>);
    await waitFor(() => expect(screen.getByText("Automation Engineer")).toBeInTheDocument());
    expect(screen.getByText(STR.exportUsesSaved, { exact: false })).toBeInTheDocument();
    // one finished row exports; the still-running one does not pretend to
    expect(screen.getAllByText(STR.exportMenu)).toHaveLength(1);
    expect(screen.getByText("Still working…")).toBeInTheDocument();
    await openMenu();
    const items = screen.getAllByRole("menuitem").map((b) => b.textContent);
    expect(items).toEqual([STR.exportWord, STR.exportPdf, STR.exportMarkdown]);
    fireEvent.click(screen.getByText(STR.exportMarkdown));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved as “Alex Morgan - Automation Engineer.md”"));
    const hit = calls.find((c) => c.url.endsWith("/export/md"));
    expect(hit?.url).toBe("/api/candidates/alex-1/applications/app-1/export/md");
    // nothing re-ran tailoring
    expect(calls.some((c) => c.url.endsWith("/tailor"))).toBe(false);
  });

  it("reports the PDF page count from the local render and never leaks technical words", async () => {
    stubFetch({ pdfOk: true });
    render(<AppProvider><Applications /></AppProvider>);
    await waitFor(() => expect(screen.getByText(STR.exportMenu)).toBeInTheDocument());
    await openMenu();
    fireEvent.click(screen.getByText(STR.exportPdf));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved as “Alex Morgan - Automation Engineer.pdf” · 2 pages"));
    const html = document.body.innerHTML.toLowerCase();
    for (const term of ["renderer", "backend", "conversion engine", "exporter", "libreoffice"]) expect(html).not.toContain(term);
  });

  it("explains an unavailable PDF in plain language and keeps Word working", async () => {
    stubFetch({ pdfOk: false });
    render(<AppProvider><Applications /></AppProvider>);
    await waitFor(() => expect(screen.getByText(STR.exportMenu)).toBeInTheDocument());
    await openMenu();
    fireEvent.click(screen.getByText(STR.exportPdf));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(PDF_UNAVAILABLE));
    await openMenu();
    fireEvent.click(screen.getByText(STR.exportWord));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved as “Alex Morgan - Automation Engineer.docx”"));
  });
});

describe("tailor screen", () => {
  async function tailored() {
    const calls = stubFetch({ pdfOk: true });
    render(<AppProvider><Tailor /></AppProvider>);
    await waitFor(() => expect(screen.getByLabelText("Job description")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Job description"), { target: { value: "x".repeat(60) } });
    fireEvent.click(screen.getByText(STR.analyze));
    await waitFor(() => expect(screen.getByText(STR.exportMenu)).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("Built the billing automation.")).toBeInTheDocument());
    return calls;
  }

  it("replaces the single Word action with an Export menu of all three formats", async () => {
    const calls = await tailored();
    expect(screen.queryByText("Export Word ↓")).toBeNull();
    await openMenu();
    expect(screen.getAllByRole("menuitem").map((b) => b.textContent)).toEqual([STR.exportWord, STR.exportPdf, STR.exportMarkdown]);
    fireEvent.click(screen.getByText(STR.exportWord));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved as “Alex Morgan - Automation Engineer.docx”"));
    expect(calls.some((c) => c.url === "/api/candidates/alex-1/applications/app-1/export/docx")).toBe(true);
  });

  it("makes an unsaved line edit explicit instead of silently exporting the older version", async () => {
    const calls = await tailored();
    fireEvent.click(screen.getByText("Edit"));
    await waitFor(() => expect(screen.getByLabelText("Edit resume line")).toBeInTheDocument());
    await openMenu();
    expect(screen.getByRole("note")).toHaveTextContent(STR.exportBlockedEditing);
    for (const item of screen.getAllByRole("menuitem")) expect(item).toBeDisabled();
    fireEvent.click(screen.getByText(STR.exportPdf));
    expect(calls.some((c) => c.url.includes("/export/"))).toBe(false);
    // cancelling the edit unblocks export
    fireEvent.click(screen.getByText("Cancel"));
    await openMenu();
    expect(screen.queryByRole("note")).toBeNull();
    for (const item of screen.getAllByRole("menuitem")) expect(item).toBeEnabled();
  });
});
