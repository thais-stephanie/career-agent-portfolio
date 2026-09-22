// Frontend behaviour tests: simple mode never RENDERS internal vocabulary (not merely
// hides it), advanced mode may show diagnostics, and the label helpers stay humane.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { Experience } from "./screens/Experience";
import { AppProvider } from "./state";
import { RESULT_TONES, STATUS_TONES, STR } from "./labels";

const FORBIDDEN = [
  "raw_terms", "evidence_bank", "must_have", "nice_to_have", "user_verified",
  "secondary_multi_source", "source_strength", "profile_id", "run_id",
  "MatchType", "EvidenceRecord", "corroborated_by",
];

const EXPERIENCE_SIMPLE = {
  total: 2, confirmed: 1, supported: 1, needs_review: 0, conflicting: 0,
  items: [
    { company: "Initech", title: "Automation ownership", text: "Owned the billing automation.",
      status: "Confirmed by you", sources: ["Project documentation"] },
    { company: "Initech", title: "Reporting", text: "Built reporting.",
      status: "Supported by sources", sources: ["Resume"] },
  ],
};

const EXPERIENCE_ADVANCED = {
  ...EXPERIENCE_SIMPLE,
  items: EXPERIENCE_SIMPLE.items.map((i, n) => ({
    ...i,
    advanced: { evidence_id: `initech_00${n}`, source_strength: "primary", verification: "user_verified", proficiency: "led" },
  })),
};

const CONFLICT = {
  id: "c1",
  topic: "Cobalt Peak end date",
  message: "We found different versions of this detail.",
  versions: [
    { source: "Resume", statement: "Automation Specialist — Current" },
    { source: "LinkedIn profile", statement: "Automation Specialist — ended December 2022" },
  ],
};

function mockFetch(advancedPayloads: boolean, opts?: { conflicts?: unknown[] }) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = (data: unknown) => ({ ok: true, json: async () => data }) as Response;
    if (url === "/api/candidates") return body([{ id: "alex-1", name: "Alex Morgan", archived: false, base_resumes: 1, applications: 0 }]);
    if (url.includes("/config")) return body({ candidate: { name: "Alex Morgan", location: "Remote" }, base_resumes: [{ id: "r1", name: "Main" }], settings: {}, focus_areas: [], experience: { details: 2, conflicts: 0 } });
    if (url.includes("/experience")) return body(url.includes("advanced=1") || advancedPayloads ? EXPERIENCE_ADVANCED : EXPERIENCE_SIMPLE);
    if (url.includes("/confirm")) return body({ status: "Confirmed by you", topic: CONFLICT.topic });
    if (url.includes("/conflicts")) return body(opts?.conflicts ?? []);
    if (url.includes("/decisions")) return body([]);
    if (url.includes("/applications")) return body([]);
    if (url.includes("/resumes")) return body([{ id: "r1", name: "Main", headline: "Engineer", roles: 0, skills: 4, default: true }]);
    if (url.includes("/sources")) return body([]);
    return body({});
  }));
  return calls;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("simple mode", () => {
  it("renders the shell and overview without any internal vocabulary", async () => {
    mockFetch(false);
    render(<App />);
    await waitFor(() => expect(screen.getAllByText("Alex Morgan").length).toBeGreaterThan(0));
    const html = document.body.innerHTML;
    for (const term of FORBIDDEN) expect(html).not.toContain(term);
    expect(screen.getByText(STR.privacyLine)).toBeInTheDocument();
  });

  it("lets the user resolve a conflict by picking a version or typing the correct value", async () => {
    const calls = mockFetch(false, { conflicts: [CONFLICT] });
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getByText("Which one is correct?")).toBeInTheDocument());
    // both source versions are visible, each with its own pick action
    expect(screen.getByText(CONFLICT.versions[0].statement)).toBeInTheDocument();
    expect(screen.getByText(CONFLICT.versions[1].statement)).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("This one is correct")[1]);
    await waitFor(() => expect(calls.some((c) => c.url.includes("/conflicts/c1/confirm"))).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/confirm"))!.init!.body));
    expect(sent.choice).toBe(CONFLICT.versions[1].statement);
    // no override vocabulary anywhere in the flow
    expect(document.body.innerHTML.toLowerCase()).not.toContain("override");
  });

  it("offers a manual correction with value and note", async () => {
    const calls = mockFetch(false, { conflicts: [CONFLICT] });
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getByText("Neither — enter the correct information")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Neither — enter the correct information"));
    fireEvent.change(screen.getByPlaceholderText("e.g. September 2026"), { target: { value: "September 2026" } });
    fireEvent.change(screen.getByPlaceholderText("e.g. The resume was outdated."), { target: { value: "The resume was outdated." } });
    fireEvent.click(screen.getByText("Confirm"));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/confirm"))).toBe(true));
    const sent = JSON.parse(String(calls.find((c) => c.url.includes("/confirm"))!.init!.body));
    expect(sent.value).toBe("September 2026");
    expect(sent.note).toBe("The resume was outdated.");
  });

  it("experience items never render evidence ids in simple mode", async () => {
    mockFetch(false);
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getAllByText("Confirmed by you").length).toBeGreaterThan(0));
    expect(document.body.innerHTML).not.toContain("initech_00");
    expect(document.body.innerHTML).not.toContain("user_verified");
  });
});

describe("advanced mode", () => {
  it("may render diagnostics once the user opts in", async () => {
    localStorage.setItem("rt.advanced", "1");
    mockFetch(true);
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getByText(/initech_000/)).toBeInTheDocument());
  });
});

describe("labels", () => {
  it("maps every verdict and status to a tone surface, never a text color", () => {
    for (const label of Object.keys(RESULT_TONES)) expect(label).not.toMatch(/[a-z]_[a-z]/);
    for (const label of Object.keys(STATUS_TONES)) expect(label).not.toMatch(/[A-Z]{2,}_/);
  });
  it("keeps product copy free of internal terms", () => {
    const all = JSON.stringify(STR);
    for (const term of FORBIDDEN) expect(all).not.toContain(term);
  });
});
