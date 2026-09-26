// Added for the Career Agent public edition (2026-09-26). See NOTICE.
// Upload outcomes on screen, in the reader's language. Synthetic data only.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Sources } from "./screens/Material";
import { AppProvider } from "./state";
import { getLocale, initialLocale, sentence, sentenceFor, setLocale } from "./errors";

function stub(sourceAnswer: { status: number; body: unknown }) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const reply = (data: unknown, status = 200) =>
      ({ ok: status < 400, status, statusText: "", json: async () => data }) as Response;
    if (url === "/api/workspace") return reply({ mode: "standalone" });
    if (url === "/api/candidates") return reply([{ id: "alex-1", name: "Alex Morgan", archived: false, base_resumes: 0, applications: 0 }]);
    if (url.endsWith("/sources") && init?.method === "POST") return reply(sourceAnswer.body, sourceAnswer.status);
    return reply([]);
  }));
}

async function upload(name: string) {
  render(<AppProvider><Sources /></AppProvider>);
  const input = await screen.findByLabelText("Upload source document") as HTMLInputElement;
  await waitFor(() => expect(localStorage.getItem("rt.candidate")).toBe("alex-1"));
  fireEvent.change(input, { target: { files: [new File(["x"], name, { type: "application/pdf" })] } });
}

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); setLocale("en"); });

describe("sources uploads say what happened", () => {
  it("accepts only what the server reads", async () => {
    stub({ status: 200, body: [] });
    render(<AppProvider><Sources /></AppProvider>);
    const input = await screen.findByLabelText("Upload source document") as HTMLInputElement;
    expect(input.accept).toBe(".docx,.markdown,.md,.pdf,.txt");
  });

  it("shows a refusal on screen, in English", async () => {
    const detail = { code: "not_really", message: "server words", params: { file: "cv.pdf", ext: ".pdf" } };
    stub({ status: 400, body: { detail } });
    await upload("cv.pdf");
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("“cv.pdf” is not really a .pdf file."));
  });

  it("shows the same refusal in Portuguese", async () => {
    setLocale("pt-BR");
    const detail = { code: "not_really", message: "server words", params: { file: "cv.pdf", ext: ".pdf" } };
    stub({ status: 400, body: { detail } });
    await upload("cv.pdf");
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("“cv.pdf” não é de fato um arquivo .pdf."));
  });

  it("says a document is already there instead of adding it twice", async () => {
    stub({ status: 200, body: { id: "notes-abc", name: "notes.pdf", already: true, extracted: { details: 3 } } });
    await upload("notes.pdf");
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("Already uploaded as “notes.pdf”"));
  });
});

describe("error sentences", () => {
  it("never show internals, in either language", () => {
    expect(sentenceFor({ code: "internal", message: "Traceback at C:\\\\private" }, 500).text).not.toContain("private");
    setLocale("pt-BR");
    expect(sentence("stale_profile")).toContain("Recarregue");
    expect(sentenceFor("A plain sentence for the reader.", 404).text).toBe("A plain sentence for the reader.");
    expect(sentenceFor("anything", 500).text).toBe(sentence("internal"));
    expect(sentenceFor({ code: "a_new_code", message: "The server's own sentence." }, 400).text)
      .toBe("The server's own sentence.");
  });

  it("follow the language Career Agent passes, and remember it", () => {
    window.history.replaceState(null, "", "/?lang=pt-BR");
    expect(initialLocale()).toBe("pt-BR");
    window.history.replaceState(null, "", "/");
    expect(initialLocale()).toBe("pt-BR");
    localStorage.clear();
    expect(initialLocale()).toBe("en");
    expect(getLocale()).toBe("en");
  });
});
