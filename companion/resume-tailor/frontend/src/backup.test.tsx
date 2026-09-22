// Backup wiring: export downloads with the friendly filename and a clear success
// state; import validates through the backend, supports create-as-new and
// confirmed replace, and shows human-readable errors. No internal vocabulary.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "./screens/Material";
import { AppProvider } from "./state";

const NEWER_SCHEMA_MSG =
  "This backup was made by a newer version of Resume Tailor. Please update Resume Tailor to import it. Nothing was changed.";

function stubFetch(importResult: { ok: boolean; status?: number; body: unknown }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/api/candidates" && (!init || !init.method)) {
      return { ok: true, json: async () => [{ id: "alex-1", name: "Alex Morgan", archived: false, base_resumes: 1, applications: 0 }] } as Response;
    }
    if (url.endsWith("/backup")) {
      return {
        ok: true,
        blob: async () => new Blob(["zip"]),
        headers: { get: () => 'attachment; filename="Alex Morgan - Resume Tailor Backup.zip"' },
      } as unknown as Response;
    }
    if (url === "/api/candidates/import") {
      return { ok: importResult.ok, status: importResult.status ?? 200, json: async () => importResult.body } as Response;
    }
    return { ok: true, json: async () => ({}) } as Response;
  }));
  return calls;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:test"), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

describe("export backup", () => {
  it("downloads the candidate and confirms with the friendly filename only", async () => {
    stubFetch({ ok: true, body: {} });
    render(<AppProvider><Settings /></AppProvider>);
    fireEvent.click(await screen.findByText("↑ Export backup"));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    const toast = screen.getByRole("status").textContent ?? "";
    expect(toast).toContain("Alex Morgan - Resume Tailor Backup.zip");
    expect(toast).toContain("keep it somewhere safe");
    // no internal paths or candidate ids
    expect(toast).not.toMatch(/alex-1|\\|\//);
  });
});

describe("import backup", () => {
  const openDialogWithFile = async () => {
    render(<AppProvider><Settings /></AppProvider>);
    fireEvent.click(await screen.findByText("↓ Import backup"));
    const dialog = await screen.findByRole("dialog");
    const file = new File(["zip"], "Alex Morgan - Resume Tailor Backup.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("Backup file"), { target: { files: [file] } });
    return dialog;
  };

  it("imports as a new candidate, refreshes the selector and selects it", async () => {
    const calls = stubFetch({ ok: true, body: { id: "alex-2", name: "Alex Morgan (imported)" } });
    await openDialogWithFile();
    fireEvent.click(screen.getByText("Import"));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(screen.getByRole("status").textContent).toContain("Alex Morgan (imported)");
    const importCall = calls.find((c) => c.url === "/api/candidates/import");
    expect(importCall?.init?.body).toBeInstanceOf(FormData);
    // the selector refreshed after the import
    expect(calls.filter((c) => c.url === "/api/candidates" && !c.init?.method).length).toBeGreaterThan(1);
  });

  it("shows the human-readable message for a newer-version backup", async () => {
    stubFetch({ ok: false, status: 409, body: { detail: NEWER_SCHEMA_MSG } });
    await openDialogWithFile();
    fireEvent.click(screen.getByText("Import"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toContain("newer version of Resume Tailor");
  });

  it("replace mode requires choosing a candidate first", async () => {
    stubFetch({ ok: true, body: { id: "x", name: "X" } });
    await openDialogWithFile();
    fireEvent.click(screen.getByText("Replace an existing candidate"));
    fireEvent.click(screen.getByText("Import"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByRole("alert").textContent).toContain("Choose which candidate to replace");
  });

  it("keeps the dialog free of internal vocabulary", async () => {
    stubFetch({ ok: true, body: {} });
    const dialog = await openDialogWithFile();
    const html = dialog.innerHTML.toLowerCase();
    for (const term of ["workspace", "manifest", "schema", "candidate_id", "zip-slip", "replace_id"]) {
      expect(html).not.toContain(term);
    }
  });
});
