# V3-C: the rest of Career Agent in the V3 design (2026-10)

V3-A brought Find jobs and the job drawer to the V3 handoff, and V3-B made the
Resume helper a page of Career Agent. V3-C converges every other page. Nothing
here changes a score, a gate, Search Fit, eligibility, collection or refresh.

## What changed

- **Settings.** An internal nav with six sections, each with an icon, a name
  and a one-line status: What you're looking for, Job sites (all working / N
  need your attention, the same count as the sidebar), Smart matching (On /
  Off, read from the semantic settings), Look and language (the same theme and
  language controls as the sidebar), People on this computer (only when local
  profiles are on), Backup and privacy. The nav scrolls with the page.
- **Backup and privacy says what exists.** There is no backup button in the
  interface, so the section says so and explains that a copy of Career
  Agent's folder, made while it is closed, is a full backup. Nothing is
  simulated. The privacy list states only what is true: files stay here, no
  account, job sites are only read, text reaches an online AI only if one is
  chosen in Smart matching.
- **Home.** Two columns. Your next step is chosen from the counts alone
  (an interview, then new jobs not yet marked seen, then saved jobs, then
  browsing) and names no job it was not given. The lists show only the
  sections with something in them. Your progress is a vertical path; Small
  things to do lists a job site to fix and the answers still missing.
- **My applications.** A sentence on how to use the board, "N in progress"
  and "N interviews" chips, one next-step sentence per card (the only date it
  uses is the stored applied date), and "Get ready to apply" / "Get ready",
  which open that job's drawer on Before you apply / Practice. The board's
  columns and statuses are unchanged; there is one status system.
- **My profile.** A header card (the local profile's name, the latest
  confirmed role, where you live and whether you are open to remote work, three
  of your search phrases, Import my resume, Change what I'm looking for),
  segmented tabs (Overview, Experience, Skills, What I'm looking for) and an
  Overview with "Make your profile stronger": the kinds of fact still missing,
  each with where to add it. No percentage. "Edit my experience" lives on the
  Experience tab now that page headers are hidden.
- **Proof of my work.** All / Projects / Achievements / Certificates /
  Education, each with its count, shown only for kinds that hold something.
  Achievements stay a kind of their own: they are a different claim type and
  folding them into Projects would rewrite what the person said.
- **Setup.** The welcome card asks the language first (in both languages),
  the stepper shows five named stages over the cards, each question's reason
  sits in an info box, and a career-stage card asks where the person is in
  their career. That answer is stored in `candidate_state` and is context
  only: no scorer reads it (ADR-0020's rule, unchanged).

## "This ad may be a scam"

`web/caution.py` reads the WHOLE ad (a card's page carries only 400
characters, so the list asks for the page's full descriptions in one query)
and reports four signs. Each is an ASK the ad makes of the candidate (an
imperative opening a sentence or a list item, "you / applicants must",
"é necessário", "é obrigatório"), in English and Portuguese:

| Sign | What fires it |
|---|---|
| fee | being told to pay or deposit a fee, a taxa or a security deposit |
| bank | being told to send YOUR bank or card details (not for payroll or salary) |
| whatsapp | applying or making contact ONLY by WhatsApp or Telegram |
| equipment | being told to buy equipment or a kit from the employer |

A topic or a duty is not an ask: "no application fee", "we reimburse your
course fee", "pay vendors on time", "provide bank details for payroll" and
"customer support via WhatsApp" fire nothing; `tests/unit/test_caution.py`
pins those sentences and the scam ones beside them. The screen says "This ad
may be a scam", never that it is one, and lists the signs. Cards show "! Be
careful" instead of "New". The drawer shows the box above its tabs with Hide
this job (the ordinary hide, with Undo) and "I checked. It looks fine",
remembered per local profile and per posting on this computer; the posting
itself is not changed.

**Not built: "pay much higher than similar jobs".** It needs a comparable-
salary basis this product does not hold, and a comparison made up here would
be a claim about the market nobody measured. `career_agent.match` never imports
the module (a unit test walks its imports).
