// Added for the Career Agent public edition (2026-09-26). See NOTICE.
// Resume Tailor following a Career Agent profile: one visible profile, the
// posting handed over by id, the empty base-resume state, upload errors on
// screen, and Career Agent's application statuses. Synthetic data only.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { candidatePath } from "./api";
import { STR } from "./labels";

const PROFILE = { id: "prof-01SYNTHETIC", label: "Synthetic profile" };
const STATUSES = [
  { value: "DISCOVERED", label: "Found" }, { value: "SHORTLISTED", label: "Interested" },
  { value: "APPLIED", label: "Applied" }, { value: "INTERVIEW", label: "Interviewing" },
];
const JOB = {
  job_id: "01JOBSYNTHETIC", title: "Integration Specialist", company: "Example Systems", url: "https://example.invalid/j",
  closed: false, status: "SHORTLISTED", status_label: "Interested", applied_at: null, updated_at: "2026-09-26",
  description: "A synthetic posting that asks for integrations, APIs and careful documentation work.",
};

type Opts = { resumes?: unknown[]; uploadError?: string };

function stub(opts: Opts = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  let resumes = opts.resumes ?? [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = (data: unknown, ok = true, status = 200) => ({ ok, status, statusText: "", json: async () => data }) as Response;
    if (url === "/api/workspace") {
      return body({ mode: "profile", profile: PROFILE, candidate_id: "prof-01synthetic", candidate_name: "Synthetic profile", statuses: STATUSES });
    }
    if (url === `/api/career/jobs/${JOB.job_id}`) return body(JOB);
    if (url === "/api/career/base-resume") {
      resumes = [{ id: "career_agent_profile", name: "From my Career Profile", headline: "", roles: 1, skills: 2, default: true }];
      return body({ id: "career_agent_profile", name: "From my Career Profile", roles: 1, details: 3 });
    }
    if (url === "/api/career/applications") return body({ statuses: STATUSES, jobs: [{ ...JOB, tailored: [] }] });
    if (url.startsWith("/api/career/applications/")) return body({ ...JOB, status: "APPLIED", status_label: "Applied" });
    if (url.endsWith("/resumes/upload")) {
      return opts.uploadError ? body({ detail: opts.uploadError }, false, 400) : body({ id: "r", name: "R", extracted: { roles: 1, details: 1, skills: 1 } });
    }
    if (url.endsWith("/resumes")) return body(resumes);
    if (url.endsWith("/applications")) return body([]);
    if (url.includes("/experience")) return body({ total: 0, confirmed: 0, supported: 0, needs_review: 0, conflicting: 0, items: [] });
    return body({});
  }));
  return calls;
}

function at(search: string) {
  window.history.replaceState(null, "", `/${search}`);
}

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); at(""); });

describe("following a Career Agent profile", () => {
  it("shows the profile, not a candidate switcher, and never an empty candidate", async () => {
    stub();
    render(<App />);
    await waitFor(() => expect(screen.getByText(PROFILE.label, { selector: ".nm" })).toBeInTheDocument());
    expect(screen.getByText(STR.followsCareerAgent)).toBeInTheDocument();
    expect(screen.queryByText(STR.newCandidate)).toBeNull();
    expect(document.querySelector(".sb-profile")?.getAttribute("data-profile-id")).toBe(PROFILE.id);
  });

  it("opens the handed-over posting by id with its text and Career Agent status", async () => {
    const calls = stub();
    at(`?job=${JOB.job_id}&profile=${PROFILE.id}`);
    render(<App />);
    await waitFor(() => expect(document.querySelector(".career-job")).not.toBeNull());
    expect(document.querySelector(".career-job")?.getAttribute("data-job-id")).toBe(JOB.job_id);
    expect(document.querySelector(".career-status")?.textContent).toBe("Interested");
    expect((screen.getByLabelText("Job description") as HTMLTextAreaElement).value).toBe(JOB.description);
    // only the id travelled: nothing was posted to get the text
    expect(calls.some((c) => c.url === `/api/career/jobs/${JOB.job_id}`)).toBe(true);
    expect(screen.queryByText(STR.handoffOtherProfile)).toBeNull();
  });

  it("says when a link was opened for another profile", async () => {
    stub();
    at(`?job=${JOB.job_id}&profile=prof-01SOMEONEELSE`);
    render(<App />);
    await waitFor(() => expect(screen.getByText(STR.handoffOtherProfile)).toBeInTheDocument());
  });

  it("with no base resume, shows the empty state and Analyze cannot run", async () => {
    const calls = stub();
    at(`?job=${JOB.job_id}`);
    render(<App />);
    await waitFor(() => expect(screen.getByText(STR.noBaseResume)).toBeInTheDocument());
    expect(screen.getByText(STR.analyze).closest("button")).toBeDisabled();
    fireEvent.click(screen.getByText(STR.fromProfile));
    await waitFor(() => expect(screen.getByLabelText("Base resume")).toBeInTheDocument());
    expect(calls.some((c) => c.url === "/api/career/base-resume" && c.init?.method === "POST")).toBe(true);
    expect(screen.getByText(STR.analyze).closest("button")).not.toBeDisabled();
  });

  it("puts an upload failure on screen, in the server's words", async () => {
    stub({ uploadError: "“cv.pdf” is not really a .pdf file." });
    render(<App />);
    await waitFor(() => expect(screen.getByText(STR.tailorCta)).toBeInTheDocument());
    fireEvent.click(screen.getByText(STR.tailorCta));
    await waitFor(() => expect(screen.getByText(STR.noBaseResume)).toBeInTheDocument());
    const input = screen.getByLabelText(STR.uploadResume) as HTMLInputElement;
    expect(input.accept).toBe(".pdf,.docx,.md,.markdown,.txt");
    fireEvent.change(input, { target: { files: [new File(["x"], "cv.pdf", { type: "application/pdf" })] } });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("not really a .pdf"));
  });

  it("lists Career Agent's postings as the applications and changes status there", async () => {
    const calls = stub();
    render(<App />);
    await waitFor(() => expect(screen.getByText("Applications", { selector: "button" })).toBeInTheDocument());
    fireEvent.click(screen.getByText("Applications", { selector: "button" }));
    const select = await screen.findByLabelText(`${STR.careerStatus}: ${JOB.title}`) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(STATUSES.map((s) => s.label));
    expect(Array.from(select.options).some((o) => o.value === "TO_APPLY")).toBe(false);
    fireEvent.change(select, { target: { value: "APPLIED" } });
    await waitFor(() => expect(calls.some((c) => c.url === `/api/career/applications/${JOB.job_id}` && c.init?.method === "PATCH")).toBe(true));
    // Every call names the profile the page was opened for (a stale tab is refused).
    const patch = calls.find((c) => c.url === `/api/career/applications/${JOB.job_id}`);
    expect(new Headers(patch?.init?.headers).get("X-Local-Profile")).toBe(PROFILE.id);
  });
});

describe("the candidate path", () => {
  it("refuses an empty candidate id before any request", () => {
    expect(() => candidatePath("")).toThrow(/No candidate is selected/);
    expect(candidatePath("prof-01synthetic")).toBe("/api/candidates/prof-01synthetic");
  });
});
