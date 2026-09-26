// Information hierarchy: the answer first, one short reason, details only on demand.
// Overview leads with what needs a decision (no row of stat cards); Match rows show
// status + requirement + one line, and reveal the backing experience behind "Why?".
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Overview } from "./screens/Overview";
import { Tailor } from "./screens/Tailor";
import { AppProvider } from "./state";
import { STR } from "./labels";

const VIEW = {
  job: { role: "Automation Engineer", company: "Initech", required: ["HubSpot automation"], preferred: ["Adobe Analytics"], conditions: [] },
  match: {
    strong: 1, related: 0, gaps: ["Adobe Analytics"],
    rows: [
      { requirement: "HubSpot automation", kind: "Required", result: "Strong match", why: "Your experience covers this directly.",
        backed_by: [{ company: "Initech", text: "Built and owned HubSpot automation for onboarding." }] },
      { requirement: "Adobe Analytics", kind: "Preferred", result: "Not evidenced", why: "Nothing in your experience covers this yet.", backed_by: [] },
    ],
  },
  resume: { experience: [{ company: "Initech", title: "Engineer", bullets: ["Built the billing automation."] }] },
  checks: [{ name: "Length", level: "ok", note: "Fits two pages." }],
  pages: { verified: true, actual: 2, how: "Checked in Word", estimate: 2 },
  filename: "Alex Morgan - Automation Engineer.docx",
};
const DRAFT = {
  can_undo: false,
  resume: { headline: "Automation Engineer", summary: ["Builds automation."], edited: false, skills: [], certifications: [], note: "", unsupported_bullets: [],
    experience: [{ position_id: "p1", title: "Engineer", company: "Initech", bullets: [
      { id: "b1", text: "Built the billing automation.", auto_text: "Built the billing automation.", supported: true, hidden: false, locked: false, edited: false }] }] },
};

function stubFetch() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const body = (data: unknown) => ({ ok: true, status: 200, json: async () => data }) as Response;
    if (url === "/api/candidates") return body([{ id: "alex-1", name: "Alex Morgan", archived: false, base_resumes: 1, applications: 1 }]);
    if (url.includes("/config")) return body({ candidate: { name: "Alex Morgan", location: "Remote" }, base_resumes: [{ id: "r1", name: "Main" }] });
    if (url.includes("/experience")) return body({ total: 12, confirmed: 3, supported: 6, needs_review: 2, conflicting: 1, items: [] });
    if (url.endsWith("/tailor")) return body({ application_id: "app-1" });
    if (url.includes("/applications/app-1/draft")) return body(DRAFT);
    if (url.includes("/applications/app-1")) return body({ status: { status: "done" }, view: VIEW });
    if (url.endsWith("/applications")) return body([{ id: "app-1", role: "Automation Engineer", company: "Initech", status: "Applied", date: "2026-09-20", base_resume: "Main", match: 0.8, state: "done" }]);
    if (url.includes("/resumes")) return body([{ id: "r1", name: "Main", headline: "Engineer", roles: 0, skills: 4, default: true }]);
    return body({});
  }));
}

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); stubFetch(); });

describe("overview hierarchy", () => {
  it("leads with what needs review and recent work, not a row of stat cards", async () => {
    render(<AppProvider><Overview /></AppProvider>);
    await waitFor(() => expect(screen.getByText("3 details need your review.")).toBeInTheDocument());
    expect(document.querySelectorAll(".stat")).toHaveLength(0);
    expect(document.querySelectorAll(".card")).toHaveLength(0); // grouped rows, not shadowed cards
    // the review notice comes before the applications list in reading order
    const notice = screen.getByRole("note");
    const apps = screen.getByText("Recent applications");
    expect(notice.compareDocumentPosition(apps) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(STR.tailorCta)).toBeInTheDocument();
    // counts live in the header line
    expect(screen.getByText(/1 base resume · 12 experience details · 1 application/)).toBeInTheDocument();
  });
});

describe("match hierarchy", () => {
  async function tailored() {
    render(<AppProvider><Tailor /></AppProvider>);
    await waitFor(() => expect(screen.getByLabelText("Job description")).toBeInTheDocument());
    // Analyze waits for the base resumes to load.
    await waitFor(() => expect(screen.getByLabelText("Base resume")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Job description"), { target: { value: "x".repeat(60) } });
    fireEvent.click(screen.getByText(STR.analyze));
    await waitFor(() => expect(screen.getByText("What this job asks for")).toBeInTheDocument());
  }

  it("shows status, requirement and one line by default; backing experience only behind Why?", async () => {
    await tailored();
    expect(screen.getByText("Strong match")).toBeInTheDocument();
    expect(screen.getByText("HubSpot automation")).toBeInTheDocument();
    expect(screen.getByText("Your experience covers this directly.")).toBeInTheDocument();
    expect(screen.queryByText("Built and owned HubSpot automation for onboarding.")).toBeNull();
    // one Why? disclosure: the strong match has backing, the gap has nothing to show
    const whys = Array.from(document.querySelectorAll(".rows .disclosure"));
    expect(whys.map((b) => b.textContent)).toEqual(["Why?"]);
    fireEvent.click(whys[0]);
    expect(screen.getByText("Built and owned HubSpot automation for onboarding.")).toBeInTheDocument();
    // required is the default and stays quiet; preferred is marked, secondary
    expect(screen.getByText("preferred")).toHaveClass("faint");
    expect(screen.queryByText("required")).toBeNull();
  });

  it("folds setup after a run and keeps the tabs to three in simple mode", async () => {
    await tailored();
    expect(screen.queryByLabelText("Job description")).toBeNull();
    expect(screen.getByText("Change")).toBeInTheDocument();
    const tabs = Array.from(document.querySelectorAll(".tabs button")).map((b) => b.textContent);
    expect(tabs).toEqual(["Match", "Job posting", "Checks"]);
    // gaps are visible in the first tab without opening anything
    expect(screen.getAllByText("Adobe Analytics").length).toBeGreaterThan(0);
    // plan and evidence live behind disclosures in Checks
    fireEvent.click(screen.getByText("Checks"));
    expect(screen.getByText("1 / 1 checks passed")).toBeInTheDocument();
    expect(screen.queryByText("Started from your base resume")).toBeNull();
    fireEvent.click(screen.getByText("How this resume was put together"));
    expect(screen.getByText("Started from your base resume")).toBeInTheDocument();
  });
});
