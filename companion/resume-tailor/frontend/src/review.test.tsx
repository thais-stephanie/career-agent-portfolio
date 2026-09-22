// "Needs review" is never a dead end: a detail without a competing source offers
// Confirm / Edit before confirming / Leave for later with a plain-language reason;
// an extracted detail missing a fact asks for it before it is added. No internal
// vocabulary anywhere in the flow.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Experience } from "./screens/Experience";
import { Sources } from "./screens/Material";
import { AppProvider } from "./state";
import { STR } from "./labels";

const PENDING = {
  company: "Cobalt Peak Software", title: "Automation Specialist",
  text: "Ran onboarding sessions for new client admins on the workflow tooling.",
  status: "Needs review", sources: ["LinkedIn profile"], ref: "cp_training_001",
  review: { kind: "unconfirmed", why: "It was set aside from your resumes. Confirm it to make it usable, or leave it out.", includes_role: false },
};
const EXPERIENCE = {
  total: 2, confirmed: 0, supported: 1, needs_review: 1, conflicting: 0,
  items: [
    { company: "Initech", title: "Reporting", text: "Built reporting.", status: "Supported by sources", sources: ["Resume"] },
    PENDING,
  ],
};
const EXPERIENCE_AFTER = {
  total: 2, confirmed: 1, supported: 1, needs_review: 0, conflicting: 0,
  items: [EXPERIENCE.items[0], { ...PENDING, status: "Confirmed by you", ref: undefined, review: undefined, sources: ["LinkedIn profile", "Details you confirmed"] }],
};

function mockFetch(opts: { acceptError?: string } = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  let confirmed = false;
  let accepted = false;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const body = (data: unknown, ok = true, status = 200) => ({ ok, status, json: async () => data }) as Response;
    if (url === "/api/candidates") return body([{ id: "alex-1", name: "Alex Morgan", archived: false, base_resumes: 1, applications: 0 }]);
    if (url.includes("/experience/cp_training_001/confirm")) { confirmed = true; return body({ status: "Confirmed by you", topic: "Automation Specialist" }); }
    if (url.includes("/experience")) return body(confirmed ? EXPERIENCE_AFTER : EXPERIENCE);
    if (url.includes("/conflicts")) return body([]);
    if (url.includes("/decisions")) return body(confirmed ? [{ ref: "user_confirmation_001", topic: "Automation Specialist", value: PENDING.text, note: "", date: "2026-09-22", status: "Confirmed by you", versions: [] }] : []);
    if (url.includes("/details/0/accept")) {
      const sent = JSON.parse(String(init?.body));
      if (opts.acceptError && !sent.start) return body({ detail: opts.acceptError }, false, 400);
      accepted = true;
      return body({ added: "linkedin_profile-000", status: "Supported by sources" });
    }
    if (url.includes("/sources/linkedin_profile/details")) return body([
      { n: 0, text: "Mentored two junior automation specialists on client workflow builds.", company: "Cobalt Peak Software", title: "Automation Specialist", start: "", end: "2022-12", state: accepted ? "accepted" : "pending" },
    ]);
    if (url.includes("/sources")) return body([{ id: "linkedin_profile", name: "LinkedIn profile", kind: "LinkedIn profile", conflicts: 0, added: "2026-01-10", details: 1 }]);
    return body({});
  }));
  return calls;
}

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });

describe("details that need review", () => {
  it("say why, and can be confirmed as written; counts refresh at once", async () => {
    const calls = mockFetch();
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getByText(PENDING.text)).toBeInTheDocument());
    expect(screen.getByText(/1 needs review/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(STR.reviewDetail));
    expect(screen.getByText(PENDING.review.why)).toBeInTheDocument();
    expect(screen.getByText(STR.confirmDetail)).toBeInTheDocument();
    expect(screen.getByText(STR.editBeforeConfirm)).toBeInTheDocument();
    expect(screen.getByText(STR.leaveForLater)).toBeInTheDocument();
    fireEvent.click(screen.getByText(STR.confirmDetail));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/experience/cp_training_001/confirm"))).toBe(true));
    expect(JSON.parse(String(calls.find((c) => c.url.endsWith("/confirm"))!.init!.body))).toEqual({});
    await waitFor(() => expect(screen.getAllByText("Confirmed by you").length).toBeGreaterThan(0));
    expect(screen.queryByText(/needs review/)).toBeNull();          // header count refreshed
    expect(screen.getAllByText("Details you confirmed").length).toBeGreaterThan(0);
    expect(screen.getByText("Review this again")).toBeInTheDocument(); // reversible
    const html = document.body.innerHTML.toLowerCase();
    for (const term of ["override", "user_verified", "include_by_default", "evidence_id", "cp_training_001"]) expect(html).not.toContain(term);
  });

  it("can be corrected before confirming, with a note", async () => {
    const calls = mockFetch();
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getByText(PENDING.text)).toBeInTheDocument());
    fireEvent.click(screen.getByText(STR.reviewDetail));
    fireEvent.click(screen.getByText(STR.editBeforeConfirm));
    const input = screen.getByPlaceholderText("e.g. September 2026") as HTMLInputElement;
    expect(input.value).toBe(PENDING.text); // starts from the source wording
    fireEvent.change(input, { target: { value: "Ran onboarding sessions for client admins." } });
    fireEvent.change(screen.getByPlaceholderText("e.g. The resume was outdated."), { target: { value: "Shortened." } });
    fireEvent.click(screen.getByText("Confirm"));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/confirm"))).toBe(true));
    expect(JSON.parse(String(calls.find((c) => c.url.endsWith("/confirm"))!.init!.body)))
      .toEqual({ text: "Ran onboarding sessions for client admins.", note: "Shortened." });
  });

  it("leave for later keeps the detail waiting and sends nothing", async () => {
    const calls = mockFetch();
    render(<AppProvider><Experience /></AppProvider>);
    await waitFor(() => expect(screen.getByText(PENDING.text)).toBeInTheDocument());
    fireEvent.click(screen.getByText(STR.reviewDetail));
    fireEvent.click(screen.getByText(STR.leaveForLater));
    expect(screen.getByText(STR.reviewDetail)).toBeInTheDocument();
    expect(screen.getByText("Needs review", { selector: ".pill" })).toBeInTheDocument();
    expect(calls.some((c) => c.url.endsWith("/confirm"))).toBe(false);
  });
});

describe("extracted details waiting for review", () => {
  it("ask for the missing fact in plain words, then add the detail", async () => {
    const calls = mockFetch({ acceptError: "This detail needs a start month (like 2023-04) before it can be added." });
    render(<AppProvider><Sources /></AppProvider>);
    await waitFor(() => expect(screen.getByText("View details")).toBeInTheDocument());
    fireEvent.click(screen.getByText("View details"));
    await waitFor(() => expect(screen.getByText(STR.waitingForReview)).toBeInTheDocument());
    fireEvent.click(screen.getByText(STR.reviewDetail));
    expect((screen.getByLabelText("Company") as HTMLInputElement).value).toBe("Cobalt Peak Software");
    fireEvent.click(screen.getByText(STR.addToExperience));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("needs a start month"));
    fireEvent.change(screen.getByLabelText("Start month"), { target: { value: "2021-02" } });
    fireEvent.click(screen.getByText(STR.addToExperience));
    await waitFor(() => expect(screen.getByText("Added")).toBeInTheDocument());
    const sent = calls.filter((c) => c.url.endsWith("/accept")).map((c) => JSON.parse(String(c.init!.body)));
    expect(sent[sent.length - 1]).toMatchObject({ company: "Cobalt Peak Software", title: "Automation Specialist", start: "2021-02", end: "2022-12" });
  });
});
