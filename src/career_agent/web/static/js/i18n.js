/**
 * Two languages for the interface, and none for the jobs.
 *
 * That division is the whole design and it is not a simplification. What this
 * product renders comes from two entirely different places:
 *
 *   OURS      headings, buttons, the sentence explaining what Match means,
 *             the names of our own states. We wrote these, so we may write
 *             them twice.
 *
 *   THEIRS    a job title, a company name, a description, and above all an
 *             EVIDENCE QUOTE. ADR-0002 verifies every quote as a contiguous
 *             substring of the posting; a translated quote is not a substring
 *             of anything and is therefore not evidence. Employer text is
 *             never touched here, in either direction.
 *
 * A posting written in Portuguese stays in Portuguese for an English reader,
 * and one written in English stays in English for a Portuguese reader. The
 * demo corpus already contains both, which is what a real corpus looks like.
 *
 * **The locale is a UI preference and lives in the browser.** Not in
 * `search.local.yaml`: every section of that file is about how to READ and
 * RANK a posting, and which language a heading is in has nothing to do with
 * either. Storing it there would also mean a locale change bumped
 * `config_version` and invalidated every score in the corpus, which is an
 * absurd consequence and a good sign the fact is in the wrong place.
 *
 * **Nothing here reaches the matcher or the database.** Canonical enums stay
 * English internally -- `VERIFIED_NOT_ELIGIBLE` is a stored value and a query
 * parameter, and only its LABEL is translated. A test asserts that no
 * translated string is ever a persisted value.
 */

/** Where the choice is remembered. Per browser, per person, never sent anywhere. */
export const LOCALE_KEY = 'careerAgent.locale.v1';

/** The locales this interface has catalogues for. English is the fallback. */
export const LOCALES = ['en', 'pt-BR'];
export const DEFAULT_LOCALE = 'en';

/**
 * The English catalogue, which is also the KEY LIST.
 *
 * A key absent here is a bug rather than a missing translation: `t()` falls
 * back to English, so English is the one catalogue that has to be complete.
 * A test asserts every other catalogue is a subset of this one, so a typo in a
 * translated key fails the build instead of rendering a raw identifier at
 * somebody.
 */
const EN = {
  "tailor.groupLabel": "Resume Tailor",
  "tailor.note": "Copy this posting into Tailor resume. Review evidence separately in Resume Tailor Beta.",
  "tailor.copy": "Copy job description",
  "tailor.open": "Open Resume Tailor Beta",
  "tailor.copied": "Copied. Open Resume Tailor Beta and paste into Tailor resume.",
  "tailor.unavailable": "Clipboard unavailable. Select and copy the description below.",

  "firstrun.searchSave": "Save my search phrases",
  "firstrun.workLabel": "Work you want to do, one short phrase per line",
  "firstrun.skillsLabel": "Tools or skills to look for, one per line (optional)",
  "firstrun.searchHelp": "These phrases describe work you want to find in job descriptions. They do "
    + "not confirm experience. Save here, then edit them in Settings. Existing jobs "
    + "need recalculation after a change.",
  "maintenance.title": "Source refresh status",
  "maintenance.unavailable": "Refresh status could not be read. Reopen Settings to try again; your saved "
    + "jobs remain available.",
  "maintenance.running": "A refresh is running.",
  "maintenance.idle": "No refresh is running in this app or the bounded maintenance command.",
  "maintenance.last": "Last successful source check: {date}.",
  "maintenance.counts": "{fresh} fresh items; {pending} eligible pending items; {total} items in the inventory.",
  "maintenance.interrupted": "The previous refresh has no completion record. Its unfinished work remains "
    + "pending; completed jobs are kept.",
  "maintenance.manual": "{n} require a manual run by you.",
  "maintenance.cooldown": "{n} are waiting for a provider cooldown.",
  "maintenance.refusal": "{n} are waiting after a provider refused access.",
  "maintenance.separate": "{n} need a separate supported collection path.",
  "maintenance.larger": "{n} need a larger time budget.",
  "maintenance.later": "{n} are deferred to a later bounded session.",
  "maintenance.explanation": "Counts refer to boards or feed windows, not jobs. Deferrals describe a "
    + "30-minute plan, not failures. A recent check is not complete market "
    + "coverage. You can browse and track existing jobs while work remains pending. "
    + "Per-source buttons below use their existing collection limits.",
  'career.noSourceQuote': 'No original source quote was recorded for this statement.',
  'career.importedStatement': 'Imported statement',
  'career.revisions': 'View earlier revisions',
  'career.revision': 'Revision {number}',
  'career.itemMeta': '{category} · {state}',
  'settings.market.br': 'Brazil',
  'settings.market.eu': 'Europe',
  'settings.market.latam': 'Latin America',
  'settings.market.global': 'Worldwide',
  'career.aliasScope': 'Company labels under review: {first} and {second}. A merge uses {second} for display.',
  'career.destinationScope': 'Destination: {destination}',
  'career.evidenceText': 'Evidence statement',
  'career.textEditHelp': 'Saving creates a new revision. The original source and earlier wording remain available.',
  'career.editText': 'Edit statement',
  'career.saveText': 'Save revision',
  'career.editImportHelp': 'To correct an unconfirmed import, open its source review below.',
  'settings.refreshTiming': 'Refresh preference',
  'settings.refreshMode.AUTO': 'Follow my target markets',
  'settings.refreshMode.ENABLED': 'Enable refresh for this market',
  'settings.refreshMode.PAUSED': 'Pause refresh',
  'settings.sourcePausedByYou': 'Refresh paused by you. Collected jobs remain available.',
  'settings.refreshAnyway': 'Refresh anyway',
  'settings.refreshNow': 'Refresh now',
  'settings.targets': 'Target locations, work arrangements and compensation',
  'settings.searchHelp': 'What would you like to see more or less of?',
  'settings.prefer_keyword': 'Prioritize',
  'settings.prefer_keywordHelp': 'Move jobs containing these phrases higher in your Discover ordering.',
  'settings.avoid_keyword': 'Avoid',
  'settings.avoid_keywordHelp': 'Move jobs containing these phrases lower without hiding them.',
  'settings.exclude_keyword': 'Never show',
  'settings.exclude_keywordHelp': 'Exclude jobs containing these phrases from your Discover view.',
  'settings.addPhrase': 'Type a phrase, then Enter or comma',
  'settings.clearPhrases': 'Clear phrases',
  'settings.localPreference': 'Saved in this browser. Matching ignores letter case. '
    + 'These lists do not change Search Fit.',
  'settings.model': 'Your search model',
  'settings.modelAdvanced': 'Review concepts and advanced scoring vocabulary',
  'settings.advancedRefresh': 'Advanced refresh diagnostics',
  'settings.sourceHelp': 'Refresh timing follows your target markets. It never filters what a source collects. '
    + 'Jobs already collected remain available in Discover.',
  'settings.sourceDetails': 'Technical source details',
  'settings.sourceMarket': 'Market: {market}',
  'settings.sourcePaused': 'Paused because this market is outside your current target markets.',
  'settings.sourceFailure': 'The last refresh failed. Previously collected jobs remain available in Discover.',
  'settings.sourceUpdated': 'Last successful refresh: {date}',
  'career.heading': 'Your experiences',
  'career.guidance': 'Describe what you owned, built or changed; the tools, people and scope involved; ' +
    'decisions you made and outcomes you observed. Include measurements when you know ' +
    'them. A metric is not required.',
  'career.example': 'For example: “Organized the weekly handover between shifts” or “Built a shared ' +
    'checklist that helped new colleagues handle requests.” Keep each statement ' +
    'separate and accurate.',
  'career.boundary': 'Experiences organize what you have done. Only evidence you confirm can support ' +
    'Evidence / Readiness and resume preparation. Search Fit uses the posting and ' +
    'your search model.',
  'career.new': 'Add experience',
  'career.inbox': 'Needs organizing',
  'career.inboxCount': 'Needs organizing ({count})',
  'career.inboxHelp': 'Evidence with no reviewed experience belongs here. Missing companies, dates and ' +
    'roles remain unknown until you supply them. Select related statements to assign ' +
    'them together.',
  'career.allEvidence': 'Find evidence across experiences',
  'career.roleUnknown': 'Role needs review',
  'career.dateUnknown': 'Not stated',
  'career.independent': 'Independent experience',
  'career.current': 'Present',
  'career.count': '{count} evidence items · {confirmed} confirmed',
  'career.open': 'Explore evidence',
  'career.edit': 'Edit experience',
  'career.history': 'Organization history',
  'career.undo': 'Review undo',
  'career.proposals': 'Review imported groups and possible duplicates ({count} groups)',
  'career.proposalHelp': 'These are suggestions from imported names and dates, not established roles. ' +
    'Similar names never establish that two companies are the same. Review each ' +
    'association before applying it.',
  'career.possibleDuplicate': 'Possible duplicate company',
  'career.merged': 'You merged these company labels. Experience roles remain separate.',
  'career.separate': 'You chose to keep these companies separate.',
  'career.proposalCount': '{count} evidence items to organize',
  'career.ambiguity': 'Review the role and dates: information is missing, overlaps or disagrees. No ' +
    'role decision has been made for you.',
  'career.reviewGroup': 'Review this group',
  'career.company': 'Company or organization',
  'career.title': 'Role or project title',
  'career.period_start': 'Start month',
  'career.period_end': 'End month',
  'career.display_order': 'Display order',
  'career.current_role': 'I currently do this work',
  'career.kind': 'Kind of experience',
  'career.metadataHelp': 'This changes how evidence is organized. Original statements, imported company ' +
    'names, dates and source records remain intact. The next step shows all affected ' +
    'evidence.',
  'career.preview': 'Review changes',
  'career.cancel': 'Cancel',
  'career.more': 'Show more',
  'career.previous': 'Previous page',
  'career.apply': 'Apply reviewed changes',
  'career.scope': 'This action affects {count} evidence items.',
  'career.preserve': 'Original source text and evidence history are preserved. Organizing evidence ' +
    'does not confirm it.',
  'career.reviewed': 'I have read the selected statements and confirm they accurately describe my experience.',
  'career.selected': '{count} selected',
  'career.anyState': 'Any review state',
  'career.anyCategory': 'All categories',
  'career.source': 'Imported / source evidence',
  'career.find': 'Words in your evidence',
  'career.filter': 'Find',
  'career.state': 'Review state',
  'career.category': 'Evidence category',
  'career.selectFiltered': 'Select all {count} filtered items',
  'career.selectExperience': 'Select all in this experience',
  'career.clear': 'Clear selection',
  'career.organizeSelected': 'Organize selected evidence',
  'career.destination': 'Destination experience',
  'career.page': '{start} to {end} of {total} evidence items',
  'career.importReview': 'Review imported sources and disagreements',
  'career.evidenceEditor': 'Edit individual evidence and inspect revisions',
  'career.action.create': 'Create experience',
  'career.action.edit': 'Edit experience metadata',
  'career.action.move': 'Move selected evidence',
  'career.action.merge_experiences': 'Merge this experience into the destination',
  'career.action.split': 'Split selection into a new experience',
  'career.action.merge_companies': 'Merge company labels',
  'career.action.keep_separate': 'Keep separate',
  'career.action.category': 'Change selected category',
  'career.action.confirm': 'Review and confirm selected',
  'career.action.retire': 'Retire / reject selected',
  'career.action.undo': 'Undo organization change',
  'career.kind.EMPLOYMENT': 'Employment',
  'career.kind.VOLUNTEER': 'Volunteering',
  'career.kind.FREELANCE': 'Freelance work',
  'career.kind.ACADEMIC': 'Academic project',
  'career.kind.PERSONAL': 'Personal project',
  'career.category.ACHIEVEMENT': 'Achievements',
  'career.category.RESPONSIBILITY': 'Responsibilities',
  'career.category.PROJECT': 'Projects',
  'career.category.TOOL': 'Tools & systems',
  'career.category.SKILL': 'Skills / supporting statements',
  'career.category.CERTIFICATION': 'Certifications',
  'career.category.EDUCATION': 'Education',
  'career.category.OTHER': 'Other evidence',
  'career.state.CONFIRMED': 'Confirmed',
  'career.state.PENDING': 'Needs review',
  'career.state.RETIRED': 'Retired',
  'career.state.REJECTED': 'Rejected',
  "review.title": "Review search model origins",
  "review.help": "Existing values are preserved. Similarity to the legacy example suggests inheritance; "
    + "it is not proof of your choice. Keep records your review. Edit or Remove shows a "
    + "preview before saving. Empty phrase groups stop recognizing that concept; dependent "
    + "screening rules remain visible in this review.",
  "review.legacy": "Legacy example value (comparison only)",
  "review.editValue": "Edit {path}",
  "review.confirm": "Confirm review",
  "review.kept": "Review saved. Search values and scores are unchanged.",
  "review.keepCost": "Confirm that you reviewed and kept this value. No recalculation is needed.",
  "review.keep": "Keep",
  "review.edit": "Edit / preview",
  "review.remove": "Remove / preview",
  "review.reviewed_by_user": "Reviewed by you",
  "review.changed_locally": "Changed locally; intent unconfirmed",
  "review.inherited_legacy_example": "Same as legacy example",
  "review.neutral_product_policy": "Neutral product policy",
  "review.unknown_provenance": "Unknown origin",

  // -- the shell ---------------------------------------------------------
  'app.tagline': 'runs on your computer',
  'app.skip': 'Skip to results',
  'view.cards': 'Cards',
  'view.table': 'Table',
  'view.board': 'Board',
  'view.group': 'How to show results',
  'order.group': 'Order and grouping',
  'order.oneRow': 'One row per role',
  'order.by': 'Order results by',
  'sort.score': 'Best search fit',
  'sort.confidence': 'Most complete posting',
  'sort.posted': 'Newest',
  'sort.company': 'Company name',
  'sort.title': 'Job title',
  'sort.status': 'Where I am with it',
  'direction.desc': 'Highest first',
  'direction.asc': 'Lowest first',

  // -- the rail ----------------------------------------------------------
  'rail.filters': 'Filters',
  'rail.retrieve': 'Retrieve jobs',
  'rail.profile': 'Your career profile',
  'rail.preferences': 'Search preferences',
  'rail.sources': 'Where these come from',

  // -- the legend, which is the thing people most often get wrong --------
  'legend.summary': 'What Match and Posting detail mean',
  'legend.match': 'Search Fit',
  'legend.matchBody': 'posting alignment with your search. It does not measure your capability.',
  'legend.detail': 'Posting detail',
  'legend.detailBody':
    'how much the posting actually told us. Not your chances of getting hired.',
  'legend.eligible': 'Can you take it',
  'legend.eligibleBody':
    'a separate answer, kept separate. It is never mixed into the other two.',

  // -- the two narrowings, which are two sentences and not one -----------
  'hidden.eligibility':
    '{count} hidden, because each one states a requirement you do not meet: a '
    + 'country, a work permit, a security clearance or something similar.',
  'hidden.eligibilityShowing':
    'Showing jobs that state a requirement you do not meet, alongside the rest.',
  'hidden.eligibilityReveal': 'Show them too',
  'filters.toggle.includeExcludedSeniority': 'Include levels you set aside',
  'filters.toggle.includeExcludedWorkModel': 'Include ways of working you set aside',
  'filters.includeExcludedWorkModelHelp':
    'Off by default, and it does nothing until you say a way of working should never be shown. '
    + 'It is a preference, not a verdict: nothing is deleted and this brings them back.',
  'hidden.workModel': '{count} set aside because of a way of working you said never to show.',
  'hidden.workModelOne': '1 set aside because of a way of working you said never to show.',
  'hidden.workModelShowing': 'Showing ways of working you set aside.',
  'hidden.workModelReveal': 'Show those too',
  'filters.includeExcludedSeniorityHelp':
    'Off by default, and it does nothing until you name a level in your profile. These are ' +
    'roles at a level you said you do not want to see, such as Staff or Director. Nothing is ' +
    'deleted and this brings them back.',
  'hidden.seniority': '{count} set aside because they are at a level you said you do not want to see.',
  'hidden.seniorityOne': '1 set aside because it is at a level you said you do not want to see.',
  'hidden.seniorityShowing': 'Showing levels you set aside.',
  'hidden.seniorityReveal': 'Show those too',
  'hidden.unresolved':
    '{count} set aside because the posting never said where the employer hires. Nothing rules you ' +
    'out; nothing confirms you either.',
  'hidden.unresolvedOne': '1 set aside because the posting never said where the employer hires.',
  'hidden.unresolvedShowing': 'Showing jobs where the posting never said where the employer hires.',
  'hidden.unresolvedReveal': 'Show those too',
  'hidden.offTarget':
    '{count} hidden as a different kind of work from the one you described. '
    + 'Nothing is wrong with them: they did not match what you said you are '
    + 'looking for.',
  'hidden.offTargetShowing':
    'Showing work you set aside as a different kind, alongside the rest.',
  'hidden.offTargetReveal': 'Show those too',
  'hidden.hideAgain': 'Hide them again',

  // -- the career profile ------------------------------------------------
  'profile.editUnder': 'Edit these under "Search preferences".',
  'profile.andMore': ' ... and {count} more',
  'profile.empty': 'Start on Home to describe the work you want, then edit your preferences here.',
  'profile.conflictOne':
    'Your candidate profile and your search settings disagree about one thing.',
  'profile.conflictMany':
    'Your candidate profile and your search settings disagree about {count} things.',

  // -- states, whose STORED values stay English --------------------------
  // The exact words `eligibilityWords` already used, moved here rather than
  // rewritten. One vocabulary across the card, the table and the filter rail
  // was a correction somebody made once; changing it while adding a language
  // would undo that quietly.
  'eligibility.VERIFIED_ELIGIBLE': 'Nothing in the way',
  'eligibility.LIKELY_ELIGIBLE': 'Probably nothing in the way',
  'eligibility.UNRESOLVED': 'Did not say',
  'eligibility.VERIFIED_NOT_ELIGIBLE': 'Rules you out',
  'band.STRONG': 'Strong',
  'band.GOOD': 'Good',
  'band.MODERATE': 'Moderate',
  'band.WEAK': 'Weak',
  // -- how the work is done, and how the worker is engaged ---------------
  //
  // Stored values a reader chooses between in the profile form. They were
  // falling through to a lower-cased fallback, so "remote" and "hybrid" sat
  // beside "Intern" and "Senior" in the same column, in different cases.
  'work_model.REMOTE': 'Remote',
  'work_model.HYBRID': 'Hybrid',
  'work_model.ONSITE': 'On site',
  'contract.FULL_TIME_EMPLOYEE': 'Employee',
  'contract.CONTRACTOR_B2B': 'Contractor',
  'contract.EOR': 'Employer of Record',

  // `CLT` and `PJ` need no catalogue entry in either language, and that is the
  // decision rather than an omission. Both are Brazilian labour statutes --
  // the Consolidacao das Leis do Trabalho, and pessoa juridica -- so an
  // English gloss would name no law that exists. `isAcronym` passes them
  // through unchanged, which is what every Brazilian job advert does, and
  // `NOT_STATED` already resolves through the `period` family.

  'seniority.INTERN': 'Intern',
  'seniority.JUNIOR': 'Junior',
  'seniority.MID': 'Mid-level',
  'seniority.SENIOR': 'Senior',
  'seniority.STAFF': 'Staff',
  'seniority.PRINCIPAL': 'Principal',
  'seniority.LEAD': 'Lead',
  // The ten `ApplicationStatus` members, and exactly those. A test compares
  // this list against the enum in both directions, which is how three
  // invented statuses -- INTERESTED, INTERVIEWING, CLOSED -- were caught
  // before they reached a dropdown nobody could use.
  'status.DISCOVERED': 'Found',
  // NOT 'Shortlisted'. That is a recruiter's word for what a hiring side
  // does to candidates, and this status is a person saying she likes a job.
  // The board and the dropdown both read it from here.
  'status.SHORTLISTED': 'Interested',
  'status.TO_APPLY': 'To apply',
  'status.APPLIED': 'Applied',
  'status.INTERVIEW': 'Interviewing',
  'status.OFFER': 'Offer',
  'status.HIRED': 'Hired',
  'status.REJECTED': 'Rejected',
  'status.WITHDRAWN': 'Withdrawn',
  'status.ARCHIVED': 'Archived',

  // -- the things a posting did not say ----------------------------------
  'absent.level': 'Level not stated',
  'absent.contract': 'Contract type not stated',
  'absent.salary': 'Salary not stated',
  'absent.generic': 'Not stated',
  'absent.levelSentence': 'The posting does not state a level. Treating it as mid-level.',

  // -- how the work is engaged, and where the employment sits -----------
  //
  // Two vocabularies that must never blur into each other on screen. The
  // employer that WROTE `contratacao CLT` said so; the employer that offered
  // `plano de saude` offered a benefit that in Brazil usually accompanies
  // employment. Both are useful, only one is a statement, and the words
  // below are the whole of that distinction as a reader meets it.
  'employment.stated': 'The posting states this',
  'employment.likely': 'Likely, from the benefits offered',
  'employment.EMPLOYEE': 'Employee',
  'employment.CONTRACTOR_B2B': 'Contractor',
  'employment.EOR': 'Through an Employer of Record',
  'employment.INTERN': 'Internship',
  'employment.APPRENTICE': 'Apprenticeship',
  'employment.TEMPORARY': 'Temporary',
  'employment.OTHER': 'Other arrangement',
  'employment.UNRESOLVED': 'Engagement not stated',
  'regime.CLT': 'CLT',
  'regime.PJ': 'PJ',
  'regime.UNRESOLVED': 'No local regime stated',

  // Deliberately not a sentence about the reader. "Likely US domestic
  // employment" describes the POSTING; "you are not eligible" would describe
  // her, and the posting has not said that. Only `eligibility` may say that.
  'domestic.LIKELY_US_DOMESTIC': 'Likely a United States domestic job',
  'domestic.sentence':
    'The posting offers {signal} and does not mention hiring outside the United '
    + 'States. That usually means the job is set up as United States employment. '
    + 'It is not a refusal, and the employer has not been asked.',
  'domestic.INTERNATIONAL_STATED': 'Mentions hiring beyond one country',

  // -- empty states ------------------------------------------------------
  'empty.none': 'No jobs match these filters.',
  'empty.loading': 'Loading',
  'empty.broken': 'Cannot tell how this is doing right now.',

  // -- the locale control itself -----------------------------------------
  // The toggle is two letters each, which is what a compact `EN | PT` control
  // is. The INTERNAL value stays `pt-BR`, because that is the language tag
  // `<html lang>` needs and the one a screen reader reads.
  'locale.label': 'Language',
  'locale.en': 'EN',
  'locale.pt-BR': 'PT',

  // -- the third drawer tab: what they ask for, beside what you have -----
  //
  // The four state names are OURS, so they are translated. The stored values
  // (`MATCHED`, `GAP`) never move: they are what the API sends and what the
  // tests assert on, and only the word a person reads changes.
  'drawer.tabs.label': 'Job details, the reasoning behind the score, and preparing to apply',
  'drawer.tab.details': 'Job details',
  'drawer.tab.why': 'Why this fits your search',
  'drawer.tab.prepare': 'Prepare to apply',
  'prep.requirements': 'What they ask for',
  'prep.retry': 'Try again',
  'prep.noRequirements': 'This posting fired none of the signals you configured, so there is '
    + 'nothing here to prepare against. That is a fact about the posting, not about you.',
  'prep.leadWithEvidence': 'Evidence / Readiness: checked against {n} confirmed claims. '
    + 'Phrase overlap links the quotes; review whether they support the requirement. '
    + 'These claims do not enter the Search Fit number.',
  'prep.leadWithoutEvidence': 'Evidence / Readiness is unknown: no career claims are confirmed. '
    + 'This does not change the separate Search Fit number.',
  'prep.noEvidenceYet': 'Import your CV or write down what you have done, and this page '
    + 'starts answering.',
  'prep.goToEvidence': 'Your career evidence',
  'prep.state.MATCHED': 'Related confirmed evidence',
  'prep.state.PARTIAL': 'Tool or skill named',
  'prep.state.GAP': 'No confirmed support recognized',
  'prep.state.UNRESOLVED': 'This cannot be told',
  // The server defines these four meanings too, in `web/workspace_api.py`, and
  // that definition is the one the docs and the tests hold to. These are the
  // same sentences for a reader who chose another language; the client prefers
  // its own and falls back to the server's, so a key added there and not here
  // still shows something true.
  'prep.meaning.MATCHED': 'A recognized phrase links this requirement to confirmed evidence. '
    + 'Review the quotes to decide whether the evidence supports the work.',
  'prep.meaning.PARTIAL': 'You have named this -- a tool or a skill -- without confirmed '
    + 'evidence of having done the work. Having used something and having done the job are '
    + 'different answers.',
  'prep.meaning.GAP': 'No confirmed supporting evidence was recognized for this requirement. '
    + 'This does not establish that you lack the experience.',
  'prep.meaning.UNRESOLVED': 'This system cannot tell. The requirement fired on a signal with '
    + 'no phrases to compare a claim against, so no verdict here would be honest.',
  'prep.theySay': 'They say',
  'prep.youSay': 'You say',
  'prep.matchedOn': 'Matched on the phrase "{phrase}".',
  'prep.fromDocument': 'The line this was read from: {line}',
  // The sentences the product itself writes about a posting. `{signal}` and
  // `{relationship}` are stored enum values and signal ids; they are never
  // translated, because they are what the database holds and the tests assert.
  'prep.concernText.geography_unresolved': 'The posting does not state where it hires. '
    + 'Worth asking first.',
  'prep.concernText.likely_us_domestic': 'Probably United States employment: it offers '
    + '{signal} and does not mention hiring elsewhere. This is context, not a refusal.',
  'prep.concernText.contract_explicit': 'The posting STATES this is {relationship}.',
  'prep.concernText.contract_suggested': 'The posting suggests this is {relationship}, from '
    + 'what it offers rather than from what it says. A benefit is not a statement.',
  'prep.concernText.seniority_unstated': 'The posting does not state a level. Worth asking '
    + 'what they mean.',
  'prep.concerns': 'To settle before applying',
  'prep.concernsLede': 'Things that are not about whether you can do the work.',
  'prep.concern.eligibility': 'Can you take it',
  'prep.concern.employment': 'Employment',
  'prep.concern.contract': 'Contract',
  'prep.concern.seniority': 'Level',
  'prep.concern.fit': 'Kind of work',
  'prep.notRight': 'Not right? Say so',
  'prep.reviewLede': 'This records what you think. It changes no score, rewrites no quote '
    + 'and creates no claim about you.',
  'prep.verdict.SUPPORTS': 'This does support it',
  'prep.verdict.PARTIALLY_SUPPORTS': 'Only partly',
  'prep.verdict.DOES_NOT_SUPPORT': 'This does not support it',
  'prep.verdict.EVIDENCE_MISSING': 'I have done this, it is not in my profile',
  'prep.withdraw': 'Withdraw this answer',
  'prep.notePlaceholder': 'A note to yourself. Optional.',
  'prep.noteLabel': 'Your note about {requirement}',
  'prep.missingLede': 'Nothing was claimed by saying that. Write it down in your own words '
    + 'and it becomes evidence you can draw on.',
  'prep.checklist': 'Before you apply',
  'prep.checklistLede': 'What has happened so far. Not a score, and not a list you have to '
    + 'finish.',
  'prep.check.eligibility': 'Eligibility reviewed ({n} still to settle)',
  'prep.check.requirements': 'Requirements read ({n} of them)',
  'prep.check.evidence': 'Evidence available ({n} confirmed)',
  'prep.check.gaps': 'Gaps understood ({n} unanswered)',
  'ledger.source.RESUME': 'From your CV',
  'ledger.source.LINKEDIN': 'From LinkedIn',
  'ledger.source.SELF_ATTESTED': 'You wrote this',
  'ledger.source.DOCUMENT': 'From a document',

  // -- the evidence overlay: what is true about you, and how it got there -
  'rail.evidence': 'Your career evidence',
  'ledger.title': 'Your career evidence',
  'ledger.close': 'Close',
  'ledger.retry': 'Try again',
  'ledger.heading': 'Your evidence',
  'ledger.lede': 'What Career Agent knows you have actually done. What you are LOOKING FOR is a '
    + 'separate thing and lives on your career profile.',
  'ledger.empty': 'Nothing is confirmed about you yet. Import a CV above, or write down '
    + 'something you have done.',

  // WHERE A CONFIRMED FACT REACHES, AND WHERE IT DOES NOT.
  //
  // Confirming three hundred statements is an hour of somebody's evening, and
  // the reasonable expectation afterwards is that the recommendations move.
  // They do not: `career_agent.match` reads a `VerifiedClaim` in exactly two
  // modules, `preparation.py` and `resume.py`, and neither of them scores
  // anything. `tests/integration/test_evidence_reach.py` asserts that, and it
  // is what these three lines are allowed to say.
  //
  // Saying so BEFORE the hour is spent is the whole point. A screen that lets
  // somebody believe otherwise is not neutral about it.
  'ledger.uses.heading': 'How does Career Agent use my evidence?',
  'ledger.uses.prepare': 'Preparing to apply. Every requirement on a posting is answered '
    + 'from what you have confirmed, and from nothing else.',
  'ledger.uses.notScore': 'Not the recommendations. A posting is scored from what the '
    + 'employer wrote about the work; nothing about you is read while scoring it.',
  'ledger.uses.notEligibility': 'Not eligibility. That is where an employer says it may '
    + 'hire, checked against the countries and scopes in your preferences.',
  'ledger.uses.where': 'What moves the recommendations is what you are looking for, under '
    + 'Preferences on your career profile.',
  'ledger.search': 'Search your evidence',
  'ledger.searchPlaceholder': 'Search experience, skills, companies, tools...',
  'ledger.showAllInGroup': 'Show all {n}',
  // SELECTION, and the one action it offers. Retiring is reversible and
  // creates nothing; there is no bulk confirm, because nothing becomes true
  // here without a person saying so one claim at a time.
  'ledger.selectStart': 'Select multiple',
  'ledger.selectDone': 'Done',
  'ledger.selectGroup': 'Select all shown',
  'ledger.selectClear': 'Clear selection',
  'ledger.selectOne': 'Select "{text}"',
  'ledger.selectedCount': '{n} selected',
  'ledger.bulkPartly': '{done} set aside. {failed} could not be -- open those and try again.',
  'ledger.bulkRetire': 'Remove from profile',
  'ledger.bulkRetireConfirm':
    'Set aside {n} statements? They keep their history and you can stand behind '
    + 'them again at any time.',

  // WHAT A GOOD ONE LOOKS LIKE. A hint that names what to include, and a
  // placeholder that shows one. Neither is ever written into the field.
  //
  // SEVEN EXAMPLES FROM SEVEN DIFFERENT KINDS OF WORK, and that spread is the
  // point rather than variety for its own sake. They used to be one trade --
  // HubSpot, Deel, billing reconciliation, SQL, a Workato certificate, a
  // computer science degree -- so a nurse writing the first thing she has ever
  // confirmed about herself was shown the owner's job as the model answer.
  // That is a default encoding one person's career, in the place where it
  // does the most damage: the blank box somebody is looking at when they do
  // not yet know what this product wants.
  //
  // What a placeholder has to teach is the SHAPE -- what you did, what you
  // used, what changed -- and a shape survives being drawn from any field.
  'ledger.hint.EMPLOYMENT':
    'Say what you actually did. The task, the tools, and the outcome where you know it.',
  'ledger.example.EMPLOYMENT':
    'Ran the weekly rota for a team of twelve, and cut last-minute swaps by half.',
  'ledger.hint.PROJECT': 'What the project was, what you built, and what changed because of it.',
  'ledger.example.PROJECT':
    'Rebuilt the way we tracked stock across two shops, so nothing was counted twice.',
  'ledger.hint.ACHIEVEMENT': 'What happened, and how you know it happened.',
  'ledger.example.ACHIEVEMENT': 'Cut invoice errors from 40 a month to under 5.',
  'ledger.hint.SKILL': 'One skill or tool you have actually used. Keep it short.',
  'ledger.example.SKILL': 'Scheduling',
  'ledger.hint.TOOL': 'The name of the tool, as the people who use it write it.',
  'ledger.example.TOOL': 'Excel',
  'ledger.hint.CERTIFICATION': 'The official name, and who issued it.',
  'ledger.example.CERTIFICATION': 'Project Management Professional -- PMI',
  'ledger.hint.EDUCATION': 'The qualification and the institution.',
  'ledger.example.EDUCATION': 'BA Communications -- University of Sao Paulo',
  'ledger.noMatch': 'Nothing here matches that.',
  'ledger.origin': 'Original text',
  'ledger.edit': 'Edit',
  'ledger.editLabel': 'Your corrected wording',
  'ledger.save': 'Save the correction',
  'ledger.retire': 'Remove from profile',
  'ledger.retireConfirm': 'Remove this from your profile?\n\n{text}\n\nIt is kept with its history, and it '
    + 'stops being drawn on when an application is prepared. You can put it back.',
  'ledger.confirm': 'Use this again',
  'ledger.retiredTag': 'Set aside',
  'ledger.draftTag': 'Not confirmed yet',
  'ledger.confirmDraft': 'Confirm',
  'ledger.revision': 'revision {n}',
  'ledger.addHeading': 'Add to your profile',
  // THE LINE THAT MATTERS MOST HERE. Somebody adding their own work should
  // not be wondering what shape this product expects. It expects a sentence.
  'ledger.addLede': 'You do not have to write this in any particular way -- put it the way '
    + 'you would explain it to someone. It is recorded as your own words, and nothing is '
    + 'cited, because there is no document behind it.',
  'ledger.addText': 'Tell us about it',
  'ledger.addType': 'What are you adding?',
  'ledger.addSubmit': 'Confirm this about me',
  'ledger.addEmpty': 'Write something first.',

  // -- reading a CV ------------------------------------------------------
  'cv.heading': 'Read your CV',
  'cv.privacy': 'Your CV is read by Career Agent on this computer. It is not uploaded '
    + 'anywhere, no model of any kind sees it, and the file itself is never stored.',
  'cv.choose': 'Choose a file',
  // The native control's own "no file chosen", which it draws in the
  // OPERATING SYSTEM'S language. Ours, so it is in the reader's.
  'cv.noFile': 'No file chosen yet',
  'cv.supported': 'Reads {kinds}',
  'cv.nothingConfirmed': 'Nothing is confirmed by being read. Every line becomes a proposal '
    + 'waiting for your answer.',
  'cv.reading': 'Reading {name} on this computer...',
  'cv.pendingCount': '{n} still to answer',
  'cv.reviewed': 'All answered',
  'cv.importMeta': '{confirmed} confirmed, {rejected} refused, {total} read',
  'cv.continueReview': 'Continue reviewing',
  'cv.reopenReview': 'Look at it again',
  'cv.backToEvidence': 'Back to your evidence',
  'cv.reviewSafety': 'Each answer is saved on its own, straight away. You can close this and '
    + 'come back to the rest.',
  'cv.fromCv': 'From your CV',
  'cv.carriesFigure': 'This carries a figure. Check it says what you remember saying -- the '
    + 'number is never taken out of the sentence.',
  'cv.accept': 'Yes, that is true',
  'cv.acceptLabel': 'Confirm: {text}',
  'cv.edit': 'Not quite -- reword it',
  'cv.editLabel': 'Your corrected wording',
  'cv.saveEdit': 'Confirm my wording',
  'cv.reject': 'No, drop it',
  'cv.rejectLabel': 'Drop: {text}',
  'cv.decision.ACCEPTED': 'Confirmed',
  'cv.decision.EDITED': 'Confirmed, your wording',
  'cv.decision.REJECTED': 'Dropped',
  'cv.decision.PENDING': 'Not answered',
  // -- Career Evidence V2: a CV read as experiences ---------------------
  'cv.archivedState': 'Archived',
  'cv.importMetaJobs': '{experiences} experiences, {confirmed} confirmed, {rejected} rejected, {total} read',
  'cv.inspect': 'Look inside',
  'cv.archivedFlash': '{name} is archived. Nothing in it waits for you now; Restore brings it back exactly as it was.',
  'lifecycle.delete': 'Delete...',
  'lifecycle.deleted': '{name} was deleted.',
  'cvr.heading': 'What {name} says, by experience',
  'cvr.back': 'Back to your evidence',
  'cvr.found': '{experiences} experiences found / {suggestions} suggestions / {attention} need attention',
  'cvr.waiting': '{waiting} still to answer, {confirmed} confirmed so far.',
  'cvr.allAnsweredLede': 'Everything is answered: {confirmed} confirmed, {rejected} rejected.',
  'cvr.archivedLede': 'This read is archived. Nothing in it waits for you and nothing in it can be '
    + 'answered until you restore it.',
  'cvr.oneAtATime': 'Nothing is confirmed until you confirm it, one statement at a time. Each '
    + 'answer is saved straight away.',
  'cvr.experiences': 'Experiences, newest first',
  'cvr.otherSections': 'Everything else',
  'cvr.rowCounts': '{waiting} waiting / {confirmed} confirmed',
  'cvr.attentionChip': '{n} to check',
  'cvr.review': 'Review',
  'cvr.reviewLabel': 'Review {company}, {role}',
  'cvr.reviewSectionLabel': 'Review {name}',
  'cvr.next': 'Next item needing review',
  'cvr.allAnswered': 'Nothing else is waiting in this read.',
  'cvr.addExperience': 'Add a missing experience',
  'cvr.archive': 'Archive',
  'cvr.archived': 'Archived. Nothing in this read waits for you now.',
  'cvr.restore': 'Restore',
  'cvr.restored': 'Restored, exactly as it was.',
  'cvr.delete': 'Delete this read...',
  'cvr.deleteTitle': 'Delete {name} permanently?',
  'cvr.deleteAll': 'This removes the import and all {removed} suggestions in it ({pending} '
    + 'unanswered, {rejected} rejected). Nothing you confirmed came from it.',
  'cvr.deleteKeeps': '{removed} unconfirmed suggestions are removed ({pending} unanswered, '
    + '{rejected} rejected). The {confirmed} you confirmed stay in your evidence, and the lines '
    + 'they came from are kept as their source.',
  'cvr.deleteArchiveInstead': 'To put it away and keep everything, archive it instead.',
  'cvr.deleteForever': 'This cannot be undone.',
  'cvr.deleteConfirm': 'Delete permanently',
  'cvr.cancel': 'Cancel',
  'cvr.companyUnknown': 'Company not stated',
  'cvr.roleUnknown': 'Role not stated',
  'cvr.datesUnknown': 'Dates not stated',
  'cvr.period': '{start} to {end}',
  'cvr.periodCurrent': '{start} to present',
  'cvr.editDetails': 'Correct company, role or dates',
  'cvr.saveDetails': 'Save details',
  'cvr.createExperience': 'Add experience',
  'cvr.saved': 'Saved. The document\'s own lines are unchanged.',
  'cvr.created': 'Experience added.',
  'cvr.company': 'Company',
  'cvr.role': 'Role',
  'cvr.start': 'Start month',
  'cvr.end': 'End month',
  'cvr.current': 'I still work here',
  'cvr.writtenAs': 'The document says: {text}',
  'cvr.backToRead': 'Back to all experiences',
  'cvr.groupCounts': '{waiting} of {total} still to answer.',
  'cvr.unresolved': 'Career Agent could not read the {what}. Fill in what you know; nothing is guessed.',
  'cvr.missing.company': 'company',
  'cvr.missing.role': 'role',
  'cvr.missing.dates': 'dates',
  'cvr.missing.structure': 'structure',
  'cvr.documentSaid': 'What the document said',
  'cvr.sourceLine': 'Line {line}: {text}',
  'cvr.mergeInto': 'Merge into',
  'cvr.merge': 'Merge into this experience',
  'cvr.merged': 'Merged. Every suggestion and every source line moved with it.',
  'cvr.deleteExperience': 'Remove this empty experience',
  'cvr.experienceDeleted': 'Experience removed.',
  'cvr.noExperience': 'No experience',
  'cvr.moveTo': 'Move to',
  'cvr.moveLabel': 'Move to another experience: {text}',
  'cvr.moveSelectedTo': 'Move the selected suggestions to',
  'cvr.moveSelected': 'Move selected',
  'cvr.moved': 'Moved {n}. Their text and source lines are unchanged.',
  'cvr.splitSelected': 'Split into a new experience',
  'cvr.splitSave': 'Create experience with the selected',
  'cvr.split': 'Split {n} into a new experience.',
  'cvr.rejectSelected': 'Reject selected',
  'cvr.rejectedN': 'Rejected {n}. Nothing was confirmed.',
  'cvr.deleteSelected': 'Delete selected...',
  'cvr.deleteSuggestions': 'Delete {n} suggestions permanently? Confirmed ones are never deleted '
    + 'here. This cannot be undone.',
  'cvr.deletedN': 'Deleted {n}.',
  'cvr.selectedN': '{n} selected',
  'cvr.selectLabel': 'Select: {text}',
  'cvr.confirmedNote': 'Confirmed. To withdraw it, retire it in Career Evidence.',
  'cvr.undoReject': 'Undo reject',
  'cvr.deleteOne': 'Delete',
  'cvr.deleteOneSure': 'Delete for good?',
  'cvr.thisExperience': 'This experience',
  'cvr.mergeFold': 'Merge with another experience',
  'cvr.mergeHelp': 'Moves every suggestion and every source line from this experience '
    + 'into the one you choose, then removes this one. Nothing is confirmed.',
  'cvr.deleteOneLabel': 'Delete this suggestion permanently: {text}',
  'cvr.fromLine': 'From your CV, line {line}',
  'cvr.asWritten': 'As written in the file',
  'cvr.answered.ACCEPTED': 'Confirmed.',
  'cvr.answered.EDITED': 'Confirmed in your words.',
  'cvr.answered.REJECTED': 'Rejected.',
  'cvr.answered.PENDING': 'Back to unanswered.',
  'cvr.attention.duplicate': 'The same sentence appears earlier in this read.',
  'cvr.attention.structure': 'Check this experience\'s company, role or dates.',
  'cvr.attention.unplaced': 'Not placed in an experience yet.',
  'cvr.section.experience': 'Experience not placed',
  'cvr.section.volunteering': 'Volunteering',
  'cvr.section.internships': 'Internships',
  'cvr.section.freelance': 'Freelance work',
  'cvr.section.skills': 'Skills',
  'cvr.section.tools': 'Tools',
  'cvr.section.education': 'Education',
  'cvr.section.certifications': 'Certifications',
  'cvr.section.projects': 'Projects',
  'cvr.section.activities': 'Activities',
  'cvr.section.awards': 'Awards',
  'cvr.section.languages': 'Languages',
  'cvr.section.unplaced': 'Work not placed in an experience',
  'career.state.UNRESOLVED': 'Not sure yet',
  'career.confirmOne': 'Confirm this one',
  'career.confirmOneLabel': 'Confirm: {text}',

  // -- the candidate intake package --------------------------------------
  'intake.heading': 'Sources and imports',
  'intake.lede': 'Where the evidence above came from. An import is one pass over your '
    + 'documents, and nothing in it is true about you until you say it is.',
  'intake.waiting': '{n} to look at',
  'intake.allAnswered': 'All answered',
  // The stored generator is an identifier, `SELF:career-agent local
  // extractor`, and it used to be printed as one. What a reader needs is
  // the KIND: whether this program read the document or an assistant she
  // chose did, which is what decides how carefully to read the result.
  'intake.readBy.SELF': 'Read by Career Agent on this computer.',
  'intake.readBy.EXTERNAL_AI': 'Read by an assistant you chose.',
  'intake.readBy.MANUAL': 'Written by hand.',
  'intake.readByNamed': 'Read by {name}, an assistant you chose.',
  'intake.progressLabel': '{done} of {total} looked at',
  'intake.startReview': 'Start review',
  'intake.continueReview': 'Continue review',
  'intake.reopenReview': 'Review again',
  'intake.putAway': 'Archive',
  // WHERE TO START, when three hundred statements are waiting.
  //
  // Every sentence here is careful about one thing: a step says where a
  // statement is MET, never what it means. Nothing is hidden from any step,
  // the same four answers are offered everywhere, and an early step is not a
  // more believable step. `intake.startNavigationOnly` says that out loud
  // rather than leaving a reader to infer it from an ordered list.
  //
  // And no number here is a score. Each one names its own denominator in the
  // same sentence.
  'intake.startHeading': 'Where to start',
  'intake.startNavigationOnly': 'This is a reading order, not a ranking. Nothing is hidden '
    + 'from any step, every statement can still be confirmed, corrected, rejected or left '
    + 'for later, and being further down does not make something less true.',
  'intake.startEssential': '{waiting} of {total} statements in the first three steps are '
    + 'still waiting. Answer those and the product has enough to work with, out of {all} in '
    + 'the package.',
  'intake.startEssentialDone': 'The first three steps are answered. {waiting} of {all} '
    + 'statements in the package are still waiting, and none of them is blocking.',
  'intake.startEssentialMeans': 'Enough to work with means: your documents agree about the '
    + 'dates, every statement about work names an employer, and your most recent job is '
    + 'answered. Everything else can wait, and nothing else is hidden while it does.',
  'intake.startFocus': '{waiting} of {total} statements mentioning "{term}" are still '
    + 'waiting. That is the requirement you came here from.',
  'intake.openStep': 'Open this step',
  'intake.openStepLabel': 'Open the step {name}',
  'intake.stepNone': 'None of these',
  'intake.stepCount': '{answered} of {total} answered in this step',
  'intake.stepBlocking': 'These come first because nothing else in the package can be '
    + 'confirmed while your documents disagree about the dates.',
  'intake.step.SETTLE_DISAGREEMENTS': 'Settle where your documents disagree',
  'intake.step.NAME_THE_EMPLOYER': 'Say who these were for',
  'intake.step.RECENT_WORK': 'Your most recent job',
  'intake.step.MEASURABLE_OUTCOMES': 'Things you measured',
  'intake.step.EARLIER_WORK': 'Earlier work and projects',
  'intake.step.SKILLS_AND_TOOLS': 'Skills and tools',
  'intake.step.STUDY_AND_CERTIFICATES': 'Study and certificates',
  'intake.step.ANYTHING_ELSE': 'Anything else',
  'intake.stepWhy.SETTLE_DISAGREEMENTS': 'Two of your documents give different dates for the '
    + 'same period. Answering that once releases every statement about it.',
  'intake.stepWhy.NAME_THE_EMPLOYER': 'These describe work but do not say who it was for, so '
    + 'nothing can be attributed to them yet.',
  'intake.stepWhy.RECENT_WORK': 'The job you are likeliest to remember in detail, and the one '
    + 'most employers ask about first.',
  'intake.stepWhy.MEASURABLE_OUTCOMES': 'Statements carrying a figure, kept in the sentence '
    + 'you wrote it in.',
  'intake.stepWhy.EARLIER_WORK': 'Everything else about jobs and projects, most recent first.',
  'intake.stepWhy.SKILLS_AND_TOOLS': 'What your documents say you can do and what you have '
    + 'worked with.',
  'intake.stepWhy.STUDY_AND_CERTIFICATES': 'Degrees, courses and certificates.',
  'intake.stepWhy.ANYTHING_ELSE': 'Statements that did not fit any of the above. Nothing is '
    + 'left out of this list.',
  // WHICH READING OF HER DOCUMENTS IS IN FORCE.
  //
  // Two packages built from the same CV and the same LinkedIn export look
  // identical on a screen that shows only a filename and a count, and a
  // reviewer who cannot tell which one she is answering is answering neither.
  // Every string here exists to make that unmistakable.
  //
  // None of them says "confirmed". Choosing a package is choosing what to
  // read; it is not standing behind a sentence in it.
  'intake.status.ACTIVE': 'Active',
  'intake.status.SUPERSEDED': 'Replaced',
  'intake.status.DISCARDED': 'Archived',
  'intake.status.INCOMPLETE': 'Nothing in it',
  'intake.useThisOne': 'Use this one instead',
  'intake.restore': 'Restore',
  'intake.inspect': 'View details',
  'intake.incompleteWhy': 'This import produced no statements, so there is nothing to review. '
    + 'It has not replaced anything.',
  'intake.supersededBy': 'Set aside when {name} was prepared from your documents. Nothing in '
    + 'it was lost, and you can come back to it.',
  'intake.restoredActive': '{name} is back, and it is the reading in force.',
  'intake.restoredAside': '{name} is back and set aside. You are still reviewing the one you '
    + 'had open; use "Review this one instead" to switch.',
  'intake.putAwayConfirm': 'Archive this import?\n\n{name}\n\n{waiting} proposals in it are still unanswered. '
    + 'Nothing is deleted and nothing you have already confirmed is touched. You can '
    + 'restore it.',
  'intake.overviewHeading': 'What {name} says about you',
  'intake.overviewLede': '{waiting} of {total} still want an answer from you. '
    + 'They are grouped so you can do one job at a time.',
  'intake.overviewDone': 'All {total} answered.',
  'intake.documents': 'The documents behind it',
  'intake.source.RESUME': 'Your CV',
  'intake.source.LINKEDIN': 'Your profile',
  'intake.source.DOCUMENT': 'A document',

  'intake.conflictHeading': 'Where your documents disagree',
  'intake.conflictNothingConfirmed': 'Choosing dates settles how to read two documents. '
    + 'It confirms nothing on its own -- every statement still comes to you one at a time.',
  'intake.conflictLede': 'Your documents give different dates for this. Settling it once '
    + 'answers the question for all {n} statements about it.',
  'intake.conflictUnnamed': 'A period in your history',
  'intake.conflictOpen': 'Not settled',
  'intake.conflictSettled': 'Settled',
  'intake.conflictRead': 'Read as {reading}',
  'intake.conflictFrom': 'From {sources}, carried by {n} statements',
  'intake.conflictFromOne': 'From {sources}, carried by one statement',
  'intake.conflictChoose': 'These dates are right',
  'intake.conflictChooseLabel': 'Choose the dates {dates}',
  'intake.conflictChosen': 'You chose these dates.',
  'intake.conflictReopen': 'Change this answer',
  'intake.conflictSaved': 'Saved. Those statements are back in the queue.',
  'intake.span': '{start} to {end}',
  'intake.spanCurrent': '{start}, still there',
  'intake.spanOpen': '{start}, no end given',
  'intake.noDate': 'no date given',
  'intake.noReading': 'no dates this program could read',

  'intake.groupsHeading': 'Where to start',
  'intake.groupsLede': 'Work is grouped by employer, because that is the question you can '
    + 'actually answer: what does this say about my time there.',
  'intake.groupPeriod': '{span} -- {n} statements',
  'intake.groupPeriodOne': '{span} -- one statement',
  'intake.groupSize': '{n} statements',
  'intake.groupSizeOne': 'One statement',
  'intake.groupKinds': 'Holds: {kinds}',
  'intake.groupConflicted': '{n} of these are waiting on dates your documents disagree about.',
  'intake.openGroup': 'Look at these',
  'intake.openGroupLabel': 'Look at the statements about {name}',
  'intake.statesHeading': 'What you have already decided',
  'intake.stateChip': '{state} ({n})',
  'intake.state.UNREVIEWED': 'Not looked at',
  'intake.state.CONFIRMED': 'Confirmed',
  'intake.state.CORRECTED_BY_USER': 'Confirmed, your wording',
  'intake.state.CONFLICT': 'Waiting on dates',
  'intake.state.UNRESOLVED': 'Not sure yet',
  'intake.state.REJECTED': 'Dropped',

  'intake.backToPackage': 'Back to the overview',
  'intake.listLede': '{waiting} of {total} here still want an answer.',
  'intake.listDone': 'All {total} here are answered.',
  'intake.search': 'Find one of these',
  'intake.focusedOn': 'Showing what is waiting for an answer and mentions {term}. Nothing is hidden permanently.',
  'intake.dropFocus': 'Show everything waiting',
  'intake.searchPlaceholder': 'a word from the statement',
  'intake.noMatch': 'Nothing here matches that.',
  'intake.emptyList': 'Nothing is filed here yet.',
  'intake.truncated': 'Showing {shown} of {matched}. Answer some of these and the rest follow.',
  'intake.noBulkConfirm': 'There is no "confirm everything". Anything you confirm here can '
    + 'end up on a real application, and one click cannot mean you read them all.',
  'intake.batchUnsure': 'Set the remaining {n} aside as not sure yet',
  'intake.batchUnsureConfirm': 'Mark {n} statements as "not sure yet"? Nothing is confirmed '
    + 'and nothing is dropped -- they stop being presented as untouched, and you can answer '
    + 'any of them later.',
  'intake.asItArrived': 'As the package wrote it',
  'intake.fromDocument': 'From {document}',
  'intake.foundIn': 'Found in {where}',
  'intake.datesWrote': 'Your document wrote {span}',
  'intake.datesRead': 'Career Agent read that as {span}',
  'intake.tools': 'Tools named here: {tools}',
  'intake.figure': 'This carries a figure, in the sentence you stated it in: "{sentence}". '
    + 'The number is never taken out of the sentence.',
  'intake.claimConflicted': 'This statement carries dates your documents disagree about. '
    + 'Settle them once, at the top, and it comes back here.',
  'intake.settleFirst': 'Settle the dates first',
  'intake.confirm': 'Yes, that is true',
  'intake.unsure': 'Not sure yet',
  'intake.reopen': 'Answer it again',
  'intake.retireInLedger': 'This is a claim you stand behind now. Retire it in your evidence '
    + 'below, which keeps its history.',

  // -- claim kinds -------------------------------------------------------
  // THE GROUP HEADINGS, and they are not the `claim.type.*` labels.
  //
  // Those were written to name ONE claim -- "A skill", "A certification" --
  // and reusing them over a collapsible group of forty-eight produced
  // headings that disagreed with their own counts. A heading names the
  // category; the singular labels stay where a single claim is described.
  'claimGroup.EMPLOYMENT': 'Experience',
  'claimGroup.PROJECT': 'Projects',
  'claimGroup.ACHIEVEMENT': 'Achievements',
  'claimGroup.SKILL': 'Skills',
  'claimGroup.TOOL': 'Tools',
  'claimGroup.EDUCATION': 'Education',
  'claimGroup.CERTIFICATION': 'Certifications',
  'claimGroup.METRIC': 'Results and figures',
  'claim.type.EMPLOYMENT': 'Work you have done',
  'claim.type.PROJECT': 'A project',
  'claim.type.ACHIEVEMENT': 'Something you achieved',
  'claim.type.SKILL': 'A skill',
  'claim.type.TOOL': 'A tool you know',
  'claim.type.EDUCATION': 'Education',
  'claim.type.CERTIFICATION': 'A certification',
  'claim.type.METRIC': 'A figure',
  // -- the daily digest ---------------------------------------------------
  'daily.open': 'Today',
  'daily.title': 'Worth looking at today',
  'daily.close': 'Close',
  'daily.retry': 'Try again',
  'daily.worthLooking': 'roles worth looking at',
  'daily.lastLooked': 'You last marked this read on {when}.',
  'daily.neverLooked': 'You have never marked this read, so the first section goes by the '
    + 'date the board published instead.',
  'daily.markRead': 'I have read this',
  'daily.nothing': 'Nothing here today.',
  'daily.posted': 'posted {date}',
  'daily.firstSeen': 'first seen {date}',
  'daily.noDate': 'no date',
  'daily.unscored': 'not scored',
  'daily.section.recent.title': 'New in the last {n} days',
  'daily.section.recent.lead': 'Filtered by the date the board published, ORDERED by the same '
    + 'match score as everywhere else. Recency decides what is in this section; it does not '
    + 'decide what is at the top of it.',
  'daily.section.since_last_review.title': 'Since you last looked',
  'daily.section.since_last_review.lead': 'Postings this machine first held after your last '
    + 'review. That is when WE noticed them, not when the employer wrote them -- the two are '
    + 'different facts and neither stands in for the other.',
  'daily.section.best.title': 'Best matches right now',
  'daily.section.best.lead': 'The same score the cards show, in the same order. Nothing is '
    + 're-ranked here.',
  'daily.section.unresolved.title': 'Waiting on one answer',
  'daily.section.unresolved.lead': 'Jobs with strong search fit whose eligibility nobody has resolved. One '
    + 'recruiter question each would settle them.',
  'daily.section.tracking.title': 'You are tracking',
  'daily.section.tracking.lead': 'Anything you saved or moved. These stay visible whatever a '
    + 'rescore decides.',
  'daily.unscoredHead': 'These jobs have not been scored yet.',
  'daily.unscoredBody': 'There are {n} jobs here and none is scored against your current '
    + 'settings, so today has nothing to show. Recalculating happens on your own computer '
    + 'and costs nothing.',
  // -- what to lead with, for one posting --------------------------------
  'resume.heading': 'What to lead with, if you apply',
  'resume.lede': 'Your own confirmed sentences, put in the order this posting argues for. '
    + 'Nothing is rewritten and nothing new is written: this only chooses and orders what '
    + 'you have already said is true.',
  'resume.answers': 'Answers: {list}',
  'resume.nothingSpeaks': 'Nothing you have confirmed answers what this posting asks for. '
    + 'That is worth knowing before you write anything.',
  'resume.spare': '{n} other confirmed facts',
  'resume.spareLede': 'Still true, and this posting does not argue for them. What belongs in '
    + 'your document is your decision.',
  'resume.gapsHead': 'And what nothing of yours answers',
  'resume.gapsLede': 'These stay on the page. A document written without knowing them is a '
    + 'document you have to defend in an interview.',
  // -- source health, in words rather than in coverage classes -----------
  'source.state.HEALTHY': 'Working',
  'source.state.ATTENTION': 'Needs a look',
  'source.state.NEEDS_SETUP': 'Needs a key or quota',
  'source.state.WAITING': 'Waiting for permission',
  'source.state.NOT_RUN': 'Not run yet',
  'source.state.DISABLED': 'Off, by their rules',
  'source.state.BLOCKED_PROVIDER': 'Access blocked or unavailable',
  'source.state.DISABLED_QUOTA': 'Off, quota exhausted',
  'source.state.NOTHING_PUBLISHED': 'No job board published',
  // -- the job card ------------------------------------------------------
  'card.gatedBecause': 'Not eligible: {reason}',
  'card.gated': 'This posting states a requirement you do not meet',
  'card.offTarget': 'Not the kind of work you asked for.',
  'card.offTargetBecause': 'Not the work you asked for. {reason}',
  'card.where': 'Where',
  'card.contract': 'Contract',
  'card.salary': 'Salary',
  'card.salaryUnstated': 'Salary not stated',
  'card.toolsLabel': 'Tools named in this posting',
  'card.moreTools': '{n} more. Open the job to see them.',
  'card.posted': 'Posted: {date}',
  'card.noPostedDate': 'No publication date recorded',
  'card.you': 'You:',
  // -- the filter rail ---------------------------------------------------
  'filters.section.find': 'Find',
  'filters.section.findHelp': 'Searches the whole posting, not just the job title.',
  'filters.section.quick': 'Quick filters',
  'filters.section.quality': 'Search fit and posting detail',
  'filters.section.qualityHelp': 'Two separate numbers. Neither is a prediction about your chances. ',
  'filters.preset.all': 'Everything',
  'filters.preset.allHelp': 'Every job collected so far, with nothing filtered out.',
  'filters.preset.strong': 'Strong search fit',
  'filters.preset.strongHelp': 'Jobs scoring 70 or more on how close they are to the work you want. Says '
    + 'nothing about whether you could take them.',
  'filters.preset.eligible': 'Nothing standing in the way',
  'filters.preset.eligibleHelp': 'Only jobs where nothing in the posting rules you out. Jobs that never '
    + 'said where they hire are left out, because silence is not permission.',
  'filters.preset.applied': 'Already applied',
  'filters.preset.appliedHelp': 'Jobs you have actually sent an application to.',
  'filters.toggle.saved': 'Only ones I saved',
  'filters.toggle.hasSalary': 'Only ones that state a salary',
  'filters.toggle.hasSalaryHelp': 'Most postings do not. This will hide a lot of real jobs.',
  'filters.toggle.remote': 'Remote only',
  'filters.toggle.latam': 'Open to Latin America',
  'filters.toggle.latamHelp': 'The posting named Latin America, or a country in it, as somewhere it '
    + 'hires.',
  'filters.toggle.worldwide': 'Says it hires worldwide',
  'filters.toggle.worldwideHelp': 'The posting said so in as many words. Remote on its own does not count. ',
  'filters.toggle.enriched': 'Only ones the local model has read',
  'filters.toggle.enrichedHelp': 'A model on your own machine, run only when you ask. It never changes the '
    + 'score.',
  'filters.clearSection': 'Clear the {section} filters',
  'filters.activeIn': '{n} active in {section}',
  'filters.startingPoints': 'Starting points',
  'filters.startFrom': 'Start from',
  'filters.searchPlaceholder': 'Search title, company, place or posting text',
  'filters.searchClear': 'Clear the search',
  'filters.posted': 'Posted',
  'filters.posted.any': 'Any time',
  'filters.posted.3': 'Last 3 days',
  'filters.posted.7': 'Last week',
  'filters.posted.14': 'Last 2 weeks',
  'filters.posted.30': 'Last month',
  'filters.posted.90': 'Last 3 months',
  'filters.salaryCurrency': 'Currency for the lowest salary',
  'filters.phrasePlaceholder': 'Type a word and press Enter',
  'filters.mustMention': 'Must mention',
  'filters.mustNotMention': 'Must not mention',

  // THE SOFT PAIR, and the hint is doing real work.
  //
  // A control that reorders is indistinguishable from one that filters until
  // somebody notices the count did not move -- and by then they have already
  // decided the control is broken. Saying it up front costs one sentence.
  'filters.prefer': 'Prefer',
  'filters.avoid': 'Rather not',
  'filters.softHint':
    'These two change the ORDER, not the list. Nothing is hidden by them, and '
    + 'the count above stays the same.',
  'filters.trackingChip': 'Only ones I am tracking',
  // -- the card footer and the badges ------------------------------------
  'card.save': 'Save',
  'card.saved': 'Saved',
  'card.saveLabel': 'Save {title}',
  'card.unsaveLabel': 'Unsave {title}',
  'card.apply': 'Apply on the company site',
  'badge.notScored': 'not scored',
  // -- the job drawer ----------------------------------------------------
  'drawer.close': 'Close details',
  'drawer.loading': 'Loading...',
  'drawer.loadFailed': 'Could not load this posting',
  'drawer.unscored': 'This job has not been scored yet, so there is nothing to explain. '
    + 'Scores are recalculated when your preferences change.',
  'drawer.unscoredNumber': 'This job has not been scored, so there is no number to explain.',
  'drawer.matchedNoQuote': 'Matched, but the posting had no single line worth quoting.',
  'drawer.matchedNoLine': 'Matched, without a quotable line.',
  'drawer.applicationStatus': 'Application status',
  'drawer.nothingMatchedHere': 'Nothing in this posting matched here.',
  'drawer.countedAgainst': 'What counted against it',
  'drawer.noBreakdown': 'There is no breakdown stored for this posting.',
  'drawer.advanced': 'Advanced scoring details',
  'drawer.nothingChecked': 'Nothing has been checked for this posting yet.',
  'drawer.whyNotHigher': 'The unticked lines above are why this number is not higher.',
  'drawer.salary': 'Salary',
  'drawer.employmentType': 'Employment type',
  'drawer.worksite': 'Office or remote',
  'drawer.seniority': 'Seniority',
  'drawer.noDescription': 'No description text was stored for this posting.',
  'drawer.notesPlaceholder': 'Your notes. Saved when you click away.',
  'drawer.notes': 'Notes',
  'drawer.cancelLocalModel': 'Cancel the local model request',
  'drawer.localModelNote': 'Read by a model on your own computer. It never changes the score.',
  'drawer.localModelIdle': 'Nothing yet. This runs on your machine, only when you ask.',
  'drawer.jobBoard': 'Job board',
  'drawer.postedOn': 'Posted',
  'drawer.firstSeen': 'First seen',
  'drawer.lastSeen': 'Last seen',
  'drawer.original': 'Original',
  'drawer.advancedHelp': 'How the number was built, point by point. Every point comes from a quote '
    + 'in the posting.',
  'drawer.whySection': 'Why this fits your search',
  // -- the rest of the interface -----------------------------------------
  'app.loading': 'Loading...',
  'app.thisPosting': 'this posting',
  'app.rescore': 'Recalculate search fit',
  'app.rescoreFailed': 'Something went wrong. Try again',
  'app.rescoreDone': 'Done. Loading your matches',
  'app.starting': 'Starting...',
  'app.listFailed': 'The list of jobs could not be loaded.',
  'app.listFailedShort': 'Could not load the list.',
  'health.noLocalModel': 'no local model set up',
  'health.localModelSilent': 'local model is not answering',
  'health.localModelUntried': 'local model not contacted yet',
  'health.searchSlow': 'search is running slowly',
  'health.databaseHelp': 'The exact set of jobs this window is reading.',
  'health.unknown': 'Cannot tell how this is doing right now.',
  'app.stale': 'Your preferences changed. These matches are out of date.',
  'table.caption': 'Job postings',
  'table.help': 'Job list. Scrolls sideways.',
  'table.visibleColumns': 'Visible columns',
  'table.columns': 'Columns',
  'table.gatedShort': 'States a requirement you do not meet',
  'table.offTargetShort': 'Not the kind of work you asked for',
  'table.noneRecorded': 'none recorded',
  'filters.search': 'Search',
  'filters.minSalaryYear': 'Lowest yearly salary you would consider',
  'filters.minSalary': 'Lowest salary you would consider',
  'filters.aYear': 'a year',
  'filters.currencyPlaceholder': 'Currency...',
  'filters.showingOnly': 'Showing only',
  'prefs.none': 'No editable phrases were found.',
  'prefs.atLeastOne': 'Leave at least one phrase.',
  'prefs.saving': 'Saving...',
  'profile.saving': 'Saving...',
  'profile.countryPlaceholder': 'Start typing a country...',
  'profile.yourChoices': 'What you are looking for',
  'retrieval.funnel': 'Retrieval funnel',
  'revision.stale':
    'These jobs answer your previous preferences (revision {serving}). You are now '
    + 'on revision {current}. Nothing was lost: the scores for the new preferences '
    + 'are not ready yet.',
  'revision.progress': 'Recalculating: {done} of {total} ({pct}%)',
  'revision.recalculate': 'Recalculate now',
  'revision.interrupted': 'Recalculation stopped at {done} of {total} ({pct}%) before it finished. '
    + 'The list still answers your previous preferences.',
  'revision.resume': 'Continue recalculating',
  'revision.lost': 'Lost contact with Career Agent while recalculating. It may have stopped.',
  'revision.checkAgain': 'Check again',
  'app.rescoreLost': 'Lost contact while recalculating. Try again',
  'prefs.reach': 'Lexical reach: {n} of {total} scored open postings ({pct}%). Title or body.',
  'prefs.reachNone':
    'Lexical reach: no configured phrase was found in the {total} scored open postings. '
    + 'Equivalent work may use different wording.',
  'prefs.reachUnmeasured':
    'Not counted here. This one is a rule the eligibility check applies to the whole '
    + 'posting, not a phrase the score records.',
  'prefs.savedPhrases': 'Current saved phrases',
  'prefs.contextReach': 'Lexical reach applies the saved context and negation rules.',
  'prefs.unsavedPhrases': 'Unsaved edits. Reach counts still describe stored scores.',
  'prefs.scoringPhrasesHint':
    'These phrases are matcher inputs. Saving changes your search configuration; '
    + 'recalculate to update scores.',
  'prefs.reachRevision': 'Reach uses stored scores from revision {version}, not draft edits or candidate evidence.',
  'prefs.bodyReach': 'Positive body scoring reach: {n} postings contributed points before component caps.',
  'prefs.bodyReachUnmeasured':
    'Positive body scoring reach is not recorded for these scores. Available after '
    + 'recalculation.',
  'prefs.noChange': 'Nothing changed, so nothing was saved.',
  'flash.hiddenWithReason': 'Set aside, and thank you for saying why.',
  'flash.whyHidden': 'Why? (optional)',
  'hideReason.WRONG_WORK': 'Not my work',
  'hideReason.WRONG_PLACE': 'Wrong place',
  'hideReason.WRONG_LEVEL': 'Wrong level',
  'hideReason.TITLE_MISLEADING': 'Title misled me',
  'hideReason.PAY': 'Pay',
  'hideReason.EMPLOYER': 'This employer',
  'hideReason.STALE': 'Old or already seen',
  'hideReason.OTHER': 'Something else',
  'prefs.confirm': 'Press Save again to confirm.',
  'prefs.diffAdded': 'Adding: {list}.',
  'prefs.diffRemoved': 'Removing: {list}.',
  'prefs.diffCost':
    'Saving changes the search configuration. Recalculation is required to update search-fit '
    + 'scores; existing results remain available until then.',
  'retrieval.scoredElsewhere':
    'None of these are scored against your current preferences, but {n} scores are '
    + 'still stored from an earlier version of them. Nothing was lost: the question '
    + 'changed. Recalculate to bring them up to date.',
  'retrieval.bySource': 'By source',
  'retrieval.boardsHelp': 'Company job boards that answered, out of the ones we asked.',
  'sources.loading': 'Reading the catalogue...',
  // The source table's columns. Only fields the server actually sends:
  // there is no Enabled column because there is no per-source switch, and
  // no Last sync because nothing records one.
  'sources.colSource': 'Source',
  'sources.colStatus': 'Status',
  'sources.colWhere': 'Where',
  'sources.colPostings': 'Postings',
  'sources.colWhy': 'Detail',

  // HOW FRESH, WHICH IS A DIFFERENT QUESTION FROM WHAT IT CAN DO.
  //
  // Every one of these has to survive being read by somebody who does not know
  // what a collector is. "Refreshing now" rather than RUNNING, "we stopped
  // early" rather than PARTIAL, and a paused source says what it is waiting
  // for rather than looking broken.
  'sources.refreshHead': 'How fresh each one is',
  'sources.refreshNote':
    'Collecting happens in the background. Everything already found stays in '
    + 'your list while it runs, so nothing here has to finish before you can '
    + 'look at jobs.',
  'sources.colRefresh': 'Right now',
  'sources.colProgress': 'Found in this run',
  'sources.colFresh': 'Last successful run',
  'sources.state.NOT_STARTED': 'Never run',
  'sources.state.QUEUED': 'Waiting to start',
  'sources.state.RUNNING': 'Updating',
  'sources.state.PARTIAL': 'Partially refreshed',
  'sources.state.COMPLETE': 'Refresh complete',
  'sources.state.PAUSED': 'Paused',
  'sources.state.FAILED': 'Last refresh failed',
  'sources.state.BLOCKED': 'Currently unavailable',
  // "Not measured" and "none" are different answers, and this is the first.
  'sources.state.STALE': 'Out of date',
  'sources.staleHelp': 'The last successful refresh is more than three days old. Refresh it to see current jobs.',
  'sources.reason.PAGE_LIMIT': 'Stopped at this source\'s page limit, by design: it holds more than one refresh reads.',
  'sources.reason.SOURCE_CEILING': 'The source serves no more than this through its public access.',
  'sources.reason.REQUEST_BUDGET': 'Stopped at this refresh\'s request budget; the next refresh continues.',
  'sources.reason.SOME_FAILED': 'Some requests failed this time; what was read is kept.',
  'sources.reason.BOARDS_DEFERRED': 'Some employer boards were left for the next refresh.',
  'sources.notMeasured': 'not measured',
  'sources.retrievedNoTotal': 'found (total not published)',
  'sources.neverFresh': 'never',
  'sources.etaAbout': 'about',
  'sources.etaUnderMinute': 'under a minute left',
  'sources.etaMinutes': 'minutes left',
  'sources.etaHours': 'hours left',
  'sources.exactReason': 'The exact reason',
  'dom.noLink': 'No usable link on this posting',
  'kanban.appliedHelp': 'The date you applied. Later stages never overwrite it.',
  // -- the long sentences ------------------------------------------------
  'app.emptyAside': 'Your experience can open more paths than one job title. This tool '
    + 'searches what a posting says about the work, not just what it is called.',
  'health.searchSlowHelp': 'Searching is slower than usual, and it matches a little differently: it '
    + 'finds your words inside longer words, and looks in one place rather than'
    + 'everywhere. Recalculating your matches fixes it.',
  'health.staleFacetsHelp': 'These jobs were scored before the country, region, office and salary '
    + 'filters existed, so those four will not find them. Recalculating fixes'
    + 'it, on your own computer and at no cost.',
  'filters.qualityHint': 'Match is how close the work is to what you want. Posting detail is how '
    + 'much the employer actually wrote down. A short posting scores low on'
    + 'detail however good the job is.',
  'filters.salaryHint': 'Pick a currency too. Nothing here converts between currencies, so '
    + 'without one there is nothing to compare against. Postings that quote a '
    + 'month or an hour are converted to a year first. Most postings never '
    + 'state a salary at all, and those are left out by this filter rather than'
    + 'kept.',
  'prefs.lede': 'These phrases are what the score is made of. Edit them here; nothing is '
    + 'written to the shipped defaults.',
  'profile.nothingConfigured': 'Start on Home to describe the work you want, then edit your preferences here.',
  // The three tabs, named by WHO DECIDES rather than by what they contain.
  // "Preferences" would have been the design's word and would have described
  // all three of them.
  'profile.tabsLabel': 'Which part of your profile to look at',
  'retrieval.lastLookedHelp': 'When we last went and looked -- not when an employer last published '
    + 'something.',
  // -- button labels, which are positional and were missed once ----------
  'action.close': 'Close',
  'action.tryAgain': 'Try again',
  'action.cancel': 'Cancel',
  'action.save': 'Save',
  'action.clear': 'Clear',
  'action.clearAll': 'Clear all',
  'action.clearAllFilters': 'Clear all filters',
  'action.retry': 'Retry',
  'action.previous': 'Previous',
  'action.next': 'Next',
  'action.apply': 'Apply',
  'drawer.clearAppliedDate': 'Clear applied date',
  'drawer.askLocalModel': 'Ask the local model to read it',
  'pager.position': 'Page {page} of {pages}',
  'filters.section.place': 'Where and how you would work',
  'filters.section.role': 'Role and level',
  'rail.resultsHeading': 'Jobs',
  // -- the first run -----------------------------------------------------
  'firstrun.title': 'Start here',
  'firstrun.lede':
    'A few things, in this order. None of them is required, and you can stop '
    + 'after any one of them -- each says what it lets Career Agent work out.',
  'firstrun.privacy':
    'Everything below happens on this computer. Your documents are read by '
    + 'Career Agent itself, they are never uploaded anywhere, no AI sees them, '
    + 'and the files are not kept -- only the lines you confirm.',
  'firstrun.step.documents': 'Import your career information',
  'firstrun.why.documents':
    'A CV is enough to start. A LinkedIn export adds dates, and any other '
    + 'document you have can go in too.',
  'firstrun.step.evidence': 'Say which of it is true',
  'firstrun.why.evidence':
    'Nothing read from a document counts until you confirm it. Until something '
    + 'is confirmed, every requirement on every job reads as a gap.',
  'firstrun.step.where': 'Where you live, and who may hire you',
  'firstrun.why.where':
    'The one answer that can empty the whole list. Without it no job can be '
    + 'shown as open to you, because nothing counts as a place you may work.',
  'firstrun.step.work': 'What kind of work you are looking for',
  'firstrun.why.work':
    'Described in your own words, and matched against the whole posting rather '
    + 'than the job title -- so a job with an odd name still reaches you.',
  'firstrun.step.jobs': 'Find jobs',
  'firstrun.why.jobs':
    'Collect postings and score them against everything above. Offline, and '
    + 'nothing about you is sent anywhere.',
  'firstrun.go.evidence': 'Open the review',
  'firstrun.go.where': 'Answer this',
  'firstrun.go.work': 'Describe the work',
  'firstrun.go.jobs': 'Go to jobs',
  'firstrun.state.documents': '{n} read',
  'firstrun.state.noDocuments': 'Nothing read yet',
  'firstrun.state.confirmed': '{n} confirmed by you',
  'firstrun.state.waiting': '{n} statements waiting for your answer',
  'firstrun.state.nothingToReview': 'Nothing to review yet',
  'firstrun.state.country': 'You said {code}',
  'firstrun.state.noCountry': 'Not answered yet',
  'firstrun.state.phrases': '{n} phrases describe the work you want',
  'firstrun.state.noPhrases': 'Not described yet',
  'firstrun.state.scored': '{n} postings scored',
  'firstrun.state.noScores': 'No postings scored yet',
  'firstrun.kind.resume': 'CV or resume',
  'firstrun.kind.linkedin': 'LinkedIn export',
  'firstrun.kind.document': 'Another document',
  'firstrun.kindLabel': 'What kind of document {name} is',
  'firstrun.choose': 'Choose files',
  'firstrun.supported': 'Accepted: {kinds}. A scanned CV is images rather than text and cannot be read.',
  'firstrun.removeFile': 'Remove',
  'firstrun.read': 'Read these on this computer',
  'firstrun.reading': 'Reading...',
  'firstrun.found':
    '{n} statements found. None of them is true yet -- the next step is you '
    + 'answering them one at a time.',
  'firstrun.reopened':
    'These documents had already been read. Your existing answers are kept, '
    + 'and {n} statements are in that review.',
  // -- what a posting asks of somebody starting out (migration 0027) ------
  'filters.section.entry': 'Getting in',
  'filters.section.entryHelp':
    'What a posting asks for in the way of previous experience, and what it '
    + 'says to people starting out or coming from another field.',
  'filters.experience': 'Experience the posting asks for',
  'filters.experienceHelp':
    'Every choice here shows only the postings that SAID how much they want. '
    + 'Most postings never mention it, and those are not counted as open.',
  'filters.experience.any': 'Any',
  'filters.experience.none': 'They said none is needed',
  'filters.experience.upTo1': 'Up to 1 year',
  'filters.experience.upTo2': 'Up to 2 years',
  'filters.experience.upTo5': 'Up to 5 years',
  'filters.toggle.includeTransferable': 'Show roles I could move into',
  'filters.includeTransferableHelp':
    'Off by default. Your search describes the work you have been doing, so it '
    + 'sets aside jobs in a field you are moving to. This brings back the ones '
    + 'that ask for things you have already confirmed about yourself. It does '
    + 'nothing until you have confirmed something.',
  'facet.experience_requirement': 'How firmly they ask for experience',
  'facet.entry_signal': 'What they say to people starting out',
  'chip.experienceNone': 'They said no experience is needed',
  'chip.experienceUpTo': 'Asks for at most {n} years',
  'experience.NONE_REQUIRED': 'None needed',
  'experience.REQUIRED_MINIMUM': 'A number of years',
  'experience.REQUIRED_UNQUANTIFIED': 'Required, no number',
  'experience.PREFERRED': 'Preferred, not required',
  'experience.NICE_TO_HAVE': 'A bonus',
  'experience.NOT_STATED': 'Did not say',
  'entrySignal.NO_EXPERIENCE_REQUIRED': 'No experience needed',
  'entrySignal.ENTRY_LEVEL': 'Entry level',
  'entrySignal.RECENT_GRADUATE': 'Recent graduates welcome',
  'entrySignal.TRAINING_PROVIDED': 'Training provided',
  'entrySignal.CAREER_CHANGERS_WELCOME': 'Open to other fields',
  'drawer.experienceHeading': 'Previous experience',
  'drawer.experienceNone':
    'This role does not require previous professional experience.',
  'drawer.experienceYears': 'This role asks for at least {n} years of experience.',
  'drawer.experienceUnquantified':
    'This role asks for previous experience without saying how much.',
  'drawer.experiencePreferred': 'Experience is preferred here, not required.',
  'drawer.experienceBonus': 'Experience is treated as a bonus here.',
  'drawer.experienceSilent':
    'This posting never mentions previous experience. That is not the same as '
    + 'saying none is needed.',
  'drawer.experienceCovered':
    'Your confirmed evidence answers {matched} of the {total} things this '
    + 'posting asks for.',
  'drawer.experienceStillUnconfirmed':
    'The experience it explicitly asks for is not among what you have confirmed.',
  'filters.section.pay': 'Pay',
  'filters.section.payHelp': 'Compared inside one currency. Nothing here converts between them.',
  'filters.section.skills': 'Tools and skills',
  'filters.section.sources': 'Where the job was found',
  'filters.section.progress': 'Where you are with it',
  'filters.section.advanced': 'Advanced',
  'filters.toggle.includeIneligible': 'Include jobs with eligibility conflicts',
  'filters.toggle.includeUnresolved': 'Include jobs that never said where they hire',
  'filters.includeUnresolvedHelp':
    'Off by default. These are jobs where the posting never said where the employer can hire, so nobody ' +
    'can tell whether you could take them. That is not a no -- it is a question the posting left open.',
  'jobs.hiddenUnresolved':
    '{n} hidden because the posting never said where the employer hires. Nothing rules you out; nothing ' +
    'confirms you either.',
  'jobs.showUnresolved': 'Show those too',
  'filters.toggle.includeOffTarget': 'Include work you set aside',
  'card.locations': '{n} locations',
  'card.locationsHelp': 'This employer published this role as {n} separate postings.',
  'card.morePlaces': '+{n} more',
  // -- the shell: navigation, the rail toggle and Home --------------------
  // THE WORKSPACE V2 SHELL.
  //
  // The rail, the page header contract and the mobile drawer. Every string a
  // reader meets in the frame around the product.
  //
  // The user card carries NO level and NO completeness percentage. What it
  // carries is the evidence review as a fraction, because that has a
  // denominator somebody can point at.
  'nav.settings': 'Settings & Sources',
  'nav.sectionSearch': 'Search',
  'nav.sectionProfile': 'Profile',
  'nav.sectionSystem': 'System',
  'sidenav.menu': 'Menu',
  'sidenav.menuLabel': 'Open the navigation',
  'sidenav.appearance': 'Appearance',
  'sidenav.language': 'Language',
  'sidenav.statJobs': 'jobs',
  'sidenav.statOpen': 'open',
  'sidenav.statInterview': 'interview',
  'pagehead.eyebrow.home': 'Today',
  'pagehead.title.home': 'How is your search going?',
  'pagehead.eyebrow.jobs': 'Discover',
  'pagehead.title.jobs': 'Jobs for you',
  'pagehead.eyebrow.applications': 'Tracking',
  'pagehead.title.applications': 'Where each one stands',
  'pagehead.eyebrow.profile': 'Profile',
  'pagehead.title.profile': 'Your career profile',
  'pagehead.eyebrow.evidence': 'Evidence',
  'pagehead.title.evidence': 'Professional evidence',
  'pagehead.eyebrow.settings': 'System',
  'pagehead.title.settings': 'Settings and sources',
  'nav.home': 'Home',
  'nav.jobs': 'Discover Jobs',
  'nav.applications': 'Applications',
  'nav.profile': 'Career Profile',
  'nav.evidence': 'Evidence',
  'rail.hide': 'Hide filters',
  'rail.show': 'Show filters',
  'home.title': 'Where your search stands',
  'home.since': 'Since you last marked the list read, on {when}.',
  'home.neverReviewed': 'You have not marked the list read yet, so the two counts about what '
    + 'changed are waiting for a first checkpoint.',
  'home.metric.new': 'New',
  'home.metric.saved': 'Saved',
  'home.metric.applied': 'Applied',
  'home.metric.interviews': 'Interviews',
  'home.metric.offers': 'Offers',
  'home.metric.progressed': 'Progressed',
  'home.metric.tracking': 'Tracking',
  'home.kind.now': 'right now',
  'home.kind.event': 'since you last looked',
  'home.metricOpen': 'Show {label} in the job list',
  'home.nothing': 'Nothing here today.',
  'home.complete': 'Finish your Career Profile',
  'home.completeLede': 'Each of these makes one part of the product able to answer. There is no '
    + 'completion score, because there is no honest denominator for a career.',
  'home.gap.evidence': 'Nothing is confirmed about you, so every requirement on every posting '
    + 'reads as a gap.',
  'home.gap.residence': 'Where you live is not set, so nothing can tell whether a posting hires '
    + 'there.',
  'home.gap.hiring_scopes': 'No hiring scope is accepted, so the geography gate can never pass and '
    + 'every posting stays unresolved.',
  'home.gap.compensation': 'No pay target is set, so the compensation part of the match scores '
    + 'nothing either way.',
  'home.gap.work': 'No phrases describe the work you want, so there is nothing for a posting '
    + 'to match.',
  'home.openProfile': 'Open your profile',
  'home.openEvidence': 'Add your evidence',
  'kanban.closed': 'Closed',
  'kanban.columnLabel': '{column}, {n} jobs',
  // Dismissing the sentence, never the state.
  'hidden.dismiss': 'Stop showing this notice',
  // The theme control. `theme.js` is a classic script in the head and cannot
  // import the catalogue, so it renders the English and `relabelStaticText`
  // swaps these in.
  'theme.label': 'Colour theme',
  'theme.light': 'Light',
  'theme.dark': 'Dark',
  'theme.lightHint': 'Always use the light theme, whatever this computer prefers.',
  'theme.darkHint': 'Always use the dark theme, whatever this computer prefers.',
  'theme.buttonLabel': '{label} theme. {hint}',
  // The header status line. `mode` is the SEMANTIC key the server already
  // sends; `banner` is its English rendering and is now only a fallback
  // for a mode this catalogue has never heard of.
  'mode.PERSONAL': 'Personal data',
  'mode.DEMO': 'Demo data',
  'mode.UNKNOWN': 'This file does not say what it holds',
  'health.modePersonalHelp': 'Your own jobs. Example data is never shown in their place.',
  'health.modeDemoHelp': 'Made-up jobs for trying things out, kept separate from your own.',
  'health.jobs': '{n} jobs',
  'health.jobsUnknown': 'an unknown number of jobs',
  'health.localModelUp': 'local model {model} up',
  'health.staleScores': '{n} scores predate the filters',
  'health.localModelHelp': 'Everything on this page works with the local model stopped.',
  'health.worthKnowing': '{n} things worth knowing',
  'health.worthKnowingOne': '1 thing worth knowing',
  'health.allWorking': 'Everything is working',
  // Sentences built around a number or a job title. The title itself is
  // the employer's and is substituted, never translated.
  'a11y.matchPercent': 'Search Fit {n} out of 100',
  'a11y.readablePercent': 'posting readable to {n} percent',
  'age.daysAgo': '{n} days ago',
  'age.monthsAgo': '{n} months ago',
  'kanban.appliedOn': 'Applied {date}',
  'kanban.moveLabel': 'Move {title} to another stage',
  'card.statusLabel': 'Application status for {title}',
  // The three phrase groups. The server sends its English beside these keys;
  // the key is what a Portuguese reader gets.
  'prefsCategory.desired': 'Desired signals',
  'prefsCategory.desiredHelp': 'Phrases that add match value when a posting contains them.',
  'prefsCategory.negative': 'Negative signals',
  'prefsCategory.negativeHelp': 'Phrases that reduce the match without excluding the job.',
  'prefsCategory.excluded': 'Hard exclusions',
  'prefsCategory.excludedHelp':
    'Phrases that remove a job from the eligible view. A posting must SAY one of '
    + 'these; silence never excludes.',
  'prefs.addPhrase': 'Add a phrase',
  'prefs.phrasesFor': 'Phrases for {label}',
  // How old the ADVERT is. A separate question from where the candidate
  // is with it, and both used to render in English.
  'a11y.notScored': 'Not scored',
  'badge.readabilityUnknown': 'readability unknown',
  'age.oneMonthAgo': '1 month ago',
  'age.notStated': 'date not stated',
  'age.today': 'today',
  'age.yesterday': 'yesterday',
  'freshness.FRESH': 'Fresh',
  'freshness.RECENT': 'Recent',
  'freshness.AGING': 'Aging',
  'freshness.STALE': 'Stale',
  'freshness.CLOSED': 'Closed',
  'freshness.UNKNOWN': 'Age unknown',
  // The retrieval panel and the source panel. The values substituted into
  // these -- an error a third party returned, a reason the catalogue wrote
  // -- are quoted as they came and never translated.
  'retrieval.lastLooked': 'Last checked {when}',
  'retrieval.runningBoards': 'Running; {done} of {total} boards',
  'retrieval.failedWith': 'Failed: {error}',
  'retrieval.unknownError': 'unknown error',
  'sources.reachOne': 'reaches {boards} employer boards; {producing} of them have returned a '
    + 'posting. A board is one company, not a source.',
  'sources.reachMany': 'reach {boards} employer boards; {producing} of them have returned a '
    + 'posting. A board is one company, not a source.',
  'sources.postingsCount': '{n} postings',
  'sources.boardsCount': '{producing}/{total} boards',
  'sources.catalogueSays': 'The catalogue says: {reason}',
  'sources.lastError': 'Last error recorded: {error}',
  'sources.newestSeen': 'Newest posting first seen: {date}',
  'sources.wouldChange': 'What would change this: {what}',
  'sources.downgraded': 'Downgraded: {why}',
  'profile.disagreeOne': 'Your candidate profile and your search settings disagree about one '
    + 'thing.',
  'profile.disagreeMany': 'Your candidate profile and your search settings disagree about {n} '
    + 'things.',
  'profile.saysValue': '{path} says {value}',
  // The filter chips. A CURRENCY CODE is not translated -- BRL is BRL in
  // every language -- and neither is the text somebody typed into the search
  // box.
  'chip.minScore': 'Search Fit at least {n}',
  'chip.maxScore': 'Search Fit at most {n}',
  'chip.minConfidence': 'Posting detail at least {n}',
  'chip.postedWithinOne': 'Posted in the last 1 day',
  'chip.postedWithin': 'Posted in the last {n} days',
  'chip.contains': 'Contains “{text}”',
  'chip.minSalary': 'At least {amount} a year',
  'chip.minSalaryCurrency': 'At least {amount} {currency} a year',
  'chip.removeFilter': 'Remove filter {label}',
  'chip.removeValue': 'Remove {label} {value}',
  'filters.section.advancedHelp': 'How this tool classified each posting. Useful for checking its work. ',
  // The table. Job titles, company names and tool names are substituted
  // verbatim: they are the employer's words and the configuration's, and
  // neither is ours to translate.
  'table.sortBy': 'Sort by {column}',
  'table.groupTitle': 'Published as {n} postings: {places}',
  'table.groupTitleBare': 'Published as {n} separate postings',
  'table.groupLabel': 'stands for {n} postings',
  'table.openDetails': 'Open details for {title} at {company}',
  'table.andMoreTools': 'and {n} more: {names}',
  'table.markApplied': 'Mark {title} as applied',
  'table.appliedDateFor': 'Applied date for {title}',
  // The results header, the empty state's advice and the one-line
  // confirmations. `filterWord.*` names each filter INSIDE a sentence, which
  // is a different word from its heading in the rail.
  'confirm.clearApplied': 'Clear the applied date for {title}?\n\n'
    + 'This records that no application was ever sent. The status stays as '
    + 'it is. It cannot be undone.',
  'count.oneRole': '1 role',
  'count.roles': '{n} roles',
  'count.oneJob': '1 job',
  'count.jobs': '{n} jobs',
  'count.oneRepostFolded': '1 repost folded in',
  'count.repostsFolded': '{n} reposts folded in',
  'count.showingRange': 'showing {from} to {to}',
  'count.oneFilterActive': '1 filter active',
  'count.filtersActive': '{n} filters active',
  'count.noFilters': 'no filters',
  'advice.matchBelow': 'asking for a Match below {n}',
  'advice.detailBelow': 'asking for a Detail below {n}',
  'advice.searchingLess': 'searching for less than “{text}”',
  'advice.eligibility': 'allowing jobs that did not say whether you could take them',
  'advice.removingFilter': 'removing the {what} filter',
  'advice.turningOff': 'turning off “{what}”',
  'advice.salaryLess': 'asking for less than {amount} a year',
  'advice.postedMoreThanOne': 'including jobs posted more than 1 day ago',
  'advice.postedMoreThan': 'including jobs posted more than {n} days ago',
  'advice.tryClearing': 'Try clearing the filters.',
  'advice.try': 'Try {list}.{why}',
  'advice.or': 'or',
  'advice.eligibilityWhy': 'Most postings never say where a company will hire, so most of them '
    + 'cannot be confirmed either way. That is not the same as being ruled out.',
  'empty.checking': 'Checking what is in your database...',
  'empty.unscored': 'There are {n} jobs here, but none of them have been scored against your '
    + 'current preferences. That happens when you change what you are looking '
    + 'for: the old scores answered the old question. Recalculating happens on'
    + 'your own computer, costs nothing and takes a few minutes.',
  'empty.noneCollected': 'No jobs have been collected yet. Find jobs checks public job boards for you.',
  'empty.notScoredYet': 'These jobs have not been scored yet.',
  'empty.nothingYet': 'There is nothing here yet.',
  'rescore.progress': 'Recalculating: {done} of {total}',
  'rescore.working': 'Recalculating...',
  'flash.movedTo': 'Moved to {status}.',
  'flash.appliedDateSet': 'Applied date set to {date}.',
  'flash.appliedDateCleared': 'Applied date cleared.',
  'filterWord.provider': 'job board',
  'filterWord.role_class': 'job title',
  'filterWord.signal': 'matched wording',
  'filterWord.technology': 'tool',
  'filterWord.status': 'progress',
  'filterWord.fit_band': 'match band',
  'filterWord.country': 'country',
  'filterWord.region': 'region',
  'filterWord.worksite': 'office or remote',
  'filterWord.employment_type': 'contract type',
  'filterWord.contract_regime': 'Brazilian contract',
  'filterWord.salary_currency': 'currency',
  'filterWord.salary_period': 'pay period',
  'filterWord.keyword': 'must-mention word',
  'filterWord.exclude_keyword': 'must-not-mention word',
  'filterWord.saved_only': 'only ones I saved',
  'filterWord.has_salary': 'only ones that state a salary',
  'filterWord.remote_only': 'remote only',
  'filterWord.enriched_only': 'only ones the local model has read',
  'filterWord.latam_only': 'open to Latin America',
  'filterWord.worldwide_only': 'says it hires worldwide',
  // The job drawer. Every heading, every lede and every sentence the product
  // writes about a posting. What is NOT here: the description, the title,
  // the company name and every evidence quote -- those are the employer's
  // words and ADR-0002 makes a translated quote not a quote.
  'drawer.scoredOutOf': 'Search Fit {score} out of 100: {howClose} for the work you said you '
    + 'want. Confirmed career evidence is assessed separately under Evidence / Readiness.',
  'drawer.closeStrong': 'strong search fit',
  'drawer.closeReasonable': 'reasonable search fit',
  'drawer.closePartial': 'partial search fit',
  'drawer.closeWeak': 'weak search fit',
  'drawer.pickedUpOne': 'It picked up 1 thing you are looking for, quoted below.',
  'drawer.pickedUp': 'It picked up {n} things you are looking for, quoted below.',
  'drawer.readableHigh': 'The posting is detailed, so there was a lot to go on.',
  'drawer.readableMid': 'The posting is only moderately detailed, so some of this is uncertain.',
  'drawer.readableLow': 'The posting is thin, so there was little to go on and this score is '
    + 'uncertain.',
  'drawer.wouldRuleOut': 'Something in the posting would rule you out. See below.',
  'drawer.secWhyMatches': 'Why this fits your search',
  'drawer.secStrengths': 'What this job has that you asked for',
  'drawer.secGaps': 'What the posting did not spell out',
  'drawer.secGates': 'Could you take this job',
  'drawer.secConfidence': 'How much the posting told us',
  'drawer.secUnknowns': 'Never mentioned in the posting',
  'drawer.secSignals': 'Tools and skills it names',
  'drawer.secPay': 'Pay and contract',
  'drawer.secDescription': 'Description',
  'drawer.secHistory': 'Status history',
  'drawer.secEnrich': 'What the local model saw',
  'drawer.secPlaces': 'Posted in several places',
  'drawer.secProvenance': 'Where we found this',
  'drawer.allQuoted': 'Every line below is quoted from the posting itself.',
  'drawer.someQuoted': '{n} of these are quoted from the posting itself. The rest matched on '
    + 'something the posting implies rather than says.',
  'drawer.noneQuoted': 'None of these had a line worth quoting. They matched on something the '
    + 'posting implies rather than says.',
  'drawer.gapsLede': 'These are not marks against the job. They are things the employer left '
    + 'out, and they are why the detail number is not higher.',
  'drawer.saved': '★ Saved',
  'drawer.save': '☆ Save',
  'drawer.appliedOn': 'Applied on {date}. ',
  'drawer.appliedKept': 'Kept while the status is {status}, which means an application was sent.',
  'drawer.confirmClearApplied': 'Clear the applied date ({date}) for {title}?\n\n'
    + 'This records that no application was ever sent. The status stays as '
    + 'it is. It cannot be undone.',
  'drawer.cappedAt': 'Capped at {points}: the posting matched more than this part of the score '
    + 'can award.',
  'drawer.gatesLede': 'Three answers, not two. A posting that never says where a company hires '
    + 'is an open question, not a no.',
  'drawer.covered': 'The posting covered {awarded} of the {total} things we look for. This is '
    + 'about the posting, not about you.',
  'drawer.salaryUnstated': 'Salary not stated in this posting',
  'drawer.notStated': 'Not stated',
  'drawer.noUrl': 'No URL recorded',
  'drawer.statusChange': '{from} → {to}',
  'drawer.configuredEndpoint': 'the configured endpoint',
  'drawer.noLocalModel': 'No local model is configured, so there is nothing to ask.',
  'drawer.localModelSilent': 'The local model was contacted at {endpoint} and did not answer. Start it '
    + 'and reload this page. Everything else here works without it.',
  'drawer.localModelReady': '{model} answered at {endpoint}. A run still takes a few minutes.',
  'drawer.theLocalModel': 'The local model',
  'drawer.theLocalModelLower': 'the local model',
  'drawer.localModelUntried': 'The local model has not been asked about this job yet. Opening a page '
    + 'never asks it. The first run may take a few minutes, and you can cancel'
    + 'it.',
  'drawer.asking': 'Asking {model}: this can take a few minutes.',
  'drawer.doneIn': 'Done in {seconds}s.',
  'drawer.localModelFailed': 'The local model could not be reached: {error}',
  'drawer.placesLede': '{company} published this role as {n} separate postings, one per '
    + 'location. Each has its own link at the provider; this drawer describes'
    + 'the highest-scoring one.',
  'drawer.morePlaces': '{n} more not listed here',
  'gate.PASS': 'Nothing in the way',
  'gate.FAIL': 'Rules you out',
  'gate.UNRESOLVED': 'The posting did not say',
  'value.notStated': 'not stated',
  // What the BOARD printed as the employment type, normalised.
  // `employment.*` is a different question -- the engagement this system
  // infers -- and `contract.*` a third: the regime the candidate accepts.
  // CLT and PJ name Brazilian legal instruments, the same word in both.
  'jobType.FULL_TIME': 'Full-time',
  'jobType.PART_TIME': 'Part-time',
  'jobType.CONTRACT': 'Contract',
  'jobType.TEMPORARY': 'Temporary',
  'jobType.INTERNSHIP': 'Internship',
  'jobType.VOLUNTEER': 'Volunteer',
  // The last of the product's own English. What is deliberately NOT here: a
  // job title, a company name, a description, an evidence quote, a country
  // code and a currency code.
  'empty.headline': 'Nothing to show here.',
  'empty.filtered': 'Nothing matches what you are asking for.',
  'rescore.couldNotStart': 'Could not start. Try again',
  'flash.notesSaved': 'Notes saved.',
  'order.everyPosting': 'Every posting',
  'order.oneRowHelp': 'Employers often post one job once per city. This shows each job once, '
    + 'and the card says how many places it appeared in.',
  'order.everyPostingHelp': 'Shows every posting separately, including the ones an employer repeated '
    + 'for each location.',
  'order.lowestFirst': 'Lowest first',
  'order.highestFirst': 'Highest first',
  'order.lowestFirstLabel': 'Showing the lowest first. Activate to show the highest first.',
  'order.highestFirstLabel': 'Showing the highest first. Activate to show the lowest first.',
  'absent.untitled': 'Untitled posting',
  'absent.company': 'an unnamed company',
  'absent.companyStated': 'Company not stated',
  'absent.source': 'Source not recorded',
  'absent.place': 'Location not stated',
  'column.score': 'Search Fit',
  'column.confidence': 'Posting detail',
  'column.company': 'Company',
  'column.title': 'Title',
  'column.location': 'Location',
  'column.source': 'Job board',
  'column.technologies': 'Tools',
  'column.salary': 'Salary',
  'column.contract': 'Contract',
  'column.posted': 'Posted',
  'column.freshness': 'Age',
  'column.eligibility': 'Can you take it',
  'column.status': 'Progress',
  'column.applied': 'Applied',
  'column.applied_at': 'Applied date',
  'column.saved': 'Saved',
  'column.link': 'Link',
  'kanban.emptyShortlisted': 'Nothing marked interesting yet. Set a job to Interested in Cards or '
    + 'Table.',
  'kanban.emptyToApply': 'Nothing queued to apply to.',
  'kanban.emptyApplied': 'No applications sent yet.',
  'kanban.emptyInterview': 'No interviews in progress.',
  'kanban.emptyOffer': 'No offers.',
  'kanban.emptyHired': 'Nothing here yet.',
  'kanban.emptyClosed': 'Nothing closed.',
  'facet.company': 'Company',
  'facet.provider': 'Job board',
  'facet.technology': 'Tools named in the posting',
  'facet.worksite': 'Office, hybrid or remote',
  'facet.region': 'Part of the world, as posted',
  'facet.country': 'Country, as posted',
  'facet.seniority': 'Level',
  'facet.employment_type': 'Contract type',
  'facet.contract_regime': 'Brazilian contract',
  'facet.salary_currency': 'Currency',
  'facet.salary_period': 'Paid per',
  'facet.status': 'Your progress',
  'facet.eligibility': 'Could you take it',
  'facet.role_class': 'How the job title reads',
  'facet.fit_band': 'Search Fit band',
  'facet.signal': 'Everything the posting matched',
  'filters.includeIneligibleHelp': 'Off by default. These are jobs whose posting states something you do not '
    + 'meet, such as a country, a work permit or required travel. Jobs that'
    + 'simply did not say are always shown.',
  'filters.includeOffTargetHelp': 'Off by default. These are jobs where none of the things you said you are '
    + 'looking for turned up at all. Nothing is wrong with them; they are a'
    + 'different kind of work. A LOW match is not this and is always shown.',
  'filters.matchAtLeast': 'Search Fit at least',
  'filters.detailAtLeast': 'Posting detail at least',
  'stage.fetched': 'Fetched',
  'stage.active': 'Active',
  'stage.normalised': 'Readable',
  'stage.scored': 'Scored',
  'stage.deduplicated': 'Roles',
  'stage.eligible': 'Eligible',
  'stage.recommended': 'Recommended',
  'stage.fetchedHelp': 'Every posting ever collected, including closed ones.',
  'stage.activeHelp': 'Still open at the source.',
  'stage.normalisedHelp': 'The full job description arrived and can be read.',
  'stage.scoredHelp': 'Measured against the preferences you have now.',
  'stage.deduplicatedHelp': 'One per company and title; an employer often posts a role per city.',
  'stage.eligibleHelp': 'Not explicitly disqualified. Silence is not a disqualification.',
  'stage.recommendedHelp': 'At or above the shortlist score.',
  'retrieval.retrieving': 'Retrieving…',
  'retrieval.never': 'Never retrieved from this interface.',
  'retrieval.finished': 'Finished',
  'retrieval.cancelled': 'Cancelled; the database is intact',
  'prefs.savedRescore': 'Saved. Your matches are now out of date and need recalculating.',
  'prefs.saved': 'Saved.',
  'profile.notSaved': 'That change was not saved.',
  // WHAT SAVING DOES, from the reader's side. This used to say "Changing any of
  // these rewrites your own settings and needs a rescore afterwards", which is
  // three pieces of implementation -- a settings file, a rescore, and the fact
  // that both exist -- in a sentence that only had to answer "and then what".
  'profile.editLeadLocal':
    'Tell us what makes sense for you right now. We use these answers to put the '
    + 'closest jobs first. Your recommendations update when you save.',
  'profile.editLeadFirst':
    'Tell us what makes sense for you right now. We use these answers to put the '
    + 'closest jobs first.',
  'badge.confidenceHelp': 'How much the employer actually wrote down. This is NOT your chance of '
    + 'being hired. A short posting scores low here however good the job is.',
  'badge.matchHelp': 'How close this job is to the work you want, worked out from the words in '
    + 'the posting and your search model. Your confirmed career evidence does not enter this number.',
  'badge.eligibilityUnresolvedHelp': 'This posting never said where the company can hire. Saying nothing is '
    + 'not the same as saying yes, so this stays an open question.',
  'badge.eligibilityHelp': 'Whether you could take this job at all. A separate question from how '
    + 'well it matches.',
  'badge.notMeasured': 'not measured',
  'prominence.PRIMARY': 'central to this job',
  'prominence.SECONDARY': 'used regularly',
  'prominence.INCIDENTAL': 'mentioned once',
  'prominence.OTHER': 'mentioned',
  'period.YEAR': 'Year',
  'period.MONTH': 'Month',
  'period.WEEK': 'Week',
  'period.DAY': 'Day',
  'period.HOUR': 'Hour',
  'period.NOT_STATED': 'Not stated',
  'region.LATAM': 'LatAm',
  'region.EMEA': 'EMEA',
  'region.APAC': 'APAC',
  'region.AMERICAS': 'Americas',
  'region.NORTH_AMERICA': 'North America',
  'region.WORLDWIDE': 'Worldwide',
  'access.ATS_STRUCTURED': 'ATS structured',
  'access.AGGREGATOR_API': 'Aggregator API',
  'access.ATS_HTML': 'ATS HTML',
  'access.MANUAL_IMPORT': 'Manual import',
  'sources.oneFamily': '1 connector family',
  'sources.families': '{n} connector families',
  // The card's accessible name. The title and the company are the employer's
  // words and are substituted, never translated.
  'card.openLabel': '{title} at {company}. Press Enter to open details.',
  // The screen-reader label on the sort control.
  'order.sortLabel': 'Order results by',
  // Hiding one posting by hand. The fourth reason a job is off the screen,
  // and the only one she chose: distinct from an employer ruling her out,
  // from her search calling it other work, and from a rejection.
  'card.hide': 'Hide',
  'card.unhide': 'Put back',
  'card.hideLabel': 'Hide {title} from these lists',
  'card.unhideLabel': 'Put {title} back into these lists',
  'action.undo': 'Undo',
  'flash.hidden': 'Hidden. It stays in the database and nothing else changed.',
  'flash.unhidden': 'Back in the list.',
  'hidden.byYou': '{count} hidden because you set them aside.',
  'hidden.byYouShowing': 'Showing the ones you set aside, alongside the rest.',
  'hidden.byYouReveal': 'Show them too',
  'hidden.restoreView': 'See only what you set aside',
  // One of them, said in the singular. Portuguese inflects the noun, the
  // article and the pronoun together, so a count spliced into a plural
  // sentence reads as broken grammar rather than as a number.
  'hidden.eligibilityOne': '1 hidden, because it states a requirement you do not meet: a country, a '
    + 'work permit, a security clearance or something similar.',
  'hidden.offTargetOne': '1 hidden as a different kind of work from the one you described. Nothing '
    + 'is wrong with it: it did not match what you said you are looking for.',
  'hidden.byYouOne': '1 hidden because you set it aside.',
  // Contextual help, where the word is. It replaced a permanent block under
  // the toolbar that was nowhere near either number.
  'help.about': 'What {what} means',
  'help.theseNumbers': 'these numbers',
  'help.eligibility': 'Whether you could take this job at all is a THIRD answer, kept apart '
    + 'from both numbers. An employer states a requirement or says nothing;'
    + 'silence is not permission and it is not a refusal.',
  // Exploring. The one status a person sets before deciding anything, and
  // the one most likely to be mistaken for a signal about the job.
  'filters.preset.interested': 'The ones I am interested in',
  'filters.preset.interestedHelp': 'Only jobs you marked Interested. Saying so is a note to yourself: it '
    + 'moves no score, changes no eligibility answer and tells no employer'
    + 'anything.',
  // The questions this product cannot answer for her, asked where their
  // absence is visible. Not a wizard: every screen works with none of them
  // answered, and each says what stops working without it.
  'tags.remove': 'Remove {value}',
  'tags.noWeight': 'These are stored as they are. Nothing typed here becomes a phrase the '
    + 'matcher looks for, and nothing here changes a score.',
  'ask.start': 'Answer these now',
  'ask.step': 'Question {n} of {of}',
  'ask.save': 'Save and continue',
  'ask.skip': 'Skip this one',
  'ask.close': 'Close',
  'ask.takeMeThere': 'Take me there',
  'ask.finished': 'That is everything this could ask. You can change any of it on the '
    + 'Career Profile page.',
  'ask.nothingToAsk': 'Nothing is missing that a question could fill in.',
  'ask.nothingTyped': 'Nothing was typed, so there is nothing to save.',
  'ask.countriesHint': 'Two letters each, one at a time. A country here says an employer hiring '
    + 'specifically there can hire you. It creates no scoring weight and it is'
    + 'not a preference.',
  'ask.residence': 'Where do you live?',
  'ask.residenceWhy': 'It is the fact the geography gate compares a posting against. Without '
    + 'it, a posting saying where it hires cannot be checked against anything.',
  'ask.scopes': 'Who is allowed to hire you?',
  'ask.scopesWhy': 'This is a different question from where you live, and it is the one that '
    + 'decides eligibility. With none of these set, no posting can ever pass '
    + 'the geography gate: the whole corpus stays unresolved.',
  'ask.pay': 'What are you aiming for?',
  'ask.payWhy': 'Without a target the compensation part of every match score has nothing '
    + 'to compare against and awards nothing. A posting that states a salary is'
    + 'still shown; it just cannot be scored on it.',
  'ask.evidence': 'What have you actually done?',
  'ask.evidenceWhy': 'Until something is confirmed, every requirement on every posting reads '
    + 'as a gap. Reading your CV proposes claims; you confirm, correct or'
    + 'reject each one, and nothing is believed because it was read.',
  'ask.work': 'What kind of work are you looking for?',
  'ask.workWhy': 'The phrases you look for are what a posting is matched against. Unlike '
    + 'the answers above, editing these DOES change how every posting is'
    + 'scored, so they live on their own screen and say so.',
  // The Career Profile page. The SERVER composed every heading, lede and
  // product-authored row label on it, so a Portuguese reader got an English
  // page inside a translated shell. A signal's own label and every stored
  // value stay exactly as the configuration wrote them.
  'profileSection.about': 'About your search',
  'profileSection.aboutLead': 'The name this search goes by.',
  'profileSection.signals_desired': 'Desired signals',
  'profileSection.signals_desiredLead': 'Phrases that add match value when a posting contains them. ',
  'profileSection.signals_negative': 'Negative signals',
  'profileSection.signals_negativeLead': 'Phrases that reduce the match without excluding the job.',
  'profileSection.signals_excluded': 'Hard exclusions',
  'profileSection.signals_excludedLead': 'Phrases that remove a job from the eligible view. A posting must SAY one '
    + 'of these; silence never excludes.',
  'profileSection.place': 'Where you can work',
  'profileSection.placeLead': 'A posting whose stated hiring region includes none of these fails the '
    + 'geography gate. A posting '
    + 'that says nothing stays unresolved -- silence is not a rejection.',
  'profileSection.blockers': 'What rules a job out',
  'profileSection.blockersLead': 'Things an employer states that you cannot meet. Each one has to quote '
    + 'the sentence it fired on, so nothing is ruled out on a guess.',
  'profileSection.shape': 'The shape of the work',
  'profileSection.shapeLead': 'Preferences, not gates. A posting that disagrees loses points and stays '
    + 'visible.',
  'profileSection.pay': 'Pay',
  'profileSection.payLead': 'Worth at most five points, and never a reason to rule a job out. A '
    + 'posting that states no salary is not penalised for it.',
  'profileRow.searchName': 'You call this search',
  'profileRow.youAreIn': 'You are in',
  'profileRow.hiredFrom': 'You can be hired from',
  'profileRow.workModels': 'Ways of working you accept',
  'profileRow.contractsPreferred': 'Contracts you prefer',
  'profileRow.contractsUnwanted': 'Contracts you do not want',
  'profileRow.levels': 'Levels you are looking for',
  'profileRow.travel': 'Travel you will accept',
  'profileRow.aimingFor': 'You are aiming for',
  'profileRow.crossCurrency': 'Cross-currency comparison',
  'profileValue.travelUpTo': 'up to {pct}% of the time',
  // QUESTIONS, not field names. Each one is what a person would be asked out
  // loud, and each has a `fieldHelp.*` sentence under it saying what the
  // answer is USED FOR -- because "Most travel you would accept" above the
  // number 15 was a label with no unit and no consequence attached to it.
  'field.work_models': 'How do you feel about each way of working?',
  'field.require_remote': 'Only show me fully remote roles',
  'field.contract_preferred': 'Which working arrangements work for you?',
  'field.contract_unwanted': 'Any of those you would prefer less?',
  'field.seniority_preferred': 'Which levels make sense for you right now?',
  'field.seniority_excluded': 'Any levels you would rather not see?',
  'field.travel_max_pct': 'How much work travel would you be comfortable with?',
  'field.compensation_target': 'How much would you like to earn each month?',
  'field.compensation_currency': 'In which currency?',
  'field.candidate_country': 'Where do you currently live?',
  'field.eligible_scopes': 'Which regions can you work in?',
  'field.eligible_countries': 'Which countries can employ you directly?',

  // WHAT THE ANSWER DOES. One sentence, in the second person, naming the
  // consequence rather than the mechanism.
  'fieldHelp.work_models': 'Prefer adds a little to a job\'s fit and rather avoid takes it away. Never show also '
    + 'hides those jobs from Discover, with one click to see them again. None of this changes '
    + 'where you can be hired: remote does not mean a company can hire you anywhere.',
  'fieldHelp.require_remote': 'Worked out from your answers: on when hybrid and on-site are both never shown.',
  'fieldHelp.contract_preferred':
    'Works for me adds a little to a job\'s fit and rather not takes it away. Many postings do '
    + 'not say, and then nothing changes.',
  'fieldHelp.contract_unwanted': 'Arrangements you would rather not have. A job that offers one loses a little fit.',
  'fieldHelp.seniority_preferred': 'Kept for your own reference; it does not change scores yet. To keep a level out of '
    + 'Discover, use the next question.',
  'fieldHelp.seniority_excluded':
    'Jobs at these levels will not be recommended to you. Anything you are already following stays where it is.',
  'fieldHelp.travel_max_pct': 'Kept for your own reference; it does not change scores or hide jobs yet.',
  'fieldHelp.compensation_target':
    'Use whatever you are aiming for today. Jobs that do not say what they pay still '
    + 'show up as normal.',
  'fieldHelp.compensation_currency': 'Currency',
  'fieldHelp.candidate_country':
    'This helps us check which jobs can hire someone where you are.',
  'fieldHelp.eligible_scopes':
    'We use this to check whether the region a job hires in includes you.',
  'fieldHelp.eligible_countries':
    'Countries where a company could put you on their payroll without going '
    + 'through anyone else. Leave it empty if there are none.',

  // The travel readout. A percentage with a word beside it, because 15 on its
  // own answers "fifteen what".
  'travel.none': 'No travel',
  'travel.occasional': 'Occasional travel',
  'travel.some': 'Some travel',
  'travel.frequent': 'Frequent travel',
  'travel.heavy': 'Travel is a big part of the job',
  'travel.readout': '{pct}% of your working time · {band}',

  // The groups the questions are asked in.
  'choiceGroup.where': 'Where and how you want to work',
  'choiceGroup.whereLede': 'Where you are, and where a job can reach you.',
  'choiceGroup.what': 'The work you are looking for',
  'choiceGroup.whatLede': 'Level, contract and how much travel you would take on.',
  'choiceGroup.pay': 'Pay',
  'choiceGroup.payLede': 'What you are aiming for each month.',
  'region.EUROPE': 'Europe',
  // The Save button says how much is pending, so pressing it is not a guess
  // about what it will write.
  'profile.saveOne': 'Save 1 change',
  'profile.saveMany': 'Save {n} changes',
  // Provenance, never quality. An excerpt from an aggregator and a genuinely
  // brief posting are the same length and opposite facts, and only one of
  // them has more to say somewhere else.
  'content.PARTIAL_CONTENT': 'Excerpt only',
  'content.METADATA_ONLY': 'No description stored',
  'content.partialHelp': 'This source returns a short excerpt and offers no way to fetch the rest, '
    + 'so what is scored here is part of the posting. The employer\'s own page '
    + 'has the whole of it. Nothing about the score is adjusted for this: the'
    + 'text was read exactly as it arrived.',
  'content.metadataHelp': 'No description text was stored for this posting, so there was nothing to '
    + 'read. The link still goes to the employer.',
  'facet.content_completeness': 'How much of the posting we have',
  // Taking the selection away. The workflow ends at the employer's own form,
  // so the last useful thing this product can do is hand her the sentences
  // she already confirmed.
  'resume.copy': 'Copy this selection',
  'resume.copied': 'Copied. It is your own sentences, in this order, with the gaps.',
  'resume.copyFailed': 'Your browser would not let this page write to the clipboard. The text is '
    + 'selectable below.',
  'resume.exportLede': 'What you would take away: these sentences, in this order, and the gaps '
    + 'underneath them. Nothing in it was written by this program about you.',
  // A slider at zero is not asking for anything, which is different from
  // asking for zero.
  'filters.anyValue': 'any',
  // Server-composed English on the profile page. The ownership rule is
  // quoted by `doctor` too, which is why the server keeps sending the English
  // beside the key.
  'profile.bothDescribeYou': 'Both files describe you. Where they disagree, the matcher reads the '
    + 'search settings -- and the disagreement is shown rather than resolved.',
  'profile.ownershipRule': 'Candidate Profile owns facts about the person; search configuration owns '
    + 'the matching machinery.',

  // -- THE CAREER PROFILE, WHICH IS NOW A PERSON AS WELL AS A SEARCH ------
  //
  // Three of the four tabs describe what somebody has CONFIRMED about their
  // own career and one describes what they are looking for. The copy has to
  // keep that line visible: nothing on the first three was decided by this
  // program, and nothing on the fourth is a fact about the past.
  'profileTab.overview': 'Overview',
  'profileTab.experience': 'Experience',
  'profileTab.skills': 'Skills',
  'profileTab.preferences': 'Preferences',
  'profileFold.reading': 'How a job is read',
  'profileFold.phrases': 'Phrase groups',

  'profile.glanceHeading': 'What you have confirmed',
  'profile.glanceLead': 'Facts you personally stood behind. Nothing here was confirmed for you.',
  'profile.countWork': 'Things you have done',
  'profile.countSkills': 'Skills and tools',
  'profile.countQuals': 'Certificates and study',
  'profile.recentWork': 'Most recent work',
  'profile.yourSkills': 'Your skills',
  'profile.andMoreSkills': 'And {n} more, under Skills.',
  // The expectation this page would otherwise set, corrected where it is set.
  // Answering three hundred proposals is an hour of somebody's evening and
  // the reasonable thing to expect afterwards is that the job list changes.
  // It will not: no scoring module can read a claim, and a test fails if one
  // ever learns to.
  'profile.confirmedNote': 'Confirming something does not move a recommendation. Your evidence is '
    + 'read when you prepare an application, and it is where the words in that application come '
    + 'from.',
  'profile.openEvidence': 'Open your career evidence',
  'profile.nothingConfirmed': 'Nothing confirmed yet',
  'profile.nothingConfirmedLead': 'This is where the work you have done appears, once you have '
    + 'said which parts of it are true. Nothing is confirmed on your behalf.',

  'profile.employerNotStated': 'Employer not stated',
  // The dates as the DOCUMENT gave them. Never reformatted into a month name:
  // the stored value is what a CV or an export said, and a screen that prints
  // "June 2024" over a file that says "2024-06" has started interpreting.
  'profile.periodRange': '{start} to {end}',
  'profile.periodFrom': 'From {start}',
  'profile.periodUntil': 'Until {end}',
  'profile.periodUnknown': 'Dates not stated',
  'profile.showAllLines': 'Show all {n}',
  // NOT "{n} earlier roles". A group is one employer and one period, and
  // her two documents produce twenty-three of them over four companies, so
  // counting them as roles would state a career she never claimed.
  'profile.showOlderRoles': 'Show {n} more',
  'profile.experienceLead': 'Grouped by employer and period, most recent first. These are your '
    + 'own confirmed sentences, word for word.',
  'profile.experienceNote': 'A CV and a LinkedIn export often name the same employer '
    + 'differently, and nothing here merges them: deciding that two names are one company is '
    + 'yours to say. You can correct a name under Career Evidence.',
  'profile.skillsHeading': 'Skills and tools',
  // Said out loud because the design shows a bar with a number on the end of
  // it, and somebody who has seen that will look for one here.
  'profile.skillsLead': 'Each of these is a word you confirmed. There is no proficiency score, '
    + 'because nothing in this product measures one.',
  'profile.qualsHeading': 'Certificates and study',
  'profile.educationHeading': 'Education',
  'profile.certIssued': 'Issued {date}',
  'profile.certExpires': 'Expires {date}',
  'profile.certNoExpiry': 'No expiration',
  'profile.certCredential': 'Credential ID: {id}',

  // -- CAREER EVIDENCE: WHAT NEEDS YOU, THEN WHAT YOU HAVE ----------------
  //
  // The page used to open on its own machinery and answer "where did this
  // come from" before "is there anything for me to do". These are the words
  // of the block that answers the first question first.
  'attend.waiting': '{n} items need your review',
  'attend.oneWaiting': '1 item needs your review',
  'attend.waitingLede': 'Career Agent read your documents and wrote down what it thinks you '
    + 'have done. None of it counts until you say so.',
  'attend.continue': 'Review now',
  'attend.clear': 'You are all caught up',
  'attend.clearLede': 'Everything read out of your documents has been answered. What you '
    + 'confirmed is below.',
  'attend.nothingYet': 'Nothing has been read out of your documents yet. Add a CV under '
    + 'Sources and imports, or write something down yourself.',
  'attend.summary': '{confirmed} confirmed, {retired} set aside',

  // An import, named by WHAT IT WAS READ FROM and WHEN. Two of them were on
  // screen called `career-agent-import.json`, and the only thing telling them
  // apart was a badge. A filename is not an identity when two things share it.
  'intake.importTitle': '{documents}, read on {when}',
  'intake.importFrom': 'From {file}. {who}',
  'intake.reviewDone': 'Review complete. {n} reviewed.',
  'intake.needReview': '{n} still need review',
  'intake.nothingIn': 'Nothing was read out of it',
  'intake.conflictCount': '{n} disagreements to settle first',
  'intake.historyHeading': 'Earlier imports ({n})',

  'ledger.details': 'Details',
  'ledger.twoNames': 'A CV and a LinkedIn export often name the same employer '
    + 'differently, and nothing here merges them: deciding that two names are one '
    + 'company is yours to say.',
  'ledger.cameFrom': 'Where this came from: {source}',
  'ledger.cameFromAt': 'Where this came from: {source}, {employer}',
  'ledger.employerNotStated': 'Employer not stated',
  'ledger.groupWhen': '{who}, {start} to {end}',
  'ledger.groupFrom': '{who}, from {start}',
  'ledger.filterLabel': 'Which evidence to show',
  // Three states, because three is how many a claim in this ledger has.
  // Anything about being unreviewed or in conflict belongs to an IMPORT and
  // is answered before a claim ever gets here.
  'ledger.state.ALL': 'All',
  'ledger.state.CONFIRMED': 'Confirmed',
  'ledger.state.ASIDE': 'Set aside',
  // The same provenance as `ledger.source.*`, short enough to sit at the end
  // of a row without competing with the sentence in front of it.
  'ledger.sourceShort.RESUME': 'CV',
  'ledger.sourceShort.LINKEDIN': 'LinkedIn',
  'ledger.sourceShort.SELF_ATTESTED': 'Added by you',
  'ledger.sourceShort.DOCUMENT': 'Document',

  // -- WHEN SOMETHING GOES WRONG ------------------------------------------
  //
  // Written for whoever is reading the screen, which is the whole point of
  // them. Three things every one of these does: it says what happened in
  // words nobody has to decode, it says whether anything was changed, and it
  // gives one thing to try. No status number, no filename, no command to run
  // in a terminal she may never have opened.
  //
  // `api.js` picks between these and the server's own sentence, and the
  // server decides which by marking a message as written for a reader.
  'error.serverFault': 'Something went wrong inside Career Agent and this change was not '
    + 'made. Everything already saved is safe. Try again, and if it keeps happening, close '
    + 'Career Agent and start it once more.',
  'error.notThere': 'That is not here any more. Reload the page to see what is.',
  'error.refused': 'That request was turned away because it did not come from this page. '
    + 'Reload and try again.',
  'error.tooBig': 'That file is too large to send. Try a smaller one.',
  'error.didNotWork': 'That did not work, and nothing was changed. Try again in a moment.',
  'error.unreadable': 'The answer came back unreadable, so nothing was changed. Try again.',
  'error.notAnswering': 'Career Agent is not answering. It runs on your own computer, so this '
    + 'usually means it was closed. Start it again and reload this page.',
  'error.cancelled': 'Cancelled. Nothing was changed.',
  'error.localModelOff': 'The local model is not running. Everything else on this page works '
    + 'without it, so nothing else is affected.',
  // The dev harness. It has postings and no candidate, and inventing one
  // would put a made-up career into the repository.
  'error.noCandidateHere': 'This preview has job postings and nobody to match them against. '
    + 'Open the real Career Agent to add your CV, confirm what you have done and prepare an '
    + 'application.',
  'error.noFixture': 'The preview data could not be loaded.',
  'error.noImportHere': 'Adding a posting by hand is switched off in this preview.',
  'setup.progress': 'Step {n}',
  'setup.later': 'Do this later',
  'setup.back': 'Back',
  'setup.skip': 'Skip for now',
  'setup.continue': 'Continue',
  'setup.saving': 'Saving…',
  'setup.pickOne': 'Choose at least one, or choose Skip for now.',
  'setup.welcome.title': 'Let’s set up your job search',
  'setup.welcome.why': 'A few short questions, one at a time. Each answer is saved as you go, and you can change any '
    + 'of them later in Settings.',
  'setup.welcome.point1': 'Describe the kind of work you want, in your own words.',
  'setup.welcome.point2': 'Say where you live and where companies can hire you, so Career Agent can tell which jobs '
    + 'are open to you.',
  'setup.welcome.point3': 'Then find your first jobs from public job boards.',
  'setup.welcome.privacy': 'Everything stays on this computer. Nothing about you is sent to job sites, and Career '
    + 'Agent never applies to a job for you.',
  'setup.welcome.start': 'Start',
  'setup.work.title': 'What work do you want to do next?',
  'setup.work.why': 'Career Agent reads the whole job description, not only the job title, so describe the work '
    + 'itself.',
  'setup.work.label': 'Kinds of work, one per line',
  'setup.work.example': 'For example: customer onboarding, payroll administration, data analysis.',
  'setup.work.skillsLabel': 'Tools or methods you want to use next, one per line (optional)',
  'setup.work.note': 'Choose these on purpose. They describe what you want to find, are not copied from your CV '
    + 'and are not a claim about your experience.',
  'setup.roles.title': 'Do you have specific roles in mind?',
  'setup.roles.why':
    'Optional. Name a few job titles and Career Agent will also search for them, and for titles that mean '
    + 'the same work. They are search anchors, not limits.',
  'setup.review.roles': 'Roles in mind',
  'roles.label': 'Roles in mind',
  'roles.hint': 'Up to {n} job titles, in your own words. Press Enter after each one.',
  'roles.placeholder': 'For example: Account Executive',
  'roles.suggestions': 'From your Career Profile. Add one only if you want that role again:',
  'roles.add': 'Add {role}',
  'roles.suggestionsNote': 'Roles you have held are not added unless you choose them.',
  'roles.aliases': 'Also searched for: {list}',
  'roles.notLimits':
    'These help Career Agent ask job sources better questions. Jobs with other titles are still found, '
    + 'and these roles never change a Search Fit.',
  'roles.full': 'You can name up to {n} roles.',
  'roles.saving': 'Saving...',
  'roles.saved': 'Saved.',
  'roles.notSaved': 'Your roles could not be saved. Try again, or skip this step.',
  'roles.settingsTitle': 'Roles in mind',
  'setup.work.required': 'Write at least one kind of work, or choose Skip for now.',
  'setup.work.already': 'Your search already describes the work you want ({n} phrases). You can change them later in '
    + 'Settings.',
  'setup.home.title': 'Where do you live?',
  'setup.home.why': 'Jobs are compared with where you are. Only the country is saved, and only on this computer.',
  'setup.home.label': 'Country',
  'setup.hire.title': 'Where can companies hire you?',
  'setup.hire.why': 'Many remote jobs only hire people in certain countries. Tell Career Agent where an employer '
    + 'could put you on their payroll directly, so it can tell which jobs are open to you.',
  'setup.hire.homeQuestion': 'Can a company hire you directly in {country}?',
  'setup.hire.yes': 'Yes, I can work for employers in {country}',
  'setup.hire.unsure': 'I am not sure yet',
  'setup.hire.othersLabel': 'Other countries where you can be hired (optional)',
  'setup.hire.countriesLabel': 'Countries where you can be hired',
  'setup.hire.add': 'Add',
  'setup.hire.remove': 'Remove {country}',
  'setup.hire.note': 'Not sure? Leave it. Jobs will say “not known yet” rather than guess.',
  'setup.regions.title': 'Any hiring regions that include you?',
  'setup.regions.why': 'Some jobs name a region instead of countries, such as “Latin America”. Tick a region only '
    + 'if employers hiring there can hire someone living where you do.',
  'setup.regions.legend':
    'Regions that include {country}',
  'setup.regions.note': 'Leaving a region unticked never counts as a no.',
  'setup.region.WORLDWIDE': 'Anywhere in the world',
  'setup.region.AMERICAS': 'The Americas (North, Central and South)',
  'setup.region.LATAM': 'Latin America',
  'setup.region.NORTH_AMERICA': 'North America (United States and Canada)',
  'setup.region.EMEA': 'Europe, the Middle East and Africa',
  'setup.region.APAC': 'Asia and the Pacific',
  'setup.level.title': 'Any levels you want to keep off your list?',
  'setup.level.why': 'Jobs at the levels you tick are hidden from Discover. One click shows them again, and nothing '
    + 'is deleted.',
  'setup.level.legend': 'Hide jobs at these levels',
  'setup.level.note': 'Most people leave all of these unticked.',
  'setup.pay.title': 'What pay are you aiming for?',
  'setup.pay.why': 'Used to compare with the salary a job states. You can skip it.',
  'setup.pay.label': 'Amount per month',
  'setup.pay.currency': 'Currency',
  'setup.pay.chooseCurrency': 'Choose a currency',
  'setup.pay.note': 'It stays on this computer.',
  'setup.pay.invalid': 'Enter an amount above zero, or leave it empty.',
  'setup.pay.needCurrency': 'Choose the currency for this amount.',
  'setup.ready.title':
    'You are ready. What now?',
  'setup.ready.why':
    'Find your first jobs now, or come back to it from Home whenever you like.',
  'setup.ready.phrases': '{n} phrases',
  'setup.ready.notAnswered': 'Not answered',
  'setup.ready.change': 'Change',
  'setup.ready.changeLabel': 'Change {what}',
  'setup.ready.note': 'Finding jobs checks public job boards for new postings and can take several minutes. Nothing '
    + 'about you is sent, and nothing is applied to.',
  'setup.ready.find': 'Find jobs now',
  'setup.ready.starting': 'Starting…',
  'setup.ready.finding': 'Finding jobs…',
  'setup.ready.progress': 'Checked {done} of {total} job sources',
  'setup.ready.progressLabel': 'Job sources checked',
  'setup.ready.keepUsing': 'You can keep using Career Agent while this runs.',
  'setup.ready.stop': 'Stop',
  'setup.ready.findAgain': 'Look again',
  'setup.ready.cancelled': 'Stopped. Jobs from the {ok} sources already checked are kept.',
  'setup.ready.failed': 'Finding jobs stopped because of a problem. Nothing you saved was changed. Try again in a '
    + 'moment.',
  'setup.ready.see': 'See your jobs',
  'setup.ready.toHome': 'Go to Home',
  'empty.findJobs': 'Find jobs now',
  'settings.setupHead': 'Your answers',
  'settings.setupLede': 'Where you live, where you can be hired, the work you want and your pay target. Go through '
    + 'them again one at a time, starting from what is saved.',
  'settings.setupOpen': 'Change my answers',
  'pagehead.eyebrow.setup': 'Getting started',
  'pagehead.title.setup': 'Set up your search',
  'setup.ready.none': 'None',
  'setup.progressLabel':
    'Setup progress',
  'setup.saveAndReturn':
    'Save and go back',
  'setup.work.savedRoles':
    'Kinds of work you gave',
  'setup.work.savedSkills':
    'Tools and skills you gave',
  'setup.work.changeInSettings':
    'To change these, open Settings, Search phrases: each one is shown with how many jobs it reaches.',
  'setup.home.placeholder':
    'Start typing a country',
  'setup.home.note':
    'Living somewhere does not mean every company there can hire you. The next question asks that.',
  'setup.home.unknown':
    'Choose a country from the list, or leave this empty.',
  'setup.workmodel.title':
    'How do you want to work?',
  'setup.workmodel.why':
    'Jobs in a way of working you prefer rank a little higher, and ones you would rather avoid a little lower.',
  'setup.workmodel.legend':
    'Your answer for each way of working',
  'setup.workmodel.note':
    'Never show hides those jobs from Discover, with one click to see them again. Remote is about how '
    + 'you work, not where companies can hire you: that is the hiring question you already answered.',
  'setup.arrangement.title':
    'Which working arrangements work for you?',
  'setup.arrangement.why':
    'When a job says how it hires, the ones that work for you rank a little higher. Many jobs do not say.',
  'setup.arrangement.legend':
    'Your answer for each arrangement',
  'setup.arrangement.note':
    'Nothing here hides a job.',
  'setup.cv.title':
    'Add your CV (optional)',
  'setup.cv.why':
    'It is read here, on this computer, and nothing in it counts until you confirm it.',
  'setup.cv.noNeed':
    'You can search for jobs without adding a CV.',
  'setup.cv.helps':
    'Adding your CV helps Career Agent build Career Evidence and makes resume preparation more useful. '
    + 'Each line becomes a statement for you to confirm or reject later, in Career Evidence.',
  'setup.cv.added':
    'Career data added. Review it any time in Career Evidence.',
  'setup.cv.choose':
    'Choose a file',
  'setup.cv.read':
    'Read this CV',
  'setup.cv.chooseFirst':
    'Choose a file first.',
  'setup.cv.reading':
    'Reading {name}…',
  'setup.cv.found':
    '{n} statements found. None of them counts until you confirm it in Career Evidence.',
  'setup.cv.privacy':
    'Reads {kinds}. The file is not kept, and nothing is sent anywhere.',
  'setup.cv.skip':
    'Skip for now',
  'setup.review.title':
    'Here is what you told Career Agent',
  'setup.review.why':
    'Change anything here, or later in Settings.',
  'setup.review.work':
    'Kinds of work',
  'setup.review.home':
    'Based in',
  'setup.review.hire':
    'Can be hired in',
  'setup.review.workmodel':
    'Ways of working',
  'setup.review.arrangement':
    'Working arrangements',
  'setup.review.level':
    'Levels hidden',
  'setup.review.pay':
    'Pay target',
  'setup.review.cv':
    'Career data',
  'setup.review.hireUnknown':
    'Not known yet',
  'setup.review.noPreference':
    'No preference',
  'setup.review.cvAdded':
    'CV added',
  'setup.review.cvNotAdded':
    'Not added yet',
  'setup.review.note':
    'Every answer here stays editable in Settings.',
  'setup.review.looksRight':
    'Looks right',
  'setup.ready.noCv':
    'You can find jobs without a CV. Career Evidence and resume preparation become useful once you add one.',
  'setup.ready.addCv':
    'Add career data',
  'setup.ready.settings':
    'Review settings',
  'evstart.title': 'Build your evidence bank',
  'evstart.body': 'Import your CV and Career Agent lists what it says, one line at a time. Nothing counts as your '
    + 'experience until you confirm it, and you can edit or reject any line.',
  'evstart.import': 'Import your CV',
  'evstart.hint': 'PDF, Word, text or Markdown. It is read on this computer and the file is not kept. You can also '
    + 'add an experience by hand below.',
  'career.aboutEvidence': 'Examples, and what this is used for',
  'board.empty': 'No applications tracked yet. Save a job or change its status in Discover and it appears here.',
  'board.toDiscover': 'Go to Discover',
  'settings.sourceEach': 'Each job source ({n}, {paused} paused): status and refresh timing',
  // choices.js: one answer per row, shared by the setup and Settings.
  'workModel.REMOTE': 'Remote',
  'workModel.HYBRID': 'Hybrid',
  'workModel.ONSITE': 'On-site',
  'workModel.answer.prefer': 'Prefer',
  'workModel.answer.fine': 'Fine',
  'workModel.answer.avoid': 'Rather avoid',
  'workModel.answer.never': 'Never show',
  'workModel.summary.prefer': 'Prefer {model}',
  'workModel.summary.avoid': 'Rather avoid {model}',
  'workModel.summary.never': 'Never show {model}',
  'arrangement.FULL_TIME_EMPLOYEE': 'Employee, on the company\'s payroll',
  'arrangement.CONTRACTOR_B2B': 'Contractor: you invoice the company (for example PJ or B2B)',
  'arrangement.EOR': 'Hired through an employer of record (EOR)',
  'arrangement.short.FULL_TIME_EMPLOYEE': 'employee',
  'arrangement.short.CONTRACTOR_B2B': 'contractor',
  'arrangement.short.EOR': 'employer of record',
  'arrangement.answer.yes': 'Works for me',
  'arrangement.answer.none': 'No preference',
  'arrangement.answer.no': 'Rather not',
  'arrangement.summary.yes': 'Works: {kind}',
  'arrangement.summary.no': 'Rather not: {kind}',
  'profileRow.workModelsAvoided': 'Ways of working you would rather avoid',
  'profileRow.workModelsExcluded': 'Ways of working never shown in Discover',
  // Finding jobs: one drawing for every screen that shows a run.
  'collect.now': 'Now reading {source} ({time})',
  'collect.elapsed': '{time} so far',
  'collect.deferred': '{n} sources skipped: paused, or not for the places you can work',
  'collect.slow': 'Some sources take a few minutes to read. It is still working.',
  'collect.noEta': 'No time left is shown: each source takes a different amount of time, so an estimate '
    + 'would be a guess.',
  'collect.scoring': 'Scoring the jobs that came in: {done} of {total}',
  'collect.scoringStart': 'Scoring the jobs that came in…',
  'collect.scoringLabel': 'Jobs scored',
  'collect.took': 'Reading the sources took {time}.',
  'collect.stopping': 'Stopping after this source…',
  'collect.finished': 'Done: {ok} of {total} sources answered, in {time}.',
  'collect.lastRun': 'Finding jobs',
  'collect.show': 'Show progress',
  'collect.dismiss': 'Dismiss',
  'time.seconds': '{s} s',
  'time.minutes': '{m} min {s} s',
  'time.hours': '{h} h {m} min',
  'date.months': 'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec',
  'firstrun.finishTitle': 'Finish setup · {n} left',
  'firstrun.finished': 'Setup finished',
  'firstrun.ledeLeft': 'What is left of your setup. None of it is required, and each says what it lets Career '
    + 'Agent work out. Change earlier answers any time in Settings.',
  'tailor.needsCv': 'Resume Tailor Beta works from your CV, and Career Agent has nothing about your career yet. '
    + 'Add your CV, or write down what you have done, in Career Evidence first.',
  'tailor.addCv': 'Add your CV',
  'tailor.openOwn': 'Already gave Resume Tailor your CV? Open Resume Tailor Beta',
  'setup.work.tooMany': 'That is {n} lines. Keep the 20 that matter most: each one is looked for in every job.',
  'setup.work.tooLong': 'Line {line} is long for a search phrase. Keep each line to a few words, like a job '
    + 'title or a tool.',
  // -- Career Workspace: profile, evidence, documents, guided import --
  'nav.documents': 'Documents',
  'nav.manage': 'Manage statements',
  'pagehead.eyebrow.documents': 'Documents',
  'pagehead.title.documents': 'Your documents',
  'pagehead.sub.documents': 'The CVs and documents Career Agent has read, and what is left to review.',
  'pagehead.eyebrow.manage': 'Advanced',
  'pagehead.title.manage': 'All statements',
  'pagehead.sub.profile': 'Who you are and what you are looking for.',
  'pagehead.sub.evidence': 'The proof behind your experience: projects, achievements and certifications.',
  'profileHead.import': 'Import resume',
  'profileHead.edit': 'Edit profile',
  'ui.period': '{start} to {end}',
  'ui.periodCurrent': '{start} to present',
  'ui.periodPresentOnly': 'Current',
  'ui.datesNotStated': 'Dates not stated',
  'ui.dateUnknown': 'Unknown',
  'ui.removeChip': 'Remove {name}',
  'ui.removeLine': 'Remove: {text}',
  'ui.cancel': 'Cancel',
  'ui.undo': 'Undo',
  'ui.undone': 'Undone.',
  'ui.close': 'Close',
  'ui.noLine': 'From {where}. No exact line was kept.',
  'ui.writtenByYou': 'Written by you',
  'ui.yourDocument': 'Your document',
  'ui.line': 'line {n}',
  'ui.source': 'Source: {where}',
  'ui.viewSource': 'View source',
  'ui.asWritten': 'As written in the file',
  'xp.heading': 'Experience',
  'xp.listLabel': 'Your experiences, newest first',
  'xp.add': 'Add experience',
  'xp.addHint': 'A role, freelance work or a side project.',
  'xp.empty': 'No experiences in your profile yet. Import your resume, or add one yourself.',
  'xp.editingEyebrow': 'Editing profile',
  'xp.editingText': 'Changes here update what application preparation can draw on. Search Fit is not affected.',
  'xp.doneEditing': 'Done editing',
  'xp.waitingNote': '{n} details from your documents are waiting for your review.',
  'xp.waitingNoteOne': '1 detail from your documents is waiting for your review.',
  'xp.continueReview': 'Continue review',
  'xp.roleNotStated': 'Role not stated',
  'xp.companyNotStated': 'Company not stated',
  'xp.edit': 'Edit',
  'xp.editLabel': 'Edit {role} at {company}',
  'xp.remove': 'Remove',
  'xp.removeLabel': 'Remove {role} at {company} from your profile',
  'xp.removeQuestion': 'Remove this experience from your profile?',
  'xp.removeDetail': 'Its details stay in your evidence, outside any experience. You can undo this.',
  'xp.removeConfirm': 'Remove from profile',
  'xp.removed': '{role} removed from your profile.',
  'xp.cardLabel': '{role} at {company}',
  'xp.skillsLabel': 'Skills used',
  'xp.detailsWaiting': '{n} details to review',
  'xp.detailsWaitingOne': '1 detail to review',
  'xp.reviewThem': 'Review',
  'xp.reviewThemLabel': 'Review {n} details for {role}',
  'xp.reviewThemLabelOne': 'Review 1 detail for {role}',
  'xp.noMonth': 'Month (optional)',
  'xp.monthOf': '{label}: month',
  'xp.yearOf': '{label}: year',
  'xp.yearPlaceholder': 'Year',
  'xp.start': 'Start',
  'xp.end': 'End',
  'xp.current': 'I work here now',
  'xp.role': 'Role',
  'xp.company': 'Company',
  'xp.description': 'What you did',
  'xp.highlights': 'Highlights',
  'xp.highlightsHint': 'One result or responsibility per line. Each is saved as a statement you made.',
  'xp.addHighlight': '+ Add highlight',
  'xp.highlightPlaceholder': 'What you did, and what changed because of it',
  'xp.skills': 'Skills used',
  'xp.skillPlaceholder': 'Type a skill and press Enter',
  'xp.changeType': 'Change type',
  'xp.typeLabel': 'Kind of experience',
  'xp.toolsFromHighlights': 'Also named in your highlights: {names}. Change the highlight to remove them.',
  'xp.save': 'Save changes',
  'xp.saved': 'Changes saved.',
  'xp.cancel': 'Cancel',
  'xp.newEyebrow': 'New experience',
  'xp.editEyebrow': 'Edit experience',
  'xp.editorNote': 'Used when preparing applications',
  'xp.newLabel': 'New experience',
  'xp.editingLabel': 'Editing {role}',
  'evp.heading': 'Professional evidence',
  'evp.lede': 'Proof you have confirmed. Application preparation quotes it; it does not change Search Fit.',
  'evp.add': '+ Add evidence',
  'evp.emptyTitle': 'No evidence yet',
  'evp.emptyBody': 'Add a project, an achievement or a certification, or import your resume from Documents.',
  'evp.emptyImport': 'Import your resume',
  'evp.emptyAdd': 'Add one yourself',
  'evp.group.PROJECT': 'Projects',
  'evp.group.ACHIEVEMENT': 'Achievements',
  'evp.group.CERTIFICATION': 'Certifications',
  'evp.group.EDUCATION': 'Education',
  'evp.type.PROJECT': 'Project',
  'evp.type.ACHIEVEMENT': 'Achievement',
  'evp.type.CERTIFICATION': 'Certification',
  'evp.type.EDUCATION': 'Education',
  'evp.typeHint.PROJECT': 'Something you built or led',
  'evp.typeHint.ACHIEVEMENT': 'A talk, award or recognition',
  'evp.typeHint.CERTIFICATION': 'A course, exam or credential',
  'evp.entries': '{n} entries',
  'evp.entriesOne': '1 entry',
  'evp.foldTitle': '{name} ({n})',
  'evp.asideTitle': 'Not in use ({n})',
  'evp.asideLede': 'Statements you stopped using, and ones written down but never confirmed. They '
    + 'keep their history.',
  'evp.draft': 'Not confirmed yet',
  'evp.retired': 'Not in use',
  'evp.confirmDraft': 'Confirm',
  'evp.useAgain': 'Use again',
  'evp.backInUse': 'Back in use.',
  'evp.open': 'Open',
  'evp.openLabel': 'Open {title}',
  'evp.stopUsing': 'Stop using',
  'evp.stopLabel': 'Stop using {title}',
  'evp.stopQuestion': 'Stop using this evidence?',
  'evp.stopDetail': 'It stays, with its history, and application preparation stops citing it. You can undo this.',
  'evp.stopConfirm': 'Stop using',
  'evp.stopped': 'No longer in use.',
  'evp.skills': 'Skills',
  'evp.linkedTo': 'Linked to {role} · {company}',
  'evp.roleNotStated': 'Role not stated',
  'evp.untitled': 'Evidence',
  'evp.history': 'History ({n} versions)',
  'evp.historyOne': 'History (1 version)',
  'evp.revision': 'Version {n}',
  'evp.sourceHeading': 'Where it came from',
  'evp.close': 'Close',
  'evp.edit': 'Edit',
  'evp.newEyebrow': 'New evidence',
  'evp.editEyebrow': 'Edit evidence',
  'evp.addTitle': 'Add evidence',
  'evp.editTitle': 'Edit evidence',
  'evp.addLede': 'Proof behind your experience. Application preparation quotes it when it explains a match.',
  'evp.addForRequirement': 'For the requirement "{requirement}". '
    + 'Write down what you did; it is saved as a statement you made.',
  'evp.typeLabel': 'Type',
  'evp.title': 'Title',
  'evp.titlePlaceholder': 'e.g. Checkout redesign',
  'evp.when': 'When (optional)',
  'evp.whenHint': 'Leave it empty if you do not remember the month.',
  'evp.linkedExperience': 'Linked experience',
  'evp.notLinked': 'Not linked',
  'evp.description': 'Description',
  'evp.descriptionPlaceholder': 'What it was, and what your part was.',
  'evp.skillPlaceholder': 'Type a skill and press Enter',
  'evp.writtenNote': 'Saved as something you wrote. It is never shown as a quote from a document.',
  'evp.editKeepsQuote': 'Your wording becomes a new version. The line from your document is kept beside it.',
  'evp.needTitle': 'Give it a title first.',
  'evp.save': 'Save evidence',
  'evp.saved': 'Evidence added.',
  'evp.savedEdit': 'Evidence saved.',
  'evp.cancel': 'Cancel',
  'docs.heading': 'Documents',
  'docs.importTitle': 'Import your resume',
  'docs.importBody': 'Career Agent reads it and lists what it found, experience by experience. '
    + 'Nothing counts as yours until you confirm it.',
  'docs.finds.experiences': 'Experiences, with company, role and dates',
  'docs.finds.skills': 'Skills and tools',
  'docs.finds.certifications': 'Certifications and courses',
  'docs.finds.education': 'Education',
  'docs.choose': 'Choose a file',
  'docs.noFile': 'PDF, Word, text or Markdown',
  'docs.privacy': 'Read on this computer. The file itself is not kept.',
  'docs.reading': 'Reading {name}...',
  'docs.yourImports': 'Your imports',
  'docs.empty': 'Nothing imported yet.',
  'docs.archivedFold': 'Archived ({n})',
  'docs.status.archived': 'Archived',
  'docs.status.replaced': 'Replaced by a newer reading',
  'docs.status.waiting': '{n} to review',
  'docs.status.done': 'All answered',
  'docs.kind.cv': 'Resume',
  'docs.kind.package': 'Document package',
  'docs.meta': '{kind} · read {date} · {experiences} · {confirmed}',
  'docs.experiences': '{n} experiences',
  'docs.experiencesOne': '1 experience',
  'docs.confirmed': '{n} confirmed',
  'docs.confirmedOne': '1 confirmed',
  'docs.continue': 'Continue review',
  'docs.lookAgain': 'Look at it again',
  'docs.reviewLabel': 'Review {name}',
  'docs.restore': 'Restore',
  'docs.restored': '{name} restored.',
  'docs.lookInside': 'Look inside',
  'docs.useThis': 'Use this reading',
  'docs.inUse': '{name} is now the reading in use.',
  'docs.archive': 'Archive',
  'docs.archiveLabel': 'Archive {name}',
  'docs.archived': '{name} archived. Nothing in it waits for you.',
  'docs.delete': 'Delete...',
  'docs.deleteLabel': 'Delete {name}',
  'docs.deleteQuestion': 'Delete {name} permanently?',
  'docs.deleteAll': 'All {removed} suggestions in it go ({pending} unanswered). Nothing you confirmed came from it.',
  'docs.deleteKeeps': '{removed} unconfirmed suggestions go ({pending} unanswered). The {confirmed} '
    + 'you confirmed stay, with the lines they came from.',
  'docs.deleteForever': 'This cannot be undone. To keep everything, archive it instead.',
  'docs.deleteConfirm': 'Delete permanently',
  'docs.deleted': '{name} deleted.',
  'imp.heading': 'Review what your document says',
  'imp.railLabel': 'Review steps',
  'imp.readOn': 'Read {date}',
  'imp.savedAsYouGo': 'Every answer is saved as you go. You can stop and come back.',
  'imp.step.experiences': 'Experiences',
  'imp.step.skills': 'Skills',
  'imp.step.certifications': 'Certifications & courses',
  'imp.step.education': 'Education & languages',
  'imp.step.other': 'Other details',
  'imp.step.summary': 'Summary',
  'imp.unnamed': 'Not named',
  'imp.archivedNote': 'This import is archived. Restore it in Documents to answer anything.',
  'imp.replacedNote': 'A newer reading of your documents is in use. Choose "Use this reading" in '
    + 'Documents to review this one.',
  'imp.noExperiences': 'No experiences found',
  'imp.noExperiencesBody': 'The document named no company or role Career Agent could read. The rest is below.',
  'imp.continue': 'Continue',
  'imp.experienceOf': 'Experience {n} of {total}',
  'imp.experienceLabel': '{role} at {company}',
  'imp.roleNotStated': 'Role not stated',
  'imp.companyNotStated': 'Company not stated',
  'imp.state.new': 'New',
  'imp.state.newBody': 'Not in your profile yet.',
  'imp.state.existing': 'Already in your profile',
  'imp.state.unchangedBody': 'No changes. Nothing new here.',
  'imp.state.newDetailsBody': '{n} new details found.',
  'imp.state.newDetailsBodyOne': '1 new detail found.',
  'imp.state.dates': 'Check the dates',
  'imp.state.datesBody': 'Your document and your profile disagree.',
  'imp.state.help': 'Needs your help',
  'imp.state.helpBody': 'Career Agent could not tell the company or the role.',
  'imp.placeNew': 'Add it to your profile to review its details there too. This confirms nothing.',
  'imp.addToProfile': 'Add to profile',
  'imp.added': '{role} added to your profile.',
  'imp.placeExisting': 'These details belong with {role} at {company} in your profile.',
  'imp.keepTogether': 'Keep them together',
  'imp.keptTogether': 'Kept with that experience.',
  'imp.whichDates': 'Which dates are right?',
  'imp.inProfile': 'In your profile',
  'imp.inDocument': 'In your document',
  'imp.useDates': 'Use the dates {where}: {dates}',
  'imp.datesKept': 'Your profile\'s dates are kept.',
  'imp.datesKeptNote': 'Your profile\'s dates are kept: {dates}.',
  'imp.datesUpdated': 'Dates updated from your document.',
  'imp.helpAsk': 'Tell Career Agent what this is. Nothing is guessed.',
  'imp.role': 'Role',
  'imp.company': 'Company',
  'imp.saveStructure': 'Save',
  'imp.structureSaved': 'Saved.',
  'imp.details': '{n} details found',
  'imp.detailsOne': '1 detail found',
  'imp.tally': '{confirmed} confirmed · {waiting} need review',
  'imp.noDetails': 'No details under this experience.',
  'imp.headingSource': 'What the document said about this job',
  'imp.sourceLine': 'Line {n}: {text}',
  'imp.dontImport': 'Don\'t import',
  'imp.dontImportLabel': 'Don\'t import the unanswered details of {role}',
  'imp.skipped': '{n} details left out.',
  'imp.skippedOne': '1 detail left out.',
  'imp.previous': 'Previous',
  'imp.nextExperience': 'Next experience',
  'imp.continueTo': 'Continue to {step}',
  'imp.edit': 'Edit',
  'imp.editLabel': 'Your wording',
  'imp.editItemLabel': 'Edit: {text}',
  'imp.cancel': 'Cancel',
  'imp.confirmMyWords': 'Confirm my wording',
  'imp.notSure': 'Not sure yet',
  'imp.notSureLabel': 'Not sure yet: {text}',
  'imp.reject': 'Reject',
  'imp.rejectLabel': 'Reject: {text}',
  'imp.confirm': 'Confirm',
  'imp.confirmLabel': 'Confirm: {text}',
  'imp.reopen': 'Reopen',
  'imp.confirmed': 'Confirmed.',
  'imp.confirmedEdit': 'Confirmed in your words.',
  'imp.rejected': 'Rejected.',
  'imp.unsure': 'Left for later.',
  'imp.reopened': 'Back to waiting.',
  'imp.itemState.waiting': 'Needs review',
  'imp.itemState.unsure': 'Not sure yet',
  'imp.itemState.confirmed': 'Confirmed',
  'imp.itemState.rejected': 'Rejected',
  'imp.stateOf': '{state}: {name}',
  'imp.alreadyThere': 'Already in your profile',
  'imp.duplicate': 'Appears twice',
  'imp.conflictNote': 'Your documents disagree about this. Choose the version that is right.',
  'imp.chooseThis': 'This one is right',
  'imp.youChanged': 'Career Agent first read: {text}',
  'imp.found.skills': '{n} skills found',
  'imp.found.skillsOne': '1 skill found',
  'imp.found.certifications': '{n} certifications and courses found',
  'imp.found.certificationsOne': '1 certification or course found',
  'imp.found.education': '{n} education and language entries found',
  'imp.found.educationOne': '1 education or language entry found',
  'imp.found.other': '{n} other details found',
  'imp.found.otherOne': '1 other detail found',
  'imp.oneByOne': 'Each one is its own answer, saved as you give it.',
  'imp.keepExisting': 'Keep the one in my profile',
  'imp.keepExistingLabel': 'Keep the one already in your profile: {text}',
  'imp.keptExisting': 'Kept the one already in your profile. Nothing was added.',
  'imp.leftToAnswer': '{n} left to answer',
  'imp.legendNew': '+ press to keep',
  'imp.legendIn': '✓ kept',
  'imp.legendOut': '× leave out',
  'imp.legendHint': 'Press a skill to keep it, or × to leave it out.',
  'imp.keepLabel': 'Keep {name}',
  'imp.leaveOutLabel': 'Leave out {name}',
  'imp.kept': '{name} kept.',
  'imp.leftOut': '{name} left out.',
  'imp.summaryWaiting': 'Almost there',
  'imp.summaryDone': 'Review finished',
  'imp.tileConfirmed': 'confirmed',
  'imp.tileWaiting': 'still to review',
  'imp.tileRejected': 'left out',
  'imp.tileExperiences': 'experiences in your profile',
  'imp.summaryNote': 'Everything you answered is already saved. Confirmed details are what '
    + 'application preparation can draw on; Search Fit is not affected.',
  'imp.reviewWaiting': 'Review what is left',
  'imp.finish': 'Back to documents',
  'ai.head': 'AI & Semantic Matching',
  'ai.intro': 'Career Agent can ask an AI to recognise the work you want in postings that describe it in other '
    + 'words. The AI only interprets: Career Agent checks every quote against the posting and does all of the '
    + 'scoring itself.',
  'ai.enabled': 'Use AI semantic matching',
  'ai.provider': 'Provider',
  'ai.mode.auto': 'Auto',
  'ai.mode.deepseek': 'DeepSeek Flash',
  'ai.mode.codex': 'Codex',
  'ai.mode.claude_code': 'Claude Code',
  'ai.mode.laya': 'Laya (local)',
  'ai.mode.deterministic': 'Deterministic only',
  'ai.mode.fake': 'Test provider',
  'ai.summary.ready': 'AI semantic matching: ready',
  'ai.summary.off': 'AI semantic matching is off. Search Fit uses your phrases only.',
  'ai.summary.deterministic': 'Deterministic only. Search Fit uses your phrases, without AI.',
  'ai.summary.unavailable': 'No AI provider is available. Search Fit uses your phrases only.',
  'ai.summary.demo': 'Demo mode never uses AI.',
  'ai.using': 'Using {provider}',
  'ai.fellBack': '{preferred} is not available, so {used} is used instead.',
  'ai.fellBackDeterministic': '{preferred} is not available. Search Fit uses your phrases only until it is.',
  'ai.saved': 'Saved.',
  'ai.providersHead': 'Providers and privacy',
  'ai.state.AVAILABLE': 'Available',
  'ai.state.CONNECTED': 'Connected',
  'ai.state.NOT_INSTALLED': 'Not installed',
  'ai.state.SIGN_IN_REQUIRED': 'Sign-in required',
  'ai.state.KEY_MISSING': 'API key missing',
  'ai.state.CONNECTION_FAILED': 'Connection failed',
  'ai.state.LIMIT_OR_ERROR': 'Unavailable or limit reached',
  'ai.state.UNSUPPORTED': 'Not supported for semantic matching',
  'ai.authApiKey': 'Signed in with an API key, which would bill the API instead of your subscription. Sign in with '
    + 'your subscription to use it here.',
  'ai.billing.METERED_API': 'Paid per use with your own API key. Career Agent estimates each run and stops at your '
    + 'budget.',
  'ai.billing.SUBSCRIPTION': 'Uses your own subscription and its limits. Career Agent cannot know a price for it.',
  'ai.billing.LOCAL': 'Runs on this computer.',
  'ai.sends.deepseek': 'Sends your search intent and each evaluated posting to DeepSeek.',
  'ai.sends.codex': 'Sends your search intent and each evaluated posting through your own Codex sign-in.',
  'ai.sends.claude_code': 'Sends your search intent and each evaluated posting through your own Claude Code sign-in.',
  'ai.sends.laya': 'Nothing leaves this computer.',
  'ai.laya.why': 'Laya answers without quoting the posting, so its answers cannot be checked as evidence. It is '
    + 'not used for Search Fit.',
  'ai.check': 'Check connection',
  'ai.checking': 'Checking',
  'ai.key.label': 'DeepSeek API key',
  'ai.key.placeholder': 'Paste your key',
  'ai.key.configured': 'An API key is configured on this computer.',
  'ai.key.missing': 'No API key yet.',
  'ai.key.add': 'Save key',
  'ai.key.replace': 'Replace key',
  'ai.key.remove': 'Remove key',
  'ai.key.help': 'Kept only in the .env file on this computer. It is never shown again, never saved in the '
    + 'database and never sent anywhere except to DeepSeek.',
  'ai.key.saved': 'Key saved.',
  'ai.key.removed': 'Key removed.',
  'ai.privacyHead': 'What leaves this computer',
  'ai.privacy.sends': 'Sent to the provider: the work, tools and other signals you said you want, and the title '
    + 'and text of each posting evaluated.',
  'ai.privacy.never': 'Never sent: your Career Profile, your evidence, CV files, application history, other '
    + 'postings or your database.',
  'ai.runHead': 'Evaluate postings',
  'ai.budget': 'DeepSeek budget per run (USD)',
  'ai.budgetHelp': 'A hard stop: a run ends before it could spend more.',
  'ai.estimate': 'Estimate',
  'ai.plan': '{candidates} postings this run, of {eligible} that match your intent. Estimated {expected}, at most '
    + '{worst}, within a budget of {budget}.',
  'ai.planSubscription': '{candidates} postings this run, of {eligible} that match your intent. Uses your '
    + 'subscription limits.',
  'ai.planNone': 'Nothing new to evaluate.',
  'ai.run': 'Evaluate {count} postings',
  'ai.running': 'Evaluating {done} of {total}',
  'ai.cancel': 'Stop',
  'ai.recalc': 'Search Fit will be recalculated for the evaluated postings.',
  'ai.last': 'Last run: {published} postings evaluated, {failed} failed, {calls} calls, {tokens} tokens, {spent} '
    + 'spent.',
  'ai.lastSubscription': 'Last run: {published} postings evaluated, {failed} failed, {calls} calls, {tokens} tokens.',
  'readiness.NOT_READY': 'Search Fit is not ready: tell Career Agent what work you want next.',
  'readiness.PARTIAL': 'Search Fit is usable. It becomes more precise when you also say {missing}.',
  'readiness.READY': 'Search Fit is ready: it knows the work, the level and the way of working you want.',
  'readiness.missing.level': 'the level you want',
  'readiness.missing.work_model': 'how you want to work',
  'readiness.missing.work': 'the work you want',
  'readiness.join': ' and ',
  'badge.notReady': 'Not ready',
  'badge.notReadyHelp': 'Search Fit needs to know what work you want next. Add it in the guided setup or in Settings.',
  'component.responsibilities': 'Work you want',
  'component.technologies': 'Tools and methods',
  'component.automation_integration': 'Other desired signals',
  'drawer.notConfigured': 'Not part of your search: you have not listed anything here, so it does not count for or '
    + 'against this posting.',
  'drawer.alreadyCounted': 'Already counted',
  'drawer.semanticFinding': 'Recognised by AI, quoted from the posting',
  'drawer.semanticUsed': '{provider} helped recognise the work you want; every quote below was checked against the '
    + 'posting.',
  'setup.work.intent': 'This is your search intent: the work you want next, which may be different from what you '
    + 'have done. Your background lives in your Career Profile and is never used as a search phrase.',
  'ai.error.failed': 'That did not work. Nothing was changed; try again in a moment.',
  'ai.error.busy': 'Something else is running. Try again when it finishes.',
  'ai.error.key': 'That does not look like a DeepSeek API key.',
  'ai.error.setting': 'That setting is not valid.',
  'ai.stop.BUDGET': 'The run stopped at its budget.',
  'ai.stop.PROVIDER_STOPPED': 'The run stopped because the provider stopped answering.',
  'ai.stop.CANCELLED': 'The run was stopped.',
  'ai.stop.NOTHING_NEW': 'Nothing new to evaluate.',
  'ai.stop.NO_PROVIDER': 'No AI provider is available.',
  'ai.stop.NO_INTENT': 'Tell Career Agent what work you want first.',
  'ai.stop.NO_INDEX': 'Recalculate Search Fit first, then try again.',
  'ai.stop.PRICE_UNKNOWN': 'This provider has no known price, so it cannot be held to a budget.',
  'ai.stop.ERROR': 'The run stopped because of an error.',
  'drawer.toolsGuard': 'Held at half: this posting uses tools you want, and none of the work you want was found in it.',
};

/**
 * Brazilian Portuguese.
 *
 * Presentation only. Every key here names something WE wrote; not one of them
 * touches a posting, a company name or a quote.
 *
 * Where a term has an established Brazilian usage in hiring, that usage wins
 * over a literal translation: `Pleno` is what a mid-level role is called here,
 * and rendering it "Nível médio" would be correct Portuguese and wrong for the
 * subject.
 */
const PT_BR = {
  "tailor.groupLabel": "Resume Tailor",
  "tailor.note": "Copie a vaga e cole em Tailor resume. Revise as evidências separadamente no Resume Tailor Beta.",
  "tailor.copy": "Copiar descrição da vaga",
  "tailor.open": "Abrir Resume Tailor Beta",
  "tailor.copied": "Copiado. Abra Resume Tailor Beta e cole em Tailor resume.",
  "tailor.unavailable": "Área de transferência indisponível. Selecione e copie a descrição abaixo.",

  "firstrun.searchSave": "Salvar as frases da minha busca",
  "firstrun.workLabel": "Trabalho que você quer fazer, uma frase curta por linha",
  "firstrun.skillsLabel": "Ferramentas ou habilidades a buscar, uma por linha (opcional)",
  "firstrun.searchHelp": "Estas frases descrevem o trabalho que você procura nas vagas. Não confirmam "
    + "experiência. Salve aqui e depois edite em Configurações e fontes. Vagas existentes "
    + "precisam de recálculo após mudanças.",
  "maintenance.title": "Estado da atualização das fontes",
  "maintenance.unavailable": "Não foi possível ler a atualização. Reabra Configurações e fontes para tentar "
    + "novamente; suas vagas salvas continuam disponíveis.",
  "maintenance.running": "Uma atualização está em andamento.",
  "maintenance.idle": "Nenhuma atualização está em andamento neste app ou pelo comando de "
    + "manutenção com limite de tempo.",
  "maintenance.last": "Última consulta bem-sucedida a uma fonte: {date}.",
  "maintenance.counts": "{fresh} itens recentes; {pending} itens aptos pendentes; {total} itens no inventário.",
  "maintenance.interrupted": "A atualização anterior não tem registro de conclusão. O trabalho incompleto "
    + "continua pendente; as vagas já concluídas foram mantidas.",
  "maintenance.manual": "{n} precisam de uma execução manual por você.",
  "maintenance.cooldown": "{n} aguardam o intervalo exigido pela fonte.",
  "maintenance.refusal": "{n} aguardam após a fonte recusar o acesso.",
  "maintenance.separate": "{n} precisam de outro caminho de coleta disponível.",
  "maintenance.larger": "{n} precisam de um limite de tempo maior.",
  "maintenance.later": "{n} ficam para uma próxima sessão com limite de tempo.",
  "maintenance.explanation": "Os números contam murais ou janelas de feeds, não vagas. Os adiamentos "
    + "descrevem um plano de 30 minutos, não falhas. Uma consulta recente não "
    + "significa cobertura de todo o mercado. Você pode explorar e acompanhar vagas "
    + "enquanto houver trabalho pendente. Os botões por fonte abaixo usam seus "
    + "limites de coleta existentes.",
  'career.noSourceQuote': 'Nenhuma citação da fonte original foi registrada para esta afirmação.',
  'career.importedStatement': 'Afirmação importada',
  'career.revisions': 'Ver revisões anteriores',
  'career.revision': 'Revisão {number}',
  'career.itemMeta': '{category} · {state}',
  'settings.market.br': 'Brasil',
  'settings.market.eu': 'Europa',
  'settings.market.latam': 'América Latina',
  'settings.market.global': 'Mundial',
  'career.aliasScope': 'Nomes de empresa em revisão: {first} e {second}. A união usa {second} na exibição.',
  'career.destinationScope': 'Destino: {destination}',
  'career.evidenceText': 'Afirmação de evidência',
  'career.textEditHelp': 'Salvar cria uma nova revisão. A fonte original e o texto anterior continuam disponíveis.',
  'career.editText': 'Editar afirmação',
  'career.saveText': 'Salvar revisão',
  'career.editImportHelp': 'Para corrigir uma importação não confirmada, abra a revisão da fonte abaixo.',
  'settings.refreshTiming': 'Preferência de atualização',
  'settings.refreshMode.AUTO': 'Seguir meus mercados desejados',
  'settings.refreshMode.ENABLED': 'Ativar atualização deste mercado',
  'settings.refreshMode.PAUSED': 'Pausar atualização',
  'settings.sourcePausedByYou': 'Você pausou a atualização. Vagas coletadas continuam disponíveis.',
  'settings.refreshAnyway': 'Atualizar mesmo assim',
  'settings.refreshNow': 'Atualizar agora',
  'settings.targets': 'Locais desejados, formas de trabalho e remuneração',
  'settings.searchHelp': 'O que você gostaria de ver com mais ou menos frequência?',
  'settings.prefer_keyword': 'Priorizar',
  'settings.prefer_keywordHelp': 'Coloca vagas com estas expressões mais acima na ordenação do Descobrir.',
  'settings.avoid_keyword': 'Evitar',
  'settings.avoid_keywordHelp': 'Coloca vagas com estas expressões mais abaixo, sem ocultá-las.',
  'settings.exclude_keyword': 'Nunca mostrar',
  'settings.exclude_keywordHelp': 'Exclui vagas com estas expressões da sua visualização do Descobrir.',
  'settings.addPhrase': 'Digite uma expressão e pressione Enter ou vírgula',
  'settings.clearPhrases': 'Limpar expressões',
  'settings.localPreference': 'Salvo neste navegador. A busca ignora maiúsculas e minúsculas. '
    + 'Estas listas não alteram a Aderência à busca.',
  'settings.model': 'Seu modelo de busca',
  'settings.modelAdvanced': 'Revisar conceitos e vocabulário avançado de pontuação',
  'settings.advancedRefresh': 'Diagnóstico avançado de atualização',
  'settings.sourceHelp': 'O momento da atualização segue seus mercados desejados. '
    + 'Isso nunca filtra o que uma fonte coleta. Vagas já coletadas continuam disponíveis no Descobrir.',
  'settings.sourceDetails': 'Detalhes técnicos das fontes',
  'settings.sourceMarket': 'Mercado: {market}',
  'settings.sourcePaused': 'Pausada porque este mercado está fora dos seus mercados desejados atuais.',
  'settings.sourceFailure': 'A última atualização falhou. Vagas já coletadas continuam disponíveis no Descobrir.',
  'settings.sourceUpdated': 'Última atualização bem-sucedida: {date}',
  'career.heading': 'Suas experiências',
  'career.guidance': 'Descreva o que assumiu, construiu ou mudou; as ferramentas, pessoas e o escopo ' +
    'envolvidos; decisões que tomou e resultados que observou. Inclua medidas quando ' +
    'souber. Não é obrigatório ter uma métrica.',
  'career.example': 'Por exemplo: “Organizei a passagem semanal entre turnos” ou “Criei uma lista ' +
    'compartilhada que ajudou novos colegas a atender solicitações”. Mantenha cada ' +
    'afirmação separada e fiel ao que aconteceu.',
  'career.boundary': 'As experiências organizam o que você fez. Só evidências confirmadas podem apoiar ' +
    'Evidências / Preparo e a preparação do currículo. Aderência à busca usa a vaga e ' +
    'seu modelo de busca.',
  'career.new': 'Adicionar experiência',
  'career.inbox': 'Precisa organizar',
  'career.inboxCount': 'Precisa organizar ({count})',
  'career.inboxHelp': 'Aqui ficam evidências sem uma experiência revisada. Empresa, datas e cargo ' +
    'ausentes continuam desconhecidos até você informá-los. Selecione afirmações ' +
    'relacionadas para atribuí-las juntas.',
  'career.allEvidence': 'Buscar evidências entre experiências',
  'career.roleUnknown': 'Cargo precisa de revisão',
  'career.dateUnknown': 'Não informado',
  'career.independent': 'Experiência independente',
  'career.current': 'Atual',
  'career.count': '{count} evidências · {confirmed} confirmadas',
  'career.open': 'Explorar evidências',
  'career.edit': 'Editar experiência',
  'career.history': 'Histórico de organização',
  'career.undo': 'Revisar reversão',
  'career.proposals': 'Revisar grupos importados e possíveis duplicatas ({count} grupos)',
  'career.proposalHelp': 'São sugestões a partir dos nomes e datas importados, não cargos estabelecidos. ' +
    'Nomes parecidos nunca comprovam que duas empresas são a mesma. Revise cada ' +
    'associação antes de aplicar.',
  'career.possibleDuplicate': 'Possível empresa duplicada',
  'career.merged': 'Você uniu estes nomes de empresa. Os cargos continuam separados.',
  'career.separate': 'Você escolheu manter estas empresas separadas.',
  'career.proposalCount': '{count} evidências para organizar',
  'career.ambiguity': 'Revise cargo e datas: há informação ausente, sobreposta ou divergente. Nenhum ' +
    'cargo foi decidido por você.',
  'career.reviewGroup': 'Revisar este grupo',
  'career.company': 'Empresa ou organização',
  'career.title': 'Cargo ou título do projeto',
  'career.period_start': 'Mês de início',
  'career.period_end': 'Mês de término',
  'career.display_order': 'Ordem de exibição',
  'career.current_role': 'Faço este trabalho atualmente',
  'career.kind': 'Tipo de experiência',
  'career.metadataHelp': 'Isso muda a organização das evidências. Afirmações originais, nomes de empresas, ' +
    'datas e fontes importadas permanecem intactos. O próximo passo mostra todas as ' +
    'evidências afetadas.',
  'career.preview': 'Revisar alterações',
  'career.cancel': 'Cancelar',
  'career.more': 'Mostrar mais',
  'career.previous': 'Página anterior',
  'career.apply': 'Aplicar alterações revisadas',
  'career.scope': 'Esta ação afeta {count} evidências.',
  'career.preserve': 'O texto da fonte e o histórico das evidências são preservados. Organizar não ' +
    'confirma evidências.',
  'career.reviewed': 'Li as afirmações selecionadas e confirmo que descrevem minha experiência com precisão.',
  'career.selected': '{count} selecionadas',
  'career.anyState': 'Qualquer estado de revisão',
  'career.anyCategory': 'Todas as categorias',
  'career.source': 'Evidência importada / fonte',
  'career.find': 'Palavras nas suas evidências',
  'career.filter': 'Buscar',
  'career.state': 'Estado de revisão',
  'career.category': 'Categoria da evidência',
  'career.selectFiltered': 'Selecionar todas as {count} evidências filtradas',
  'career.selectExperience': 'Selecionar toda a experiência',
  'career.clear': 'Limpar seleção',
  'career.organizeSelected': 'Organizar evidências selecionadas',
  'career.destination': 'Experiência de destino',
  'career.page': '{start} a {end} de {total} evidências',
  'career.importReview': 'Revisar fontes importadas e divergências',
  'career.evidenceEditor': 'Editar evidências individuais e consultar revisões',
  'career.action.create': 'Criar experiência',
  'career.action.edit': 'Editar dados da experiência',
  'career.action.move': 'Mover evidências selecionadas',
  'career.action.merge_experiences': 'Unir esta experiência ao destino',
  'career.action.split': 'Separar seleção em uma nova experiência',
  'career.action.merge_companies': 'Unir nomes de empresa',
  'career.action.keep_separate': 'Manter separadas',
  'career.action.category': 'Alterar categoria selecionada',
  'career.action.confirm': 'Revisar e confirmar selecionadas',
  'career.action.retire': 'Retirar / rejeitar selecionadas',
  'career.action.undo': 'Desfazer alteração na organização',
  'career.kind.EMPLOYMENT': 'Emprego',
  'career.kind.VOLUNTEER': 'Voluntariado',
  'career.kind.FREELANCE': 'Trabalho autônomo',
  'career.kind.ACADEMIC': 'Projeto acadêmico',
  'career.kind.PERSONAL': 'Projeto pessoal',
  'career.category.ACHIEVEMENT': 'Realizações',
  'career.category.RESPONSIBILITY': 'Responsabilidades',
  'career.category.PROJECT': 'Projetos',
  'career.category.TOOL': 'Ferramentas e sistemas',
  'career.category.SKILL': 'Habilidades / afirmações de apoio',
  'career.category.CERTIFICATION': 'Certificações',
  'career.category.EDUCATION': 'Formação',
  'career.category.OTHER': 'Outras evidências',
  'career.state.CONFIRMED': 'Confirmada',
  'career.state.PENDING': 'Precisa de revisão',
  'career.state.RETIRED': 'Retirada',
  'career.state.REJECTED': 'Rejeitada',
  "review.title": "Revisar origens do modelo de busca",
  "review.help": "Os valores atuais são preservados. Igualdade com o exemplo antigo sugere herança; não "
    + "comprova uma escolha sua. Manter registra sua revisão. Editar ou Remover mostra uma "
    + "prévia antes de salvar. Grupos vazios deixam de reconhecer o conceito; regras de "
    + "triagem dependentes continuam visíveis nesta revisão.",
  "review.legacy": "Valor do exemplo antigo (apenas comparação)",
  "review.editValue": "Editar {path}",
  "review.confirm": "Confirmar revisão",
  "review.kept": "Revisão salva. Valores da busca e pontuações continuam iguais.",
  "review.keepCost": "Confirme que revisou e manteve este valor. Não precisa recalcular.",
  "review.keep": "Manter",
  "review.edit": "Editar / prévia",
  "review.remove": "Remover / prévia",
  "review.reviewed_by_user": "Revisado por você",
  "review.changed_locally": "Alterado localmente; intenção não confirmada",
  "review.inherited_legacy_example": "Igual ao exemplo antigo",
  "review.neutral_product_policy": "Política neutra do produto",
  "review.unknown_provenance": "Origem desconhecida",

  'app.tagline': 'roda no seu computador',
  'app.skip': 'Ir para os resultados',
  'view.cards': 'Cartões',
  'view.table': 'Tabela',
  'view.board': 'Quadro',
  'view.group': 'Como mostrar os resultados',
  'order.group': 'Ordem e agrupamento',
  'order.oneRow': 'Uma linha por vaga',
  'order.by': 'Ordenar por',
  'sort.score': 'Maior aderência à busca',
  'sort.confidence': 'Anúncio mais completo',
  'sort.posted': 'Mais recentes',
  'sort.company': 'Nome da empresa',
  'sort.title': 'Título da vaga',
  'sort.status': 'Em que ponto estou',
  'direction.desc': 'Maior primeiro',
  'direction.asc': 'Menor primeiro',

  'rail.filters': 'Filtros',
  'rail.retrieve': 'Buscar vagas',
  'rail.profile': 'Seu perfil',
  'rail.preferences': 'Preferências de busca',
  'rail.sources': 'De onde elas vêm',

  'legend.summary': 'O que significam Correspondência e Detalhe do anúncio',
  'legend.match': 'Aderência à busca',
  'legend.matchBody': 'relação do anúncio com sua busca. Não mede sua capacidade profissional.',
  'legend.detail': 'Detalhe do anúncio',
  'legend.detailBody':
    'o quanto o anúncio realmente contou. Não são suas chances de ser contratada.',
  'legend.eligible': 'Você pode aceitar',
  'legend.eligibleBody':
    'uma resposta separada, mantida separada. Nunca se mistura com as outras duas.',

  'hidden.eligibility':
    '{count} ocultas, porque cada uma declara uma exigência que você não atende: '
    + 'um país, uma autorização de trabalho, um credenciamento de segurança ou '
    + 'algo parecido.',
  'hidden.eligibilityShowing':
    'Mostrando vagas que declaram uma exigência que você não atende, junto com as demais.',
  'hidden.eligibilityReveal': 'Mostrar essas também',
  'filters.toggle.includeExcludedSeniority': 'Incluir níveis que você deixou de lado',
  'filters.toggle.includeExcludedWorkModel': 'Incluir formatos de trabalho que você deixou de lado',
  'filters.includeExcludedWorkModelHelp':
    'Desligado por padrão, e não faz nada até você dizer que um formato de trabalho nunca deve '
    + 'aparecer. É uma preferência, não um veredito: nada é apagado e isto traz de volta.',
  'hidden.workModel': '{count} de lado por um formato de trabalho que você disse nunca querer ver.',
  'hidden.workModelOne': '1 de lado por um formato de trabalho que você disse nunca querer ver.',
  'hidden.workModelShowing': 'Mostrando formatos de trabalho que você deixou de lado.',
  'hidden.workModelReveal': 'Mostrar essas também',
  'filters.includeExcludedSeniorityHelp':
    'Desligado por padrão, e não faz nada até você nomear um nível no seu perfil. São vagas em ' +
    'um nível que você disse não querer ver, como Staff ou Diretor. Nada é apagado e isto traz ' +
    'de volta.',
  'hidden.seniority': '{count} de lado por estarem em um nível que você disse não querer ver.',
  'hidden.seniorityOne': '1 de lado por estar em um nível que você disse não querer ver.',
  'hidden.seniorityShowing': 'Mostrando níveis que você deixou de lado.',
  'hidden.seniorityReveal': 'Mostrar essas também',
  'hidden.unresolved':
    '{count} de lado porque o anúncio nunca disse onde o empregador contrata. Nada exclui você; ' +
    'nada confirma você também.',
  'hidden.unresolvedOne': '1 de lado porque o anúncio nunca disse onde o empregador contrata.',
  'hidden.unresolvedShowing': 'Mostrando vagas em que o anúncio nunca disse onde o empregador contrata.',
  'hidden.unresolvedReveal': 'Mostrar essas também',
  'hidden.offTarget':
    '{count} ocultas por serem um tipo de trabalho diferente do que você descreveu. '
    + 'Não há nada de errado com elas: não corresponderam ao que você disse procurar.',
  'hidden.offTargetShowing':
    'Mostrando trabalhos que você separou como de outro tipo, junto com os demais.',
  'hidden.offTargetReveal': 'Mostrar esses também',
  'hidden.hideAgain': 'Ocultar de novo',

  'profile.editUnder': 'Edite em "Preferências de busca".',
  'profile.andMore': ' ... e mais {count}',
  'profile.empty': 'Comece no Início descrevendo o trabalho que procura; depois edite suas preferências aqui.',
  'profile.conflictOne':
    'Seu perfil e suas configurações de busca discordam sobre uma coisa.',
  'profile.conflictMany':
    'Seu perfil e suas configurações de busca discordam sobre {count} coisas.',

  // The VALUES stay English in the database and in every query parameter.
  // Only what a person reads changes.
  'eligibility.VERIFIED_ELIGIBLE': 'Nada no caminho',
  'eligibility.LIKELY_ELIGIBLE': 'Provavelmente nada no caminho',
  'eligibility.UNRESOLVED': 'Não disse',
  'eligibility.VERIFIED_NOT_ELIGIBLE': 'Exclui você',
  'band.STRONG': 'Forte',
  'band.GOOD': 'Boa',
  'band.MODERATE': 'Moderada',
  'band.WEAK': 'Fraca',
  // `Pleno` rather than a literal rendering: it is what a mid-level role is
  // called in Brazilian hiring, and the subject decides the word.
  'work_model.REMOTE': 'Remoto',
  'work_model.HYBRID': 'Híbrido',
  'work_model.ONSITE': 'Presencial',
  'contract.FULL_TIME_EMPLOYEE': 'Empregado',
  'contract.CONTRACTOR_B2B': 'Prestador de serviços',
  'contract.EOR': 'Employer of Record',


  'seniority.INTERN': 'Estágio',
  'seniority.JUNIOR': 'Júnior',
  'seniority.MID': 'Pleno',
  'seniority.SENIOR': 'Sênior',
  'seniority.STAFF': 'Staff',
  'seniority.PRINCIPAL': 'Principal',
  'seniority.LEAD': 'Líder',
  'status.DISCOVERED': 'Encontrada',
  'status.SHORTLISTED': 'Tenho interesse',
  'status.TO_APPLY': 'Para enviar',
  'status.APPLIED': 'Candidatura enviada',
  'status.INTERVIEW': 'Em entrevista',
  'status.OFFER': 'Proposta',
  'status.HIRED': 'Contratada',
  'status.REJECTED': 'Recusada',
  'status.WITHDRAWN': 'Retirada',
  'status.ARCHIVED': 'Arquivada',

  'absent.level': 'Nível não informado',
  'absent.contract': 'Tipo de contrato não informado',
  'absent.salary': 'Salário não informado',
  'absent.generic': 'Não informado',
  'absent.levelSentence': 'O anúncio não informa o nível. Tratando como pleno.',

  'employment.stated': 'O anúncio diz isso',
  'employment.likely': 'Provável, pelos benefícios oferecidos',
  'employment.EMPLOYEE': 'Empregado',
  'employment.CONTRACTOR_B2B': 'Prestador de serviços',
  'employment.EOR': 'Por Employer of Record',
  'employment.INTERN': 'Estágio',
  'employment.APPRENTICE': 'Aprendiz',
  'employment.TEMPORARY': 'Temporário',
  'employment.OTHER': 'Outro arranjo',
  'employment.UNRESOLVED': 'Forma de contratação não informada',
  'regime.CLT': 'CLT',
  'regime.PJ': 'PJ',
  'regime.UNRESOLVED': 'Regime não informado',

  'domestic.LIKELY_US_DOMESTIC': 'Provavelmente uma vaga doméstica dos Estados Unidos',
  'domestic.sentence':
    'O anúncio oferece {signal} e não menciona contratar fora dos Estados '
    + 'Unidos. Isso costuma indicar que a vaga é estruturada como emprego nos '
    + 'Estados Unidos. Não é uma recusa, e o empregador não foi perguntado.',
  'domestic.INTERNATIONAL_STATED': 'Menciona contratação em mais de um país',

  'empty.none': 'Nenhuma vaga corresponde a estes filtros.',
  'empty.loading': 'Carregando',
  'empty.broken': 'Não dá para saber como isto está agora.',

  'locale.label': 'Idioma',
  'locale.en': 'EN',
  'locale.pt-BR': 'PT',

  // -- a terceira aba da gaveta ------------------------------------------
  'drawer.tabs.label': 'Detalhes da vaga, o raciocínio por trás da nota e a preparação',
  'drawer.tab.details': 'Detalhes da vaga',
  'drawer.tab.why': 'Por que se alinha à busca',
  'drawer.tab.prepare': 'Preparar candidatura',
  'prep.requirements': 'O que eles pedem',
  'prep.retry': 'Tentar de novo',
  'prep.noRequirements': 'Este anúncio não acionou nenhum dos sinais que você configurou, '
    + 'então não há o que preparar aqui. Isso é um fato sobre o anúncio, não sobre você.',
  'prep.leadWithEvidence': 'Evidências / Preparação: comparado com {n} afirmações confirmadas. '
    + 'As expressões em comum ligam as citações; confira se sustentam o requisito. '
    + 'Essas afirmações não entram na pontuação de aderência à busca.',
  'prep.leadWithoutEvidence': 'Evidências / Preparação desconhecida: nenhuma afirmação confirmada. '
    + 'Isso não altera a pontuação separada de aderência à busca.',
  'prep.noEvidenceYet': 'Importe seu currículo ou escreva o que você já fez, e esta página '
    + 'começa a responder.',
  'prep.goToEvidence': 'Suas evidências de carreira',
  'prep.state.MATCHED': 'Evidência confirmada relacionada',
  'prep.state.PARTIAL': 'Ferramenta ou habilidade citada',
  'prep.state.GAP': 'Nenhum apoio confirmado reconhecido',
  'prep.state.UNRESOLVED': 'Não dá para dizer',
  'prep.meaning.MATCHED': 'Uma expressão reconhecida liga este requisito a uma evidência confirmada. '
    + 'Confira as citações para avaliar se a evidência sustenta o trabalho.',
  'prep.meaning.PARTIAL': 'Você citou isso -- uma ferramenta ou uma habilidade -- sem '
    + 'evidência confirmada de ter feito o trabalho. Ter usado algo e ter feito o trabalho '
    + 'são respostas diferentes.',
  'prep.meaning.GAP': 'Nenhuma evidência de apoio confirmada foi reconhecida para este requisito. '
    + 'Isso não demonstra falta de experiência.',
  'prep.meaning.UNRESOLVED': 'Este sistema não consegue dizer. O requisito veio de um sinal '
    + 'sem expressões para comparar com uma afirmação, então nenhum veredito aqui seria '
    + 'honesto.',
  'prep.theySay': 'Eles dizem',
  'prep.youSay': 'Você diz',
  'prep.matchedOn': 'Correspondeu pela expressão "{phrase}".',
  'prep.fromDocument': 'A linha de onde isto foi lido: {line}',
  'prep.concernText.geography_unresolved': 'O anúncio não diz onde contrata. Vale perguntar '
    + 'antes.',
  'prep.concernText.likely_us_domestic': 'Provavelmente emprego nos Estados Unidos: oferece '
    + '{signal} e não menciona contratar em outro lugar. Isto é contexto, não uma recusa.',
  'prep.concernText.contract_explicit': 'O anúncio DIZ que é {relationship}.',
  'prep.concernText.contract_suggested': 'O anúncio sugere que é {relationship}, pelo que '
    + 'oferece e não pelo que diz. Um benefício não é uma afirmação.',
  'prep.concernText.seniority_unstated': 'O anúncio não diz o nível. Vale perguntar o que '
    + 'eles querem dizer.',
  'prep.concerns': 'Resolver antes de se candidatar',
  'prep.concernsLede': 'Coisas que não têm a ver com sua capacidade de fazer o trabalho.',
  'prep.concern.eligibility': 'Você pode aceitar',
  'prep.concern.employment': 'Vínculo',
  'prep.concern.contract': 'Contrato',
  'prep.concern.seniority': 'Nível',
  'prep.concern.fit': 'Tipo de trabalho',
  'prep.notRight': 'Não está certo? Diga',
  'prep.reviewLede': 'Isto registra o que você acha. Não muda nota, não reescreve citação '
    + 'e não cria nenhuma afirmação sobre você.',
  'prep.verdict.SUPPORTS': 'Isto sustenta sim',
  'prep.verdict.PARTIALLY_SUPPORTS': 'Só em parte',
  'prep.verdict.DOES_NOT_SUPPORT': 'Isto não sustenta',
  'prep.verdict.EVIDENCE_MISSING': 'Já fiz isso, mas não está no meu perfil',
  'prep.withdraw': 'Retirar esta resposta',
  'prep.notePlaceholder': 'Uma nota para você. Opcional.',
  'prep.noteLabel': 'Sua nota sobre {requirement}',
  'prep.missingLede': 'Dizer isso não afirmou nada. Escreva com suas palavras e vira uma '
    + 'evidência que você pode usar.',
  'prep.checklist': 'Antes de se candidatar',
  'prep.checklistLede': 'O que aconteceu até aqui. Não é nota, e não é uma lista que você '
    + 'precise terminar.',
  'prep.check.eligibility': 'Elegibilidade revisada ({n} ainda a resolver)',
  'prep.check.requirements': 'Requisitos lidos ({n} deles)',
  'prep.check.evidence': 'Evidências disponíveis ({n} confirmadas)',
  'prep.check.gaps': 'Lacunas entendidas ({n} sem resposta)',
  'ledger.source.RESUME': 'Do seu currículo',
  'ledger.source.LINKEDIN': 'Do LinkedIn',
  'ledger.source.SELF_ATTESTED': 'Você escreveu isto',
  'ledger.source.DOCUMENT': 'De um documento',

  // -- as evidências -----------------------------------------------------
  'rail.evidence': 'Suas evidências de carreira',
  'ledger.title': 'Suas evidências de carreira',
  'ledger.close': 'Fechar',
  'ledger.retry': 'Tentar de novo',
  'ledger.heading': 'Suas evidências',
  'ledger.lede': 'O que o Career Agent sabe que você de fato fez. O que você PROCURA é outra coisa e '
    + 'fica no seu perfil de carreira.',
  'ledger.empty': 'Nada foi confirmado sobre você ainda. Importe um currículo acima, ou '
    + 'escreva algo que você fez.',
  'ledger.uses.heading': 'Como o Career Agent usa minhas evidências?',
  'ledger.uses.prepare': 'Ao preparar uma candidatura. Cada exigência de um anúncio é '
    + 'respondida a partir do que você confirmou, e de mais nada.',
  'ledger.uses.notScore': 'Não nas recomendações. Um anúncio é pontuado pelo que a '
    + 'empresa escreveu sobre o trabalho; nada sobre você é lido ao pontuar.',
  'ledger.uses.notEligibility': 'Não na elegibilidade. Ela é onde a empresa diz que pode '
    + 'contratar, conferido com os países e escopos das suas preferências.',
  'ledger.uses.where': 'O que move as recomendações é o que você procura, em Preferências no seu '
    + 'perfil de carreira.',
  'ledger.search': 'Buscar nas suas evidências',
  'ledger.searchPlaceholder': 'Buscar experiência, habilidades, empresas, ferramentas...',
  'ledger.showAllInGroup': 'Mostrar todas as {n}',
  'ledger.selectStart': 'Selecionar várias',
  'ledger.selectDone': 'Pronto',
  'ledger.selectGroup': 'Selecionar tudo que aparece',
  'ledger.selectClear': 'Limpar seleção',
  'ledger.selectOne': 'Selecionar "{text}"',
  'ledger.selectedCount': '{n} selecionadas',
  'ledger.bulkPartly': '{done} deixadas de lado. {failed} não deu -- abra essas e tente de novo.',
  'ledger.bulkRetire': 'Tirar do perfil',
  'ledger.bulkRetireConfirm':
    'Deixar {n} afirmações de lado? Elas mantêm o histórico e você pode voltar a '
    + 'sustentá-las quando quiser.',

  'ledger.hint.EMPLOYMENT':
    'Diga o que você fez de fato. A tarefa, as ferramentas e o resultado quando você souber.',
  'ledger.example.EMPLOYMENT':
    'Cuidei da escala semanal de uma equipe de doze, e reduzi pela metade as trocas de última hora.',
  'ledger.hint.PROJECT': 'O que era o projeto, o que você construiu e o que mudou por causa disso.',
  'ledger.example.PROJECT':
    'Refiz a forma como controlávamos o estoque de duas lojas, para nada ser contado duas vezes.',
  'ledger.hint.ACHIEVEMENT': 'O que aconteceu, e como você sabe que aconteceu.',
  'ledger.example.ACHIEVEMENT': 'Reduzi erros de fatura de 40 por mês para menos de 5.',
  'ledger.hint.SKILL': 'Uma habilidade ou ferramenta que você usou de verdade. Curto.',
  'ledger.example.SKILL': 'Organização de agendas',
  'ledger.hint.TOOL': 'O nome da ferramenta, como quem usa escreve.',
  'ledger.example.TOOL': 'Excel',
  'ledger.hint.CERTIFICATION': 'O nome oficial, e quem emitiu.',
  'ledger.example.CERTIFICATION': 'Project Management Professional -- PMI',
  'ledger.hint.EDUCATION': 'A formação e a instituição.',
  'ledger.example.EDUCATION': 'Bacharelado em Comunicação Social -- USP',
  'ledger.noMatch': 'Nada aqui corresponde a isso.',
  'ledger.origin': 'Texto original',
  'ledger.edit': 'Editar',
  'ledger.editLabel': 'Sua redação corrigida',
  'ledger.save': 'Salvar a correção',
  'ledger.retire': 'Tirar do perfil',
  'ledger.retireConfirm': 'Tirar isto do seu perfil?\n\n{text}\n\nIsto fica guardado com o histórico e deixa de '
    + 'ser usado quando uma candidatura é preparada. Você pode colocar de volta.',
  'ledger.confirm': 'Usar isto de novo',
  'ledger.retiredTag': 'De lado',
  'ledger.draftTag': 'Ainda não confirmada',
  'ledger.confirmDraft': 'Confirmar',
  'ledger.revision': 'revisão {n}',
  'ledger.addHeading': 'Adicionar ao seu perfil',
  'ledger.addLede': 'Você não precisa escrever de um jeito técnico -- conte do seu jeito, '
    + 'como você explicaria para outra pessoa. Fica registrado com as suas palavras, e nada '
    + 'é citado, porque não existe documento por trás.',
  'ledger.addText': 'Conte sobre isso',
  'ledger.addType': 'O que você está adicionando?',
  'ledger.addSubmit': 'Confirmar isto sobre mim',
  'ledger.addEmpty': 'Escreva algo primeiro.',

  // -- ler o currículo ---------------------------------------------------
  'cv.heading': 'Ler seu currículo',
  'cv.privacy': 'Seu currículo é lido pelo Career Agent neste computador. Não sobe para '
    + 'lugar nenhum, nenhum modelo de nenhum tipo o vê, e o arquivo nunca é guardado.',
  'cv.choose': 'Escolher um arquivo',
  'cv.noFile': 'Nenhum arquivo escolhido ainda',
  'cv.supported': 'Lê {kinds}',
  'cv.nothingConfirmed': 'Nada é confirmado por ser lido. Cada linha vira uma proposta à '
    + 'espera da sua resposta.',
  'cv.reading': 'Lendo {name} neste computador...',
  'cv.pendingCount': '{n} ainda por responder',
  'cv.reviewed': 'Tudo respondido',
  'cv.importMeta': '{confirmed} confirmadas, {rejected} recusadas, {total} lidas',
  'cv.continueReview': 'Continuar a revisão',
  'cv.reopenReview': 'Ver de novo',
  'cv.backToEvidence': 'Voltar às suas evidências',
  'cv.reviewSafety': 'Cada resposta é salva sozinha, na hora. Você pode fechar isto e voltar '
    + 'para o resto depois.',
  'cv.fromCv': 'Do seu currículo',
  'cv.carriesFigure': 'Isto traz um número. Confira se diz o que você lembra ter dito -- o '
    + 'número nunca é tirado da frase.',
  'cv.accept': 'Sim, isso é verdade',
  'cv.acceptLabel': 'Confirmar: {text}',
  'cv.edit': 'Não exatamente -- reescrever',
  'cv.editLabel': 'Sua redação corrigida',
  'cv.saveEdit': 'Confirmar minha redação',
  'cv.reject': 'Não, descartar',
  'cv.rejectLabel': 'Descartar: {text}',
  'cv.decision.ACCEPTED': 'Confirmada',
  'cv.decision.EDITED': 'Confirmada, sua redação',
  'cv.decision.REJECTED': 'Descartada',
  'cv.decision.PENDING': 'Sem resposta',
  // -- Career Evidence V2: a CV read as experiences ---------------------
  'cv.archivedState': 'Arquivada',
  'cv.importMetaJobs': '{experiences} experiências, {confirmed} confirmadas, {rejected} rejeitadas, {total} lidas',
  'cv.inspect': 'Ver o conteúdo',
  'cv.archivedFlash': '{name} foi arquivado. Nada nele espera por você agora; Restaurar traz de '
    + 'volta exatamente como estava.',
  'lifecycle.delete': 'Excluir...',
  'lifecycle.deleted': '{name} foi excluído.',
  'cvr.heading': 'O que {name} diz, por experiência',
  'cvr.back': 'Voltar às suas evidências',
  'cvr.found': '{experiences} experiências encontradas / {suggestions} sugestões / {attention} precisam de atenção',
  'cvr.waiting': '{waiting} ainda por responder, {confirmed} confirmadas até agora.',
  'cvr.allAnsweredLede': 'Tudo respondido: {confirmed} confirmadas, {rejected} rejeitadas.',
  'cvr.archivedLede': 'Esta leitura está arquivada. Nada nela espera por você e nada pode ser '
    + 'respondido até você restaurá-la.',
  'cvr.oneAtATime': 'Nada é confirmado até você confirmar, uma afirmação por vez. Cada resposta é salva na hora.',
  'cvr.experiences': 'Experiências, das mais recentes às mais antigas',
  'cvr.otherSections': 'Todo o resto',
  'cvr.rowCounts': '{waiting} esperando / {confirmed} confirmadas',
  'cvr.attentionChip': '{n} para conferir',
  'cvr.review': 'Revisar',
  'cvr.reviewLabel': 'Revisar {company}, {role}',
  'cvr.reviewSectionLabel': 'Revisar {name}',
  'cvr.next': 'Próximo item para revisar',
  'cvr.allAnswered': 'Nada mais espera por você nesta leitura.',
  'cvr.addExperience': 'Adicionar uma experiência que faltou',
  'cvr.archive': 'Arquivar',
  'cvr.archived': 'Arquivada. Nada nesta leitura espera por você agora.',
  'cvr.restore': 'Restaurar',
  'cvr.restored': 'Restaurada, exatamente como estava.',
  'cvr.delete': 'Excluir esta leitura...',
  'cvr.deleteTitle': 'Excluir {name} permanentemente?',
  'cvr.deleteAll': 'Isso remove a importação e todas as {removed} sugestões dela ({pending} sem '
    + 'resposta, {rejected} rejeitadas). Nada do que você confirmou veio dela.',
  'cvr.deleteKeeps': '{removed} sugestões não confirmadas são removidas ({pending} sem resposta, '
    + '{rejected} rejeitadas). As {confirmed} que você confirmou continuam nas suas evidências, e '
    + 'as linhas de onde vieram são mantidas como fonte.',
  'cvr.deleteArchiveInstead': 'Para guardar e manter tudo, arquive em vez de excluir.',
  'cvr.deleteForever': 'Isso não pode ser desfeito.',
  'cvr.deleteConfirm': 'Excluir permanentemente',
  'cvr.cancel': 'Cancelar',
  'cvr.companyUnknown': 'Empresa não informada',
  'cvr.roleUnknown': 'Cargo não informado',
  'cvr.datesUnknown': 'Datas não informadas',
  'cvr.period': '{start} a {end}',
  'cvr.periodCurrent': '{start} até hoje',
  'cvr.editDetails': 'Corrigir empresa, cargo ou datas',
  'cvr.saveDetails': 'Salvar detalhes',
  'cvr.createExperience': 'Adicionar experiência',
  'cvr.saved': 'Salvo. As linhas do documento continuam as mesmas.',
  'cvr.created': 'Experiência adicionada.',
  'cvr.company': 'Empresa',
  'cvr.role': 'Cargo',
  'cvr.start': 'Mês de início',
  'cvr.end': 'Mês de término',
  'cvr.current': 'Ainda trabalho aqui',
  'cvr.writtenAs': 'O documento diz: {text}',
  'cvr.backToRead': 'Voltar a todas as experiências',
  'cvr.groupCounts': '{waiting} de {total} ainda por responder.',
  'cvr.unresolved': 'O Career Agent não conseguiu ler: {what}. Preencha o que você sabe; nada é adivinhado.',
  'cvr.missing.company': 'empresa',
  'cvr.missing.role': 'cargo',
  'cvr.missing.dates': 'datas',
  'cvr.missing.structure': 'estrutura',
  'cvr.documentSaid': 'O que o documento dizia',
  'cvr.sourceLine': 'Linha {line}: {text}',
  'cvr.mergeInto': 'Juntar com',
  'cvr.merge': 'Juntar a esta experiência',
  'cvr.merged': 'Juntadas. Todas as sugestões e linhas de origem foram junto.',
  'cvr.deleteExperience': 'Remover esta experiência vazia',
  'cvr.experienceDeleted': 'Experiência removida.',
  'cvr.noExperience': 'Nenhuma experiência',
  'cvr.moveTo': 'Mover para',
  'cvr.moveLabel': 'Mover para outra experiência: {text}',
  'cvr.moveSelectedTo': 'Mover as sugestões selecionadas para',
  'cvr.moveSelected': 'Mover selecionadas',
  'cvr.moved': '{n} movidas. O texto e as linhas de origem não mudaram.',
  'cvr.splitSelected': 'Separar em uma nova experiência',
  'cvr.splitSave': 'Criar experiência com as selecionadas',
  'cvr.split': '{n} separadas em uma nova experiência.',
  'cvr.rejectSelected': 'Rejeitar selecionadas',
  'cvr.rejectedN': '{n} rejeitadas. Nada foi confirmado.',
  'cvr.deleteSelected': 'Excluir selecionadas...',
  'cvr.deleteSuggestions': 'Excluir {n} sugestões permanentemente? As confirmadas nunca são '
    + 'excluídas aqui. Isso não pode ser desfeito.',
  'cvr.deletedN': '{n} excluídas.',
  'cvr.selectedN': '{n} selecionadas',
  'cvr.selectLabel': 'Selecionar: {text}',
  'cvr.confirmedNote': 'Confirmada. Para retirar, use Retirar nas Evidências de carreira.',
  'cvr.undoReject': 'Desfazer rejeição',
  'cvr.deleteOne': 'Excluir',
  'cvr.deleteOneSure': 'Excluir de vez?',
  'cvr.thisExperience': 'Esta experiência',
  'cvr.mergeFold': 'Juntar com outra experiência',
  'cvr.mergeHelp': 'Move todas as sugestões e linhas de origem desta experiência para a '
    + 'que você escolher e remove esta. Nada é confirmado.',
  'cvr.deleteOneLabel': 'Excluir esta sugestão permanentemente: {text}',
  'cvr.fromLine': 'Do seu currículo, linha {line}',
  'cvr.asWritten': 'Como está escrito no arquivo',
  'cvr.answered.ACCEPTED': 'Confirmada.',
  'cvr.answered.EDITED': 'Confirmada com suas palavras.',
  'cvr.answered.REJECTED': 'Rejeitada.',
  'cvr.answered.PENDING': 'De volta a sem resposta.',
  'cvr.attention.duplicate': 'A mesma frase aparece antes nesta leitura.',
  'cvr.attention.structure': 'Confira a empresa, o cargo ou as datas desta experiência.',
  'cvr.attention.unplaced': 'Ainda não está em nenhuma experiência.',
  'cvr.section.experience': 'Experiência sem lugar',
  'cvr.section.volunteering': 'Voluntariado',
  'cvr.section.internships': 'Estágios',
  'cvr.section.freelance': 'Trabalho autônomo',
  'cvr.section.skills': 'Habilidades',
  'cvr.section.tools': 'Ferramentas',
  'cvr.section.education': 'Formação',
  'cvr.section.certifications': 'Certificações',
  'cvr.section.projects': 'Projetos',
  'cvr.section.activities': 'Atividades',
  'cvr.section.awards': 'Prêmios',
  'cvr.section.languages': 'Idiomas',
  'cvr.section.unplaced': 'Trabalho ainda sem experiência',
  'career.state.UNRESOLVED': 'Ainda não sei',
  'career.confirmOne': 'Confirmar esta',
  'career.confirmOneLabel': 'Confirmar: {text}',

  // -- o pacote de evidencias do candidato --------------------------------
  'intake.heading': 'Fontes e importações',
  'intake.lede': 'De onde vieram as evidências acima. Uma importação é uma passagem pelos '
    + 'seus documentos, e nada nela é verdade sobre você até você dizer que é.',
  'intake.waiting': '{n} para olhar',
  'intake.allAnswered': 'Tudo respondido',
  'intake.readBy.SELF': 'Lido pelo Career Agent neste computador.',
  'intake.readBy.EXTERNAL_AI': 'Lido por um assistente que você escolheu.',
  'intake.readBy.MANUAL': 'Escrito à mão.',
  'intake.readByNamed': 'Lido por {name}, um assistente que você escolheu.',
  'intake.progressLabel': '{done} de {total} olhados',
  'intake.startReview': 'Começar revisão',
  'intake.continueReview': 'Continuar revisão',
  'intake.reopenReview': 'Revisar de novo',
  'intake.putAway': 'Arquivar',
  // Ver a nota em ingles acima: por onde comecar, sem virar uma classificacao.
  'intake.startHeading': 'Por onde começar',
  'intake.startNavigationOnly': 'Esta é uma ordem de leitura, não uma classificação. Nada '
    + 'fica escondido em nenhuma etapa, toda afirmação ainda pode ser confirmada, corrigida, '
    + 'recusada ou deixada para depois, e estar mais abaixo não torna nada menos verdadeiro.',
  'intake.startEssential': '{waiting} de {total} afirmações nas três primeiras etapas ainda '
    + 'esperam resposta. Responda essas e o produto já tem com o que trabalhar, de {all} no '
    + 'pacote.',
  'intake.startEssentialDone': 'As três primeiras etapas estão respondidas. {waiting} de '
    + '{all} afirmações do pacote ainda esperam, e nenhuma delas bloqueia as outras.',
  'intake.startEssentialMeans': 'Ter com o que trabalhar significa: seus documentos '
    + 'concordam sobre as datas, toda afirmação sobre trabalho diz para quem foi, e seu '
    + 'emprego mais recente está respondido. O resto pode esperar, e nada fica escondido '
    + 'enquanto espera.',
  'intake.startFocus': '{waiting} de {total} afirmações que mencionam "{term}" ainda esperam '
    + 'resposta. Foi esse o requisito que trouxe você até aqui.',
  'intake.openStep': 'Abrir esta etapa',
  'intake.openStepLabel': 'Abrir a etapa {name}',
  'intake.stepNone': 'Nenhuma dessas',
  'intake.stepCount': '{answered} de {total} respondidas nesta etapa',
  'intake.stepBlocking': 'Estas vêm primeiro porque nada mais no pacote pode ser confirmado '
    + 'enquanto seus documentos discordam sobre as datas.',
  'intake.step.SETTLE_DISAGREEMENTS': 'Resolver onde seus documentos discordam',
  'intake.step.NAME_THE_EMPLOYER': 'Dizer para quem foi cada uma',
  'intake.step.RECENT_WORK': 'Seu emprego mais recente',
  'intake.step.MEASURABLE_OUTCOMES': 'Coisas que você mediu',
  'intake.step.EARLIER_WORK': 'Trabalhos e projetos anteriores',
  'intake.step.SKILLS_AND_TOOLS': 'Habilidades e ferramentas',
  'intake.step.STUDY_AND_CERTIFICATES': 'Formação e certificados',
  'intake.step.ANYTHING_ELSE': 'Qualquer outra coisa',
  'intake.stepWhy.SETTLE_DISAGREEMENTS': 'Dois dos seus documentos dão datas diferentes para '
    + 'o mesmo período. Responder isso uma vez libera todas as afirmações sobre ele.',
  'intake.stepWhy.NAME_THE_EMPLOYER': 'Estas descrevem trabalho mas não dizem para quem foi, '
    + 'então nada pode ser atribuído a elas ainda.',
  'intake.stepWhy.RECENT_WORK': 'O emprego de que você provavelmente lembra em mais detalhe, '
    + 'e o que a maioria dos empregadores pergunta primeiro.',
  'intake.stepWhy.MEASURABLE_OUTCOMES': 'Afirmações que trazem um número, mantidas na frase '
    + 'em que você o escreveu.',
  'intake.stepWhy.EARLIER_WORK': 'Todo o resto sobre empregos e projetos, do mais recente '
    + 'para o mais antigo.',
  'intake.stepWhy.SKILLS_AND_TOOLS': 'O que seus documentos dizem que você sabe fazer e com '
    + 'o que você já trabalhou.',
  'intake.stepWhy.STUDY_AND_CERTIFICATES': 'Formação, cursos e certificados.',
  'intake.stepWhy.ANYTHING_ELSE': 'Afirmações que não se encaixaram em nenhuma das acima. '
    + 'Nada fica de fora desta lista.',
  // Ver a nota em ingles acima: qual leitura dos documentos dela esta em vigor.
  'intake.status.ACTIVE': 'Ativa',
  'intake.status.SUPERSEDED': 'Substituída',
  'intake.status.DISCARDED': 'Arquivada',
  'intake.status.INCOMPLETE': 'Sem nada dentro',
  'intake.useThisOne': 'Usar esta no lugar',
  'intake.restore': 'Restaurar',
  'intake.inspect': 'Ver detalhes',
  'intake.incompleteWhy': 'Esta importação não produziu nenhuma afirmação, então não há o que '
    + 'revisar. Ela não substituiu nada.',
  'intake.supersededBy': 'Deixada de lado quando {name} foi preparada a partir dos seus '
    + 'documentos. Nada nela foi perdido, e você pode voltar a ela.',
  'intake.restoredActive': '{name} voltou, e é a leitura em vigor.',
  'intake.restoredAside': '{name} voltou e ficou de lado. Você continua revisando a que estava '
    + 'aberta; use "Revisar esta no lugar" para trocar.',
  'intake.putAwayConfirm': 'Arquivar esta importação?\n\n{name}\n\nHá {waiting} propostas sem resposta nela. '
    + 'Nada é apagado e nada que você já confirmou é tocado. Você pode restaurar.',
  'intake.overviewHeading': 'O que {name} diz sobre você',
  'intake.overviewLede': '{waiting} de {total} ainda esperam uma resposta sua. '
    + 'Estão agrupados para você fazer uma coisa de cada vez.',
  'intake.overviewDone': 'Todos os {total} respondidos.',
  'intake.documents': 'Os documentos por trás disto',
  'intake.source.RESUME': 'Seu currículo',
  'intake.source.LINKEDIN': 'Seu perfil',
  'intake.source.DOCUMENT': 'Um documento',

  'intake.conflictHeading': 'Onde seus documentos discordam',
  'intake.conflictNothingConfirmed': 'Escolher as datas resolve como ler dois documentos. '
    + 'Não confirma nada por si só -- cada afirmação continua vindo a você uma a uma.',
  'intake.conflictLede': 'Seus documentos dão datas diferentes para isto. Resolver uma vez '
    + 'responde a pergunta para as {n} afirmações sobre isto.',
  'intake.conflictUnnamed': 'Um período da sua história',
  'intake.conflictOpen': 'Sem resposta',
  'intake.conflictSettled': 'Resolvido',
  'intake.conflictRead': 'Lido como {reading}',
  'intake.conflictFrom': 'De {sources}, em {n} afirmações',
  'intake.conflictFromOne': 'De {sources}, em uma afirmação',
  'intake.conflictChoose': 'Estas datas estão certas',
  'intake.conflictChooseLabel': 'Escolher as datas {dates}',
  'intake.conflictChosen': 'Você escolheu estas datas.',
  'intake.conflictReopen': 'Mudar esta resposta',
  'intake.conflictSaved': 'Salvo. Aquelas afirmações voltaram para a fila.',
  'intake.span': '{start} a {end}',
  'intake.spanCurrent': '{start}, ainda lá',
  'intake.spanOpen': '{start}, sem fim informado',
  'intake.noDate': 'sem data informada',
  'intake.noReading': 'nenhuma data que este programa consiga ler',

  'intake.groupsHeading': 'Por onde começar',
  'intake.groupsLede': 'O trabalho é agrupado por empregador, porque essa é a pergunta que '
    + 'você consegue responder: o que isto diz sobre o meu tempo lá.',
  'intake.groupPeriod': '{span} -- {n} afirmações',
  'intake.groupPeriodOne': '{span} -- uma afirmação',
  'intake.groupSize': '{n} afirmações',
  'intake.groupSizeOne': 'Uma afirmação',
  'intake.groupKinds': 'Contém: {kinds}',
  'intake.groupConflicted': '{n} destas esperam datas sobre as quais seus documentos discordam.',
  'intake.openGroup': 'Olhar estas',
  'intake.openGroupLabel': 'Olhar as afirmações sobre {name}',
  'intake.statesHeading': 'O que você já decidiu',
  'intake.stateChip': '{state} ({n})',
  'intake.state.UNREVIEWED': 'Ainda não olhado',
  'intake.state.CONFIRMED': 'Confirmada',
  'intake.state.CORRECTED_BY_USER': 'Confirmada, sua redação',
  'intake.state.CONFLICT': 'Esperando datas',
  'intake.state.UNRESOLVED': 'Ainda não sei',
  'intake.state.REJECTED': 'Descartada',

  'intake.backToPackage': 'Voltar à visão geral',
  'intake.listLede': '{waiting} de {total} aqui ainda esperam resposta.',
  'intake.listDone': 'Todas as {total} daqui estão respondidas.',
  'intake.search': 'Encontrar uma destas',
  'intake.focusedOn': 'Mostrando o que espera resposta e menciona {term}. Nada fica escondido para sempre.',
  'intake.dropFocus': 'Mostrar tudo que espera',
  'intake.searchPlaceholder': 'uma palavra da afirmação',
  'intake.noMatch': 'Nada aqui corresponde a isso.',
  'intake.emptyList': 'Nada foi arquivado aqui ainda.',
  'intake.truncated': 'Mostrando {shown} de {matched}. Responda algumas e o resto aparece.',
  'intake.noBulkConfirm': 'Não existe "confirmar tudo". Qualquer coisa confirmada aqui pode '
    + 'ir para uma candidatura de verdade, e um clique não pode significar que você leu todas.',
  'intake.batchUnsure': 'Deixar as {n} restantes como ainda não sei',
  'intake.batchUnsureConfirm': 'Marcar {n} afirmações como "ainda não sei"? Nada é confirmado '
    + 'e nada é descartado -- elas deixam de aparecer como intocadas, e você pode responder '
    + 'qualquer uma depois.',
  'intake.asItArrived': 'Como o pacote escreveu',
  'intake.fromDocument': 'De {document}',
  'intake.foundIn': 'Encontrado em {where}',
  'intake.datesWrote': 'Seu documento escreveu {span}',
  'intake.datesRead': 'O Career Agent leu isso como {span}',
  'intake.tools': 'Ferramentas citadas aqui: {tools}',
  'intake.figure': 'Isto traz um número, na frase em que você o disse: "{sentence}". '
    + 'O número nunca é tirado da frase.',
  'intake.claimConflicted': 'Esta afirmação traz datas sobre as quais seus documentos '
    + 'discordam. Resolva uma vez, lá em cima, e ela volta para cá.',
  'intake.settleFirst': 'Resolver as datas primeiro',
  'intake.confirm': 'Sim, isso é verdade',
  'intake.unsure': 'Ainda não sei',
  'intake.reopen': 'Responder de novo',
  'intake.retireInLedger': 'Isto agora é uma afirmação que você sustenta. Aposente-a nas suas '
    + 'evidências abaixo, o que preserva o histórico.',

  // -- tipos de afirmação ------------------------------------------------
  'claimGroup.EMPLOYMENT': 'Experiência',
  'claimGroup.PROJECT': 'Projetos',
  'claimGroup.ACHIEVEMENT': 'Conquistas',
  'claimGroup.SKILL': 'Habilidades',
  'claimGroup.TOOL': 'Ferramentas',
  'claimGroup.EDUCATION': 'Formação',
  'claimGroup.CERTIFICATION': 'Certificações',
  'claimGroup.METRIC': 'Resultados e números',
  'claim.type.EMPLOYMENT': 'Trabalho que você fez',
  'claim.type.PROJECT': 'Um projeto',
  'claim.type.ACHIEVEMENT': 'Algo que você conquistou',
  'claim.type.SKILL': 'Uma habilidade',
  'claim.type.TOOL': 'Uma ferramenta que você conhece',
  'claim.type.EDUCATION': 'Formação',
  'claim.type.CERTIFICATION': 'Uma certificação',
  'claim.type.METRIC': 'Um número',
  // -- o resumo diário ----------------------------------------------------
  'daily.open': 'Hoje',
  'daily.title': 'Vale olhar hoje',
  'daily.close': 'Fechar',
  'daily.retry': 'Tentar de novo',
  'daily.worthLooking': 'vagas que valem uma olhada',
  'daily.lastLooked': 'Você marcou como lido em {when}.',
  'daily.neverLooked': 'Você nunca marcou isto como lido, então a primeira seção usa a data '
    + 'em que o quadro publicou.',
  'daily.markRead': 'Já li isto',
  'daily.nothing': 'Nada aqui hoje.',
  'daily.posted': 'publicada em {date}',
  'daily.firstSeen': 'vista primeiro em {date}',
  'daily.noDate': 'sem data',
  'daily.unscored': 'sem nota',
  'daily.section.recent.title': 'Novas nos últimos {n} dias',
  'daily.section.recent.lead': 'Filtradas pela data em que o quadro publicou, ORDENADAS pela '
    + 'mesma nota de correspondência de sempre. A recência decide o que está nesta seção; não '
    + 'decide o que fica no topo dela.',
  'daily.section.since_last_review.title': 'Desde a última vez que você olhou',
  'daily.section.since_last_review.lead': 'Vagas que esta máquina passou a ter depois da sua '
    + 'última revisão. Isso é quando NÓS as vimos, não quando o empregador as escreveu -- são '
    + 'fatos diferentes e nenhum substitui o outro.',
  'daily.section.best.title': 'Melhores correspondências agora',
  'daily.section.best.lead': 'A mesma nota que os cartões mostram, na mesma ordem. Nada é '
    + 'reordenado aqui.',
  'daily.section.unresolved.title': 'Esperando uma resposta',
  'daily.section.unresolved.lead': 'Boas correspondências cuja elegibilidade ninguém resolveu. '
    + 'Uma pergunta ao recrutador resolveria cada uma.',
  'daily.section.tracking.title': 'Você está acompanhando',
  'daily.section.tracking.lead': 'Qualquer vaga que você salvou ou moveu. Elas continuam '
    + 'visíveis independentemente do que um recálculo decida.',
  'daily.unscoredHead': 'Estas vagas ainda não foram pontuadas.',
  'daily.unscoredBody': 'Há {n} vagas aqui e nenhuma está pontuada com as suas '
    + 'configurações atuais, então hoje não tem o que mostrar. Recalcular acontece no seu '
    + 'computador e não custa nada.',
  // -- o que colocar primeiro, para uma vaga -----------------------------
  'resume.heading': 'O que colocar primeiro, se você se candidatar',
  'resume.lede': 'Suas próprias frases confirmadas, na ordem que este anúncio favorece. Nada '
    + 'é reescrito e nada novo é escrito: isto apenas escolhe e ordena o que você já disse '
    + 'que é verdade.',
  'resume.answers': 'Responde: {list}',
  'resume.nothingSpeaks': 'Nada que você confirmou responde ao que este anúncio pede. Vale '
    + 'saber disso antes de escrever qualquer coisa.',
  'resume.spare': '{n} outros fatos confirmados',
  'resume.spareLede': 'Continuam verdadeiros, e este anúncio não os favorece. O que entra no '
    + 'seu documento é decisão sua.',
  'resume.gapsHead': 'E o que nada seu responde',
  'resume.gapsLede': 'Isto fica na página. Um documento escrito sem saber disso é um '
    + 'documento que você terá de defender numa entrevista.',
  // -- saude das fontes, em palavras -------------------------------------
  'source.state.HEALTHY': 'Funcionando',
  'source.state.ATTENTION': 'Precisa de atenção',
  'source.state.NEEDS_SETUP': 'Precisa de chave ou cota',
  'source.state.WAITING': 'Esperando permissão',
  'source.state.NOT_RUN': 'Ainda não rodou',
  'source.state.DISABLED': 'Desligada, pelas regras deles',
  'source.state.BLOCKED_PROVIDER': 'Acesso bloqueado ou indisponível',
  'source.state.DISABLED_QUOTA': 'Desligada, cota esgotada',
  'source.state.NOTHING_PUBLISHED': 'Sem mural de vagas publicado',
  // -- the job card ------------------------------------------------------
  'card.gatedBecause': 'Não elegível: {reason}',
  'card.gated': 'Este anúncio declara uma exigência que você não atende',
  'card.offTarget': 'Não é o tipo de trabalho que você pediu.',
  'card.offTargetBecause': 'Não é o trabalho que você pediu. {reason}',
  'card.where': 'Local',
  'card.contract': 'Contrato',
  'card.salary': 'Remuneração',
  'card.salaryUnstated': 'Remuneração não informada',
  'card.toolsLabel': 'Ferramentas citadas neste anúncio',
  'card.moreTools': 'Mais {n}. Abra a vaga para ver.',
  'card.posted': 'Publicada: {date}',
  'card.noPostedDate': 'Nenhuma data de publicação registrada',
  'card.you': 'Você:',
  // -- the filter rail ---------------------------------------------------
  'filters.section.find': 'Buscar',
  'filters.section.findHelp': 'Procura no anúncio inteiro, não só no título da vaga.',
  'filters.section.quick': 'Filtros rápidos',
  'filters.section.quality': 'Aderência à busca e detalhe do anúncio',
  'filters.section.qualityHelp': 'Dois números separados. Nenhum dos dois é uma previsão sobre suas '
    + 'chances.',
  'filters.preset.all': 'Tudo',
  'filters.preset.allHelp': 'Todas as vagas coletadas até agora, sem nenhum filtro.',
  'filters.preset.strong': 'Forte aderência à busca',
  'filters.preset.strongHelp': 'Vagas com 70 ou mais de proximidade com o trabalho que você quer. Não '
    + 'diz nada sobre você poder aceitá-las.',
  'filters.preset.eligible': 'Nada no caminho',
  'filters.preset.eligibleHelp': 'Só vagas em que nada no anúncio te exclui. Vagas que nunca disseram onde '
    + 'contratam ficam de fora, porque silêncio não é permissão.',
  'filters.preset.applied': 'Já me candidatei',
  'filters.preset.appliedHelp': 'Vagas para as quais você realmente enviou uma candidatura.',
  'filters.toggle.saved': 'Só as que eu salvei',
  'filters.toggle.hasSalary': 'Só as que informam remuneração',
  'filters.toggle.hasSalaryHelp': 'A maioria não informa. Isto vai esconder muitas vagas reais.',
  'filters.toggle.remote': 'Só remotas',
  'filters.toggle.latam': 'Aberta para a América Latina',
  'filters.toggle.latamHelp': 'O anúncio citou a América Latina, ou um país dela, como lugar onde '
    + 'contrata.',
  'filters.toggle.worldwide': 'Diz que contrata no mundo todo',
  'filters.toggle.worldwideHelp': 'O anúncio disse isso com todas as letras. Remoto sozinho não conta. ',
  'filters.toggle.enriched': 'Só as que o modelo local leu',
  'filters.toggle.enrichedHelp': 'Um modelo na sua própria máquina, executado só quando você pede. Nunca '
    + 'muda a nota.',
  'filters.clearSection': 'Limpar os filtros de {section}',
  'filters.activeIn': '{n} ativos em {section}',
  'filters.startingPoints': 'Pontos de partida',
  'filters.startFrom': 'Começar por',
  'filters.searchPlaceholder': 'Busque por título, empresa, local ou texto da vaga',
  'filters.searchClear': 'Limpar a busca',
  'filters.posted': 'Publicada',
  'filters.posted.any': 'Qualquer data',
  'filters.posted.3': 'Últimos 3 dias',
  'filters.posted.7': 'Última semana',
  'filters.posted.14': 'Últimas 2 semanas',
  'filters.posted.30': 'Último mês',
  'filters.posted.90': 'Últimos 3 meses',
  'filters.salaryCurrency': 'Moeda da remuneração mínima',
  'filters.phrasePlaceholder': 'Digite uma palavra e aperte Enter',
  'filters.mustMention': 'Precisa mencionar',
  'filters.mustNotMention': 'Não pode mencionar',

  'filters.prefer': 'Prefiro',
  'filters.avoid': 'Prefiro não',
  'filters.softHint':
    'Estes dois mudam a ORDEM, não a lista. Nada é escondido por eles, e a '
    + 'contagem acima continua a mesma.',
  'filters.trackingChip': 'Só as que estou acompanhando',
  // -- the card footer and the badges ------------------------------------
  'card.save': 'Salvar',
  'card.saved': 'Salva',
  'card.saveLabel': 'Salvar {title}',
  'card.unsaveLabel': 'Remover {title} das salvas',
  'card.apply': 'Candidatar-se no site da empresa',
  'badge.notScored': 'sem nota',
  // -- the job drawer ----------------------------------------------------
  'drawer.close': 'Fechar detalhes',
  'drawer.loading': 'Carregando...',
  'drawer.loadFailed': 'Não foi possível carregar este anúncio',
  'drawer.unscored': 'Esta vaga ainda não foi pontuada, então não há o que explicar. '
    + 'As notas são recalculadas quando suas preferências mudam.',
  'drawer.unscoredNumber': 'Esta vaga não foi pontuada, então não há número para explicar.',
  'drawer.matchedNoQuote': 'Correspondeu, mas o anúncio não tinha uma única linha que valesse citar. ',
  'drawer.matchedNoLine': 'Correspondeu, sem uma linha citável.',
  'drawer.applicationStatus': 'Situação da candidatura',
  'drawer.nothingMatchedHere': 'Nada neste anúncio correspondeu aqui.',
  'drawer.countedAgainst': 'O que pesou contra',
  'drawer.noBreakdown': 'Não há detalhamento armazenado para este anúncio.',
  'drawer.advanced': 'Detalhes avançados da pontuação',
  'drawer.nothingChecked': 'Nada foi verificado neste anúncio ainda.',
  'drawer.whyNotHigher': 'As linhas não marcadas acima são o motivo de este número não ser maior.',
  'drawer.salary': 'Remuneração',
  'drawer.employmentType': 'Tipo de contrato',
  'drawer.worksite': 'Escritório ou remoto',
  'drawer.seniority': 'Senioridade',
  'drawer.noDescription': 'Nenhum texto de descrição foi armazenado para este anúncio.',
  'drawer.notesPlaceholder': 'Suas anotações. Salvas quando você clica fora.',
  'drawer.notes': 'Anotações',
  'drawer.cancelLocalModel': 'Cancelar o pedido ao modelo local',
  'drawer.localModelNote': 'Lido por um modelo no seu próprio computador. Nunca muda a nota.',
  'drawer.localModelIdle': 'Nada ainda. Isto roda na sua máquina, só quando você pede.',
  'drawer.jobBoard': 'Quadro de vagas',
  'drawer.postedOn': 'Publicada',
  'drawer.firstSeen': 'Vista primeiro',
  'drawer.lastSeen': 'Vista por último',
  'drawer.original': 'Original',
  'drawer.advancedHelp': 'Como o número foi montado, ponto a ponto. Cada ponto vem de uma citação '
    + 'do anúncio.',
  'drawer.whySection': 'Por que se alinha à busca',
  // -- the rest of the interface -----------------------------------------
  'app.loading': 'Carregando...',
  'app.thisPosting': 'este anúncio',
  'app.rescore': 'Recalcular aderência à busca',
  'app.rescoreFailed': 'Algo deu errado. Tente de novo',
  'app.rescoreDone': 'Pronto. Carregando suas correspondências',
  'app.starting': 'Começando...',
  'app.listFailed': 'Não foi possível carregar a lista de vagas.',
  'app.listFailedShort': 'Não foi possível carregar a lista.',
  'health.noLocalModel': 'nenhum modelo local configurado',
  'health.localModelSilent': 'o modelo local não está respondendo',
  'health.localModelUntried': 'o modelo local ainda não foi contatado',
  'health.searchSlow': 'a busca está lenta',
  'health.databaseHelp': 'O conjunto exato de vagas que esta janela está lendo.',
  'health.unknown': 'Não dá para dizer como isto está agora.',
  'app.stale': 'Suas preferências mudaram. Estas correspondências estão desatualizadas.',
  'table.caption': 'Anúncios de vaga',
  'table.help': 'Lista de vagas. Rola para os lados.',
  'table.visibleColumns': 'Colunas visíveis',
  'table.columns': 'Colunas',
  'table.gatedShort': 'Declara uma exigência que você não atende',
  'table.offTargetShort': 'Não é o tipo de trabalho que você pediu',
  'table.noneRecorded': 'nenhum registrado',
  'filters.search': 'Buscar',
  'filters.minSalaryYear': 'Menor remuneração anual que você consideraria',
  'filters.minSalary': 'Menor remuneração que você consideraria',
  'filters.aYear': 'por ano',
  'filters.currencyPlaceholder': 'Moeda...',
  'filters.showingOnly': 'Mostrando só',
  'prefs.none': 'Nenhuma expressão editável foi encontrada.',
  'prefs.atLeastOne': 'Deixe pelo menos uma expressão.',
  'prefs.saving': 'Salvando...',
  'profile.saving': 'Salvando...',
  'profile.countryPlaceholder': 'Comece a digitar um país...',
  'profile.yourChoices': 'O que você procura',
  'retrieval.funnel': 'Funil de coleta',
  'revision.stale':
    'Estas vagas respondem às suas preferências anteriores (revisão '
    + '{serving}). Você está agora na revisão {current}. Nada foi '
    + 'perdido: as pontuações das novas preferências ainda não '
    + 'estão prontas.',
  'revision.progress': 'Recalculando: {done} de {total} ({pct}%)',
  'revision.recalculate': 'Recalcular agora',
  'revision.interrupted': 'O recálculo parou em {done} de {total} ({pct}%) antes de terminar. '
    + 'A lista ainda responde às suas preferências anteriores.',
  'revision.resume': 'Continuar o recálculo',
  'revision.lost': 'O contato com o Career Agent foi perdido durante o recálculo. Ele pode ter parado.',
  'revision.checkAgain': 'Verificar de novo',
  'app.rescoreLost': 'Contato perdido durante o recálculo. Tente de novo',
  'prefs.reach': 'Alcance lexical: {n} de {total} vagas abertas pontuadas ({pct}%). Título ou descrição.',
  'prefs.reachNone':
    'Alcance lexical: nenhuma expressão configurada foi encontrada nas {total} vagas abertas '
    + 'pontuadas. Trabalho equivalente pode usar outras palavras.',
  'prefs.reachUnmeasured':
    'Não contado aqui. Esta é uma regra que a checagem de elegibilidade aplica ao '
    + 'anúncio inteiro, não uma expressão que a pontuação registra.',
  'prefs.savedPhrases': 'Expressões salvas atualmente',
  'prefs.contextReach': 'O alcance lexical aplica as regras salvas de contexto e negação.',
  'prefs.unsavedPhrases': 'Alterações não salvas. O alcance ainda descreve as pontuações armazenadas.',
  'prefs.scoringPhrasesHint':
    'Estas expressões são usadas pelo matcher. Salvar muda sua busca; recalcule para '
    + 'atualizar as pontuações.',
  'prefs.reachRevision':
    'O alcance usa pontuações armazenadas da revisão {version}, não alterações em '
    + 'edição nem evidências do candidato.',
  'prefs.bodyReach': 'Alcance positivo na descrição: {n} vagas contribuíram pontos antes dos limites por componente.',
  'prefs.bodyReachUnmeasured':
    'O alcance positivo na descrição não foi registrado nestas pontuações. Disponível '
    + 'após recalcular.',
  'prefs.noChange': 'Nada mudou, então nada foi salvo.',
  'flash.hiddenWithReason': 'Guardada, e obrigado por dizer o motivo.',
  'flash.whyHidden': 'Por quê? (opcional)',
  'hideReason.WRONG_WORK': 'Não é meu trabalho',
  'hideReason.WRONG_PLACE': 'Lugar errado',
  'hideReason.WRONG_LEVEL': 'Nível errado',
  'hideReason.TITLE_MISLEADING': 'O título enganou',
  'hideReason.PAY': 'Remuneração',
  'hideReason.EMPLOYER': 'Esta empresa',
  'hideReason.STALE': 'Antiga ou já vista',
  'hideReason.OTHER': 'Outro motivo',
  'prefs.confirm': 'Aperte Salvar de novo para confirmar.',
  'prefs.diffAdded': 'Adicionando: {list}.',
  'prefs.diffRemoved': 'Removendo: {list}.',
  'prefs.diffCost':
    'Salvar muda a configuração da busca. É necessário recalcular a aderência à busca; '
    + 'os resultados existentes continuam disponíveis até lá.',
  'retrieval.scoredElsewhere':
    'Nenhuma destas está pontuada segundo suas preferências atuais, mas {n} '
    + 'pontuações continuam guardadas de uma versão anterior delas. Nada '
    + 'foi perdido: a pergunta mudou. Recalcule para atualizá-las.',
  'retrieval.bySource': 'Por fonte',
  'retrieval.boardsHelp': 'Quadros de vagas de empresas que responderam, dos que perguntamos.',
  'sources.loading': 'Lendo o catálogo...',
  'sources.colSource': 'Fonte',
  'sources.colStatus': 'Situação',
  'sources.colWhere': 'Onde',
  'sources.colPostings': 'Vagas',
  'sources.colWhy': 'Detalhe',

  'sources.refreshHead': 'Quao atualizada esta cada uma',
  'sources.refreshNote':
    'A coleta acontece em segundo plano. Tudo que ja foi encontrado continua '
    + 'na sua lista enquanto ela roda, entao nada aqui precisa terminar para '
    + 'voce olhar as vagas.',
  'sources.colRefresh': 'Agora',
  'sources.colProgress': 'Encontradas nesta execução',
  'sources.colFresh': 'Última execução bem-sucedida',
  'sources.state.NOT_STARTED': 'Nunca rodou',
  'sources.state.QUEUED': 'Esperando para comecar',
  'sources.state.RUNNING': 'Atualizando agora',
  'sources.state.PARTIAL': 'Atualizada parcialmente',
  'sources.state.COMPLETE': 'Atualização concluída',
  'sources.state.PAUSED': 'Pausada',
  'sources.state.FAILED': 'Última atualização falhou',
  'sources.state.BLOCKED': 'Indisponível no momento',
  'sources.state.STALE': 'Desatualizada',
  'sources.staleHelp': 'A última atualização bem-sucedida tem mais de três dias. Atualize para ver vagas atuais.',
  'sources.reason.PAGE_LIMIT':
    'Parou no limite de páginas desta fonte, de propósito: ela tem mais do que uma atualização lê.',
  'sources.reason.SOURCE_CEILING': 'A fonte não serve mais do que isso pelo acesso público.',
  'sources.reason.REQUEST_BUDGET': 'Parou no limite de requisições desta atualização; a próxima continua.',
  'sources.reason.SOME_FAILED': 'Algumas requisições falharam desta vez; o que foi lido está guardado.',
  'sources.reason.BOARDS_DEFERRED': 'Algumas páginas de empregadores ficaram para a próxima atualização.',
  'sources.notMeasured': 'nao medido',
  'sources.retrievedNoTotal': 'encontradas (total não publicado)',
  'sources.neverFresh': 'nunca',
  'sources.etaAbout': 'cerca de',
  'sources.etaUnderMinute': 'menos de um minuto',
  'sources.etaMinutes': 'minutos restantes',
  'sources.etaHours': 'horas restantes',
  'sources.exactReason': 'O motivo exato',
  'dom.noLink': 'Nenhum link utilizável neste anúncio',
  'kanban.appliedHelp': 'A data em que você se candidatou. Etapas posteriores nunca a '
    + 'sobrescrevem.',
  // -- the long sentences ------------------------------------------------
  'app.emptyAside': 'Sua experiência pode abrir mais caminhos do que um título de vaga. Esta '
    + 'ferramenta busca o que o anúncio diz sobre o trabalho, não só como ele'
    + 'se chama.',
  'health.searchSlowHelp': 'A busca está mais lenta que o normal, e corresponde um pouco diferente: '
    + 'encontra suas palavras dentro de palavras maiores, e procura em um lugar'
    + 'em vez de em todos. Recalcular suas correspondências resolve.',
  'health.staleFacetsHelp': 'Estas vagas foram pontuadas antes de existirem os filtros de país, '
    + 'região, escritório e remuneração, então esses quatro não vão'
    + 'encontrá-las. Recalcular resolve, no seu próprio computador e sem custo.',
  'filters.qualityHint': 'Compatibilidade é o quanto o trabalho se aproxima do que você quer. '
    + 'Detalhe do anúncio é o quanto o empregador realmente escreveu. Um'
    + 'anúncio curto pontua baixo em detalhe por melhor que a vaga seja.',
  'filters.salaryHint': 'Escolha também uma moeda. Nada aqui converte entre moedas, então sem ela '
    + 'não há com o que comparar. Anúncios que citam mês ou hora são '
    + 'convertidos para ano antes. A maioria dos anúncios nunca informa '
    + 'remuneração, e esses ficam de fora deste filtro em vez de serem'
    + 'mantidos.',
  'prefs.lede': 'Estas expressões são do que a nota é feita. Edite-as aqui; nada é '
    + 'escrito nos padrões que vêm com o produto.',
  'profile.nothingConfigured': 'Comece no Início descrevendo o trabalho que procura; '
    + 'depois edite suas preferências aqui.',
  'profile.tabsLabel': 'Qual parte do seu perfil olhar',
  'retrieval.lastLookedHelp': 'Quando fomos olhar pela última vez -- não quando um empregador publicou '
    + 'algo pela última vez.',
  // -- button labels, which are positional and were missed once ----------
  'action.close': 'Fechar',
  'action.tryAgain': 'Tentar de novo',
  'action.cancel': 'Cancelar',
  'action.save': 'Salvar',
  'action.clear': 'Limpar',
  'action.clearAll': 'Limpar tudo',
  'action.clearAllFilters': 'Limpar todos os filtros',
  'action.retry': 'Tentar de novo',
  'action.previous': 'Anterior',
  'action.next': 'Próxima',
  'action.apply': 'Aplicar',
  'drawer.clearAppliedDate': 'Limpar a data de candidatura',
  'drawer.askLocalModel': 'Pedir para o modelo local ler',
  'pager.position': 'Página {page} de {pages}',
  'filters.section.place': 'Onde e como você trabalharia',
  'filters.section.role': 'Cargo e nível',
  'rail.resultsHeading': 'Vagas',
  // -- a primeira execução ------------------------------------------------
  'firstrun.title': 'Comece por aqui',
  'firstrun.lede':
    'Algumas coisas, nesta ordem. Nenhuma é obrigatória, e você pode parar depois '
    + 'de qualquer uma -- cada uma diz o que permite ao Career Agent concluir.',
  'firstrun.privacy':
    'Tudo abaixo acontece neste computador. Seus documentos são lidos pelo '
    + 'próprio Career Agent, nunca são enviados a lugar nenhum, nenhuma IA os '
    + 'vê, e os arquivos não são guardados -- só as linhas que você confirmar.',
  'firstrun.step.documents': 'Importe suas informações de carreira',
  'firstrun.why.documents':
    'Um currículo já basta para começar. Uma exportação do LinkedIn acrescenta '
    + 'datas, e qualquer outro documento que você tenha também pode entrar.',
  'firstrun.step.evidence': 'Diga o que disso é verdade',
  'firstrun.why.evidence':
    'Nada lido de um documento vale até você confirmar. Enquanto nada estiver '
    + 'confirmado, todo requisito de toda vaga aparece como lacuna.',
  'firstrun.step.where': 'Onde você mora, e quem pode contratar você',
  'firstrun.why.where':
    'A única resposta que pode deixar a lista inteira vazia. Sem ela nenhuma '
    + 'vaga pode ser mostrada como aberta a você, porque nada conta como um '
    + 'lugar onde você pode trabalhar.',
  'firstrun.step.work': 'Que tipo de trabalho você procura',
  'firstrun.why.work':
    'Descrito com suas próprias palavras, e comparado com a vaga inteira em vez '
    + 'do título -- então uma vaga com nome estranho ainda chega até você.',
  'firstrun.step.jobs': 'Encontrar vagas',
  'firstrun.why.jobs':
    'Coletar vagas e pontuá-las contra tudo acima. Offline, e nada sobre você '
    + 'é enviado a lugar nenhum.',
  'firstrun.go.evidence': 'Abrir a revisão',
  'firstrun.go.where': 'Responder isto',
  'firstrun.go.work': 'Descrever o trabalho',
  'firstrun.go.jobs': 'Ir para as vagas',
  'firstrun.state.documents': '{n} lido(s)',
  'firstrun.state.noDocuments': 'Nada lido ainda',
  'firstrun.state.confirmed': '{n} confirmado(s) por você',
  'firstrun.state.waiting': '{n} afirmações esperando sua resposta',
  'firstrun.state.nothingToReview': 'Nada para revisar ainda',
  'firstrun.state.country': 'Você disse {code}',
  'firstrun.state.noCountry': 'Ainda sem resposta',
  'firstrun.state.phrases': '{n} frases descrevem o trabalho que você quer',
  'firstrun.state.noPhrases': 'Ainda não descrito',
  'firstrun.state.scored': '{n} vagas pontuadas',
  'firstrun.state.noScores': 'Nenhuma vaga pontuada ainda',
  'firstrun.kind.resume': 'Currículo',
  'firstrun.kind.linkedin': 'Exportação do LinkedIn',
  'firstrun.kind.document': 'Outro documento',
  'firstrun.kindLabel': 'Que tipo de documento é {name}',
  'firstrun.choose': 'Escolher arquivos',
  'firstrun.supported':
    'Aceitos: {kinds}. Um currículo escaneado é imagem, não texto, e não pode ser lido.',
  'firstrun.removeFile': 'Remover',
  'firstrun.read': 'Ler neste computador',
  'firstrun.reading': 'Lendo...',
  'firstrun.found':
    '{n} afirmações encontradas. Nenhuma delas é verdade ainda -- o próximo '
    + 'passo é você respondê-las uma por uma.',
  'firstrun.reopened':
    'Estes documentos já tinham sido lidos. Suas respostas anteriores foram '
    + 'mantidas, e {n} afirmações estão nessa revisão.',
  // -- o que a vaga pede de quem esta comecando (migracao 0027) ----------
  'filters.section.entry': 'Como entrar',
  'filters.section.entryHelp':
    'O que a vaga pede de experiência anterior, e o que ela diz para quem está '
    + 'começando ou vindo de outra área.',
  'filters.experience': 'Experiência que a vaga pede',
  'filters.experienceHelp':
    'Cada opção aqui mostra somente as vagas que DISSERAM quanto querem. A '
    + 'maioria nunca menciona isso, e essas não contam como abertas.',
  'filters.experience.any': 'Qualquer',
  'filters.experience.none': 'Disseram que não é necessária',
  'filters.experience.upTo1': 'Até 1 ano',
  'filters.experience.upTo2': 'Até 2 anos',
  'filters.experience.upTo5': 'Até 5 anos',
  'filters.toggle.includeTransferable': 'Mostrar vagas para as quais eu poderia migrar',
  'filters.includeTransferableHelp':
    'Desligado por padrão. Sua busca descreve o trabalho que você vem fazendo, '
    + 'então ela deixa de lado vagas de uma área para a qual você está migrando. '
    + 'Isto traz de volta as que pedem coisas que você já confirmou sobre si. '
    + 'Não faz nada enquanto você não tiver confirmado alguma coisa.',
  'facet.experience_requirement': 'Com que firmeza pedem experiência',
  'facet.entry_signal': 'O que dizem a quem está começando',
  'chip.experienceNone': 'Disseram que não exigem experiência',
  'chip.experienceUpTo': 'Pede no máximo {n} anos',
  'experience.NONE_REQUIRED': 'Não é necessária',
  'experience.REQUIRED_MINIMUM': 'Um número de anos',
  'experience.REQUIRED_UNQUANTIFIED': 'Pede, sem dizer quanta',
  'experience.PREFERRED': 'Desejável, não obrigatória',
  'experience.NICE_TO_HAVE': 'Conta como diferencial',
  'experience.NOT_STATED': 'Não disse',
  'entrySignal.NO_EXPERIENCE_REQUIRED': 'Sem experiência necessária',
  'entrySignal.ENTRY_LEVEL': 'Nível de entrada',
  'entrySignal.RECENT_GRADUATE': 'Recém-formados são bem-vindos',
  'entrySignal.TRAINING_PROVIDED': 'Treinamento oferecido',
  'entrySignal.CAREER_CHANGERS_WELCOME': 'Aberta a outras áreas',
  'drawer.experienceHeading': 'Experiência anterior',
  'drawer.experienceNone': 'Esta vaga não exige experiência profissional anterior.',
  'drawer.experienceYears': 'Esta vaga pede pelo menos {n} anos de experiência.',
  'drawer.experienceUnquantified':
    'Esta vaga pede experiência anterior sem dizer quanta.',
  'drawer.experiencePreferred': 'Aqui a experiência é desejável, não obrigatória.',
  'drawer.experienceBonus': 'Aqui a experiência conta como diferencial.',
  'drawer.experienceSilent':
    'Esta vaga nunca menciona experiência anterior. Isso não é o mesmo que '
    + 'dizer que não é necessária.',
  'drawer.experienceCovered':
    'Suas evidências confirmadas respondem {matched} das {total} coisas que '
    + 'esta vaga pede.',
  'drawer.experienceStillUnconfirmed':
    'A experiência que ela pede explicitamente não está entre o que você confirmou.',
  'filters.section.pay': 'Remuneração',
  'filters.section.payHelp': 'Comparada dentro de uma mesma moeda. Nada aqui converte entre elas.',
  'filters.section.skills': 'Ferramentas e habilidades',
  'filters.section.sources': 'Onde a vaga foi encontrada',
  'filters.section.progress': 'Em que ponto você está',
  'filters.section.advanced': 'Avançado',
  'filters.toggle.includeIneligible': 'Incluir vagas com conflito de elegibilidade',
  'filters.toggle.includeUnresolved': 'Incluir vagas que não disseram onde contratam',
  'filters.includeUnresolvedHelp':
    'Desligado por padrão. São vagas em que o anúncio nunca disse onde o empregador pode contratar, ' +
    'então ninguém consegue dizer se você poderia aceitá-las. Isso não é um não -- é uma pergunta que o ' +
    'anúncio deixou aberta.',
  'jobs.hiddenUnresolved':
    '{n} ocultas porque o anúncio nunca disse onde o empregador contrata. Nada exclui você; nada ' +
    'confirma você também.',
  'jobs.showUnresolved': 'Mostrar essas também',
  'filters.toggle.includeOffTarget': 'Incluir trabalhos que você separou',
  'card.locations': '{n} locais',
  'card.locationsHelp': 'Este empregador publicou esta vaga como {n} anúncios separados.',
  'card.morePlaces': '+{n} outros',
  // -- the shell: navigation, the rail toggle and Home --------------------
  // Ver a nota em ingles acima: a moldura do produto.
  'nav.settings': 'Configurações e fontes',
  'nav.sectionSearch': 'Busca',
  'nav.sectionProfile': 'Perfil',
  'nav.sectionSystem': 'Sistema',
  'sidenav.menu': 'Menu',
  'sidenav.menuLabel': 'Abrir a navegação',
  'sidenav.appearance': 'Aparência',
  'sidenav.language': 'Idioma',
  'sidenav.statJobs': 'vagas',
  'sidenav.statOpen': 'em aberto',
  'sidenav.statInterview': 'entrevista',
  'pagehead.eyebrow.home': 'Hoje',
  'pagehead.title.home': 'Como está sua busca?',
  'pagehead.eyebrow.jobs': 'Descobrir',
  'pagehead.title.jobs': 'Vagas para você',
  'pagehead.eyebrow.applications': 'Acompanhamento',
  'pagehead.title.applications': 'Onde está cada uma',
  'pagehead.eyebrow.profile': 'Perfil',
  'pagehead.title.profile': 'Seu perfil de carreira',
  'pagehead.eyebrow.evidence': 'Evidências',
  'pagehead.title.evidence': 'Evidências profissionais',
  'pagehead.eyebrow.settings': 'Sistema',
  'pagehead.title.settings': 'Configurações e fontes',
  'nav.home': 'Início',
  'nav.jobs': 'Descobrir vagas',
  'nav.applications': 'Candidaturas',
  'nav.profile': 'Perfil de carreira',
  'nav.evidence': 'Evidências',
  'rail.hide': 'Esconder filtros',
  'rail.show': 'Mostrar filtros',
  'home.title': 'Como está sua busca',
  'home.since': 'Desde que você marcou a lista como lida, em {when}.',
  'home.neverReviewed': 'Você ainda não marcou a lista como lida, então as duas contagens sobre o '
    + 'que mudou estão esperando um primeiro marco.',
  'home.metric.new': 'Novas',
  'home.metric.saved': 'Salvas',
  'home.metric.applied': 'Candidaturas',
  'home.metric.interviews': 'Entrevistas',
  'home.metric.offers': 'Propostas',
  'home.metric.progressed': 'Avançaram',
  'home.metric.tracking': 'Acompanhando',
  'home.kind.now': 'agora',
  'home.kind.event': 'desde a última vez',
  'home.metricOpen': 'Mostrar {label} na lista de vagas',
  'home.nothing': 'Nada aqui hoje.',
  'home.complete': 'Complete seu perfil de carreira',
  'home.completeLede': 'Cada um destes faz uma parte do produto conseguir responder. Não há nota '
    + 'de conclusão, porque não existe denominador honesto para uma carreira.',
  'home.gap.evidence': 'Nada foi confirmado sobre você, então todo requisito de todo anúncio '
    + 'aparece como lacuna.',
  'home.gap.residence': 'Onde você mora não está definido, então nada consegue dizer se um '
    + 'anúncio contrata lá.',
  'home.gap.hiring_scopes': 'Nenhum alcance de contratação é aceito, então o portão de geografia '
    + 'nunca pode passar e todo anúncio fica sem resposta.',
  'home.gap.compensation': 'Nenhuma meta de remuneração está definida, então a parte de remuneração '
    + 'da correspondência não pontua nada.',
  'home.gap.work': 'Nenhuma expressão descreve o trabalho que você quer, então não há o que '
    + 'um anúncio corresponder.',
  'home.openProfile': 'Abrir seu perfil',
  'home.openEvidence': 'Adicionar suas evidências',
  'kanban.closed': 'Encerradas',
  'kanban.columnLabel': '{column}, {n} vagas',
  // Dismissing the sentence, never the state.
  'hidden.dismiss': 'Parar de mostrar este aviso',
  // The theme control. `theme.js` is a classic script in the head and cannot
  // import the catalogue, so it renders the English and `relabelStaticText`
  // swaps these in.
  'theme.label': 'Tema de cores',
  'theme.light': 'Claro',
  'theme.dark': 'Escuro',
  'theme.lightHint': 'Sempre usar o tema claro, seja qual for a preferência deste computador.',
  'theme.darkHint': 'Sempre usar o tema escuro, seja qual for a preferência deste computador.',
  'theme.buttonLabel': 'Tema {label}. {hint}',
  // The header status line. `mode` is the SEMANTIC key the server already
  // sends; `banner` is its English rendering and is now only a fallback
  // for a mode this catalogue has never heard of.
  'mode.PERSONAL': 'Dados pessoais',
  'mode.DEMO': 'Dados de demonstração',
  'mode.UNKNOWN': 'Este arquivo não diz o que contém',
  'health.modePersonalHelp': 'Suas próprias vagas. Dados de exemplo nunca aparecem no lugar delas.',
  'health.modeDemoHelp': 'Vagas inventadas para experimentar, separadas das suas.',
  'health.jobs': '{n} vagas',
  'health.jobsUnknown': 'um número desconhecido de vagas',
  'health.localModelUp': 'modelo local {model} no ar',
  'health.staleScores': '{n} pontuações são anteriores aos filtros',
  'health.localModelHelp': 'Tudo nesta página funciona com o modelo local desligado.',
  'health.worthKnowing': '{n} coisas que vale saber',
  'health.worthKnowingOne': '1 coisa que vale saber',
  'health.allWorking': 'Está tudo funcionando',
  // Sentences built around a number or a job title. The title itself is
  // the employer's and is substituted, never translated.
  'a11y.matchPercent': 'Aderência à busca: {n} de 100',
  'a11y.readablePercent': 'anúncio legível em {n} por cento',
  'age.daysAgo': 'há {n} dias',
  'age.monthsAgo': 'há {n} meses',
  'kanban.appliedOn': 'Enviada em {date}',
  'kanban.moveLabel': 'Mover {title} para outra etapa',
  'card.statusLabel': 'Situação da candidatura para {title}',
  'prefsCategory.desired': 'Sinais desejados',
  'prefsCategory.desiredHelp':
    'Frases que somam valor de correspondência quando uma vaga as contém.',
  'prefsCategory.negative': 'Sinais negativos',
  'prefsCategory.negativeHelp':
    'Frases que reduzem a correspondência sem excluir a vaga.',
  'prefsCategory.excluded': 'Exclusões absolutas',
  'prefsCategory.excludedHelp':
    'Frases que tiram uma vaga da visão elegível. A vaga precisa DIZER uma destas; '
    + 'o silêncio nunca exclui.',
  'prefs.addPhrase': 'Adicionar uma frase',
  'prefs.phrasesFor': 'Expressões para {label}',
  // How old the ADVERT is. A separate question from where the candidate
  // is with it, and both used to render in English.
  'a11y.notScored': 'Sem nota',
  'badge.readabilityUnknown': 'legibilidade desconhecida',
  'age.oneMonthAgo': 'há 1 mês',
  'age.notStated': 'data não informada',
  'age.today': 'hoje',
  'age.yesterday': 'ontem',
  'freshness.FRESH': 'Recente',
  'freshness.RECENT': 'Nova',
  'freshness.AGING': 'Envelhecendo',
  'freshness.STALE': 'Antiga',
  'freshness.CLOSED': 'Encerrada',
  'freshness.UNKNOWN': 'Idade desconhecida',
  // The retrieval panel and the source panel. The values substituted into
  // these -- an error a third party returned, a reason the catalogue wrote
  // -- are quoted as they came and never translated.
  'retrieval.lastLooked': 'Última verificação em {when}',
  'retrieval.runningBoards': 'Em andamento; {done} de {total} quadros',
  'retrieval.failedWith': 'Falhou: {error}',
  'retrieval.unknownError': 'erro desconhecido',
  'sources.reachOne': 'alcança {boards} quadros de empregadores; {producing} deles já '
    + 'devolveram uma vaga. Um quadro é uma empresa, não uma fonte.',
  'sources.reachMany': 'alcançam {boards} quadros de empregadores; {producing} deles já '
    + 'devolveram uma vaga. Um quadro é uma empresa, não uma fonte.',
  'sources.postingsCount': '{n} vagas',
  'sources.boardsCount': '{producing}/{total} quadros',
  'sources.catalogueSays': 'O catálogo diz: {reason}',
  'sources.lastError': 'Último erro registrado: {error}',
  'sources.newestSeen': 'Vaga mais nova vista pela primeira vez em: {date}',
  'sources.wouldChange': 'O que mudaria isto: {what}',
  'sources.downgraded': 'Rebaixada: {why}',
  'profile.disagreeOne': 'Seu perfil de candidata e suas configurações de busca discordam sobre '
    + 'uma coisa.',
  'profile.disagreeMany': 'Seu perfil de candidata e suas configurações de busca discordam sobre '
    + '{n} coisas.',
  'profile.saysValue': '{path} diz {value}',
  // The filter chips. A CURRENCY CODE is not translated -- BRL is BRL in
  // every language -- and neither is the text somebody typed into the search
  // box.
  'chip.minScore': 'Correspondência de pelo menos {n}',
  'chip.maxScore': 'Correspondência de no máximo {n}',
  'chip.minConfidence': 'Detalhe do anúncio de pelo menos {n}',
  'chip.postedWithinOne': 'Publicada no último 1 dia',
  'chip.postedWithin': 'Publicada nos últimos {n} dias',
  'chip.contains': 'Contém “{text}”',
  'chip.minSalary': 'Pelo menos {amount} por ano',
  'chip.minSalaryCurrency': 'Pelo menos {amount} {currency} por ano',
  'chip.removeFilter': 'Remover filtro {label}',
  'chip.removeValue': 'Remover {label} {value}',
  'filters.section.advancedHelp': 'Como esta ferramenta classificou cada anúncio. Útil para conferir o '
    + 'trabalho dela.',
  // The table. Job titles, company names and tool names are substituted
  // verbatim: they are the employer's words and the configuration's, and
  // neither is ours to translate.
  'table.sortBy': 'Ordenar por {column}',
  'table.groupTitle': 'Publicada como {n} anúncios: {places}',
  'table.groupTitleBare': 'Publicada como {n} anúncios separados',
  'table.groupLabel': 'representa {n} anúncios',
  'table.openDetails': 'Abrir detalhes de {title} em {company}',
  'table.andMoreTools': 'e mais {n}: {names}',
  'table.markApplied': 'Marcar {title} como enviada',
  'table.appliedDateFor': 'Data de envio para {title}',
  // The results header, the empty state's advice and the one-line
  // confirmations. `filterWord.*` names each filter INSIDE a sentence, which
  // is a different word from its heading in the rail.
  'confirm.clearApplied': 'Limpar a data de envio de {title}?\n\n'
    + 'Isto registra que nenhuma candidatura foi enviada. A situação '
    + 'continua como está. Não dá para desfazer.',
  'count.oneRole': '1 função',
  'count.roles': '{n} funções',
  'count.oneJob': '1 vaga',
  'count.jobs': '{n} vagas',
  'count.oneRepostFolded': '1 republicação agrupada',
  'count.repostsFolded': '{n} republicações agrupadas',
  'count.showingRange': 'mostrando de {from} a {to}',
  'count.oneFilterActive': '1 filtro ativo',
  'count.filtersActive': '{n} filtros ativos',
  'count.noFilters': 'sem filtros',
  'advice.matchBelow': 'pedir correspondência abaixo de {n}',
  'advice.detailBelow': 'pedir detalhe abaixo de {n}',
  'advice.searchingLess': 'buscar por menos que “{text}”',
  'advice.eligibility': 'permitir vagas que não disseram se você poderia aceitá-las',
  'advice.removingFilter': 'remover o filtro de {what}',
  'advice.turningOff': 'desligar “{what}”',
  'advice.salaryLess': 'pedir menos de {amount} por ano',
  'advice.postedMoreThanOne': 'incluir vagas publicadas há mais de 1 dia',
  'advice.postedMoreThan': 'incluir vagas publicadas há mais de {n} dias',
  'advice.tryClearing': 'Tente limpar os filtros.',
  'advice.try': 'Tente {list}.{why}',
  'advice.or': 'ou',
  'advice.eligibilityWhy': 'A maioria dos anúncios nunca diz onde a empresa pode contratar, então a '
    + 'maioria não dá para confirmar em nenhuma direção. Isso não é o mesmo que'
    + 'estar descartada.',
  'empty.checking': 'Verificando o que existe no seu banco de dados...',
  'empty.unscored': 'Existem {n} vagas aqui, mas nenhuma foi pontuada com as suas '
    + 'preferências atuais. Isso acontece quando você muda o que está '
    + 'procurando: as notas antigas respondiam à pergunta antiga. O recálculo '
    + 'acontece no seu próprio computador, não custa nada e leva alguns'
    + 'minutos.',
  'empty.noneCollected': 'Nenhuma vaga foi coletada ainda. Encontrar vagas consulta sites de emprego públicos para '
    + 'você.',
  'empty.notScoredYet': 'Estas vagas ainda não foram pontuadas.',
  'empty.nothingYet': 'Ainda não há nada aqui.',
  'rescore.progress': 'Recalculando: {done} de {total}',
  'rescore.working': 'Recalculando...',
  'flash.movedTo': 'Movida para {status}.',
  'flash.appliedDateSet': 'Data de envio definida para {date}.',
  'flash.appliedDateCleared': 'Data de envio limpa.',
  'filterWord.provider': 'quadro de vagas',
  'filterWord.role_class': 'título da vaga',
  'filterWord.signal': 'expressão encontrada',
  'filterWord.technology': 'ferramenta',
  'filterWord.status': 'andamento',
  'filterWord.fit_band': 'faixa de correspondência',
  'filterWord.country': 'país',
  'filterWord.region': 'região',
  'filterWord.worksite': 'presencial ou remoto',
  'filterWord.employment_type': 'tipo de contrato',
  'filterWord.contract_regime': 'regime de contratação',
  'filterWord.salary_currency': 'moeda',
  'filterWord.salary_period': 'período de pagamento',
  'filterWord.keyword': 'palavra obrigatória',
  'filterWord.exclude_keyword': 'palavra proibida',
  'filterWord.saved_only': 'só as que eu salvei',
  'filterWord.has_salary': 'só as que informam salário',
  'filterWord.remote_only': 'só remotas',
  'filterWord.enriched_only': 'só as que o modelo local leu',
  'filterWord.latam_only': 'aberta à América Latina',
  'filterWord.worldwide_only': 'diz que contrata no mundo todo',
  // The job drawer. Every heading, every lede and every sentence the product
  // writes about a posting. What is NOT here: the description, the title,
  // the company name and every evidence quote -- those are the employer's
  // words and ADR-0002 makes a translated quote not a quote.
  'drawer.scoredOutOf': 'Aderência à busca: {score}/100, {howClose} para o trabalho que você '
    + 'disse querer. As evidências de carreira são avaliadas separadamente em Evidências / Preparação.',
  'drawer.closeStrong': 'uma aderência forte',
  'drawer.closeReasonable': 'uma aderência razoável',
  'drawer.closePartial': 'uma aderência parcial',
  'drawer.closeWeak': 'uma aderência fraca',
  'drawer.pickedUpOne': 'Encontrou 1 coisa que você procura, citada abaixo.',
  'drawer.pickedUp': 'Encontrou {n} coisas que você procura, citadas abaixo.',
  'drawer.readableHigh': 'O anúncio é detalhado, então havia bastante material.',
  'drawer.readableMid': 'O anúncio é só moderadamente detalhado, então parte disto é incerta.',
  'drawer.readableLow': 'O anúncio é raso, então havia pouco material e esta nota é incerta.',
  'drawer.wouldRuleOut': 'Algo no anúncio descartaria você. Veja abaixo.',
  'drawer.secWhyMatches': 'Por que se alinha à busca',
  'drawer.secStrengths': 'O que esta vaga tem do que você pediu',
  'drawer.secGaps': 'O que o anúncio não deixou claro',
  'drawer.secGates': 'Você poderia aceitar esta vaga',
  'drawer.secConfidence': 'Quanto o anúncio contou',
  'drawer.secUnknowns': 'Nunca mencionado no anúncio',
  'drawer.secSignals': 'Ferramentas e habilidades que ele cita',
  'drawer.secPay': 'Remuneração e contrato',
  'drawer.secDescription': 'Descrição',
  'drawer.secHistory': 'Histórico de situação',
  'drawer.secEnrich': 'O que o modelo local viu',
  'drawer.secPlaces': 'Publicada em vários lugares',
  'drawer.secProvenance': 'Onde encontramos isto',
  'drawer.allQuoted': 'Cada linha abaixo é citada do próprio anúncio.',
  'drawer.someQuoted': '{n} destas são citadas do próprio anúncio. As demais corresponderam a '
    + 'algo que o anúncio sugere em vez de dizer.',
  'drawer.noneQuoted': 'Nenhuma delas tinha uma linha que valesse citar. Corresponderam a algo '
    + 'que o anúncio sugere em vez de dizer.',
  'drawer.gapsLede': 'Isto não é ponto contra a vaga. São coisas que o empregador deixou de '
    + 'fora, e é por isso que o número de detalhe não é mais alto.',
  'drawer.saved': '★ Salva',
  'drawer.save': '☆ Salvar',
  'drawer.appliedOn': 'Enviada em {date}. ',
  'drawer.appliedKept': 'Mantida enquanto a situação for {status}, o que significa que uma '
    + 'candidatura foi enviada.',
  'drawer.confirmClearApplied': 'Limpar a data de envio ({date}) de {title}?\n\n'
    + 'Isto registra que nenhuma candidatura foi enviada. A situação '
    + 'continua como está. Não dá para desfazer.',
  'drawer.cappedAt': 'Limitado em {points}: o anúncio correspondeu a mais do que esta parte da '
    + 'nota pode conceder.',
  'drawer.gatesLede': 'Três respostas, não duas. Um anúncio que nunca diz onde a empresa '
    + 'contrata é uma pergunta em aberto, não um não.',
  'drawer.covered': 'O anúncio cobriu {awarded} das {total} coisas que procuramos. Isto é '
    + 'sobre o anúncio, não sobre você.',
  'drawer.salaryUnstated': 'Salário não informado neste anúncio',
  'drawer.notStated': 'Não informado',
  'drawer.noUrl': 'Nenhum endereço registrado',
  'drawer.statusChange': '{from} → {to}',
  'drawer.configuredEndpoint': 'o endereço configurado',
  'drawer.noLocalModel': 'Nenhum modelo local está configurado, então não há o que perguntar.',
  'drawer.localModelSilent': 'O modelo local foi contatado em {endpoint} e não respondeu. Ligue-o e '
    + 'recarregue esta página. Todo o resto aqui funciona sem ele.',
  'drawer.localModelReady': '{model} respondeu em {endpoint}. Uma rodada ainda leva alguns minutos. ',
  'drawer.theLocalModel': 'O modelo local',
  'drawer.theLocalModelLower': 'o modelo local',
  'drawer.localModelUntried': 'O modelo local ainda não foi perguntado sobre esta vaga. Abrir uma '
    + 'página nunca pergunta a ele. A primeira rodada pode levar alguns'
    + 'minutos, e você pode cancelar.',
  'drawer.asking': 'Perguntando a {model}: isto pode levar alguns minutos.',
  'drawer.doneIn': 'Pronto em {seconds}s.',
  'drawer.localModelFailed': 'Não foi possível alcançar o modelo local: {error}',
  'drawer.placesLede': '{company} publicou esta função como {n} anúncios separados, um por '
    + 'localidade. Cada um tem o próprio link no provedor; esta gaveta descreve'
    + 'o de maior pontuação.',
  'drawer.morePlaces': 'mais {n} não listados aqui',
  'gate.PASS': 'Nada no caminho',
  'gate.FAIL': 'Descarta você',
  'gate.UNRESOLVED': 'O anúncio não disse',
  'value.notStated': 'não informado',
  // What the BOARD printed as the employment type, normalised.
  // `employment.*` is a different question -- the engagement this system
  // infers -- and `contract.*` a third: the regime the candidate accepts.
  // CLT and PJ name Brazilian legal instruments, the same word in both.
  'jobType.FULL_TIME': 'Tempo integral',
  'jobType.PART_TIME': 'Meio período',
  'jobType.CONTRACT': 'Contrato',
  'jobType.TEMPORARY': 'Temporário',
  'jobType.INTERNSHIP': 'Estágio',
  'jobType.VOLUNTEER': 'Voluntário',
  // The last of the product's own English. What is deliberately NOT here: a
  // job title, a company name, a description, an evidence quote, a country
  // code and a currency code.
  'empty.headline': 'Nada para mostrar aqui.',
  'empty.filtered': 'Nada corresponde ao que você está pedindo.',
  'rescore.couldNotStart': 'Não deu para começar. Tente de novo',
  'flash.notesSaved': 'Anotações salvas.',
  'order.everyPosting': 'Cada anúncio',
  'order.oneRowHelp': 'Empregadores costumam publicar a mesma vaga uma vez por cidade. Isto '
    + 'mostra cada vaga uma vez, e o cartão diz em quantos lugares ela'
    + 'apareceu.',
  'order.everyPostingHelp': 'Mostra cada anúncio separadamente, incluindo os que um empregador '
    + 'repetiu para cada localidade.',
  'order.lowestFirst': 'Menor primeiro',
  'order.highestFirst': 'Maior primeiro',
  'order.lowestFirstLabel': 'Mostrando o menor primeiro. Ative para mostrar o maior primeiro.',
  'order.highestFirstLabel': 'Mostrando o maior primeiro. Ative para mostrar o menor primeiro.',
  'absent.untitled': 'Anúncio sem título',
  'absent.company': 'uma empresa sem nome',
  'absent.companyStated': 'Empresa não informada',
  'absent.source': 'Origem não registrada',
  'absent.place': 'Localidade não informada',
  'column.score': 'Aderência à busca',
  'column.confidence': 'Detalhe do anúncio',
  'column.company': 'Empresa',
  'column.title': 'Título',
  'column.location': 'Localidade',
  'column.source': 'Quadro de vagas',
  'column.technologies': 'Ferramentas',
  'column.salary': 'Remuneração',
  'column.contract': 'Contrato',
  'column.posted': 'Publicada',
  'column.freshness': 'Idade',
  'column.eligibility': 'Você pode aceitar',
  'column.status': 'Andamento',
  'column.applied': 'Enviada',
  'column.applied_at': 'Data de envio',
  'column.saved': 'Salva',
  'column.link': 'Link',
  'kanban.emptyShortlisted': 'Nada marcado como interessante ainda. Defina uma vaga como Tenho '
    + 'interesse em Cartões ou Tabela.',
  'kanban.emptyToApply': 'Nada na fila para se candidatar.',
  'kanban.emptyApplied': 'Nenhuma candidatura enviada ainda.',
  'kanban.emptyInterview': 'Nenhuma entrevista em andamento.',
  'kanban.emptyOffer': 'Nenhuma proposta.',
  'kanban.emptyHired': 'Ainda não há nada aqui.',
  'kanban.emptyClosed': 'Nada encerrado.',
  'facet.company': 'Empresa',
  'facet.provider': 'Quadro de vagas',
  'facet.technology': 'Ferramentas citadas no anúncio',
  'facet.worksite': 'Presencial, híbrido ou remoto',
  'facet.region': 'Parte do mundo, como publicado',
  'facet.country': 'País, como publicado',
  'facet.seniority': 'Nível',
  'facet.employment_type': 'Tipo de contrato',
  'facet.contract_regime': 'Regime de contratação',
  'facet.salary_currency': 'Moeda',
  'facet.salary_period': 'Pago por',
  'facet.status': 'Seu andamento',
  'facet.eligibility': 'Você poderia aceitar',
  'facet.role_class': 'Como o título da vaga soa',
  'facet.fit_band': 'Faixa de aderência à busca',
  'facet.signal': 'Tudo o que o anúncio correspondeu',
  'filters.includeIneligibleHelp': 'Desligado por padrão. São vagas cujo anúncio declara algo que você não '
    + 'atende, como um país, uma autorização de trabalho ou viagens exigidas.'
    + 'Vagas que simplesmente não disseram aparecem sempre.',
  'filters.includeOffTargetHelp': 'Desligado por padrão. São vagas em que nada do que você disse procurar '
    + 'apareceu. Não há nada de errado com elas; são outro tipo de trabalho.'
    + 'Uma correspondência BAIXA não é isto e aparece sempre.',
  'filters.matchAtLeast': 'Aderência à busca de pelo menos',
  'filters.detailAtLeast': 'Detalhe do anúncio de pelo menos',
  'stage.fetched': 'Coletadas',
  'stage.active': 'Ativas',
  'stage.normalised': 'Legíveis',
  'stage.scored': 'Pontuadas',
  'stage.deduplicated': 'Funções',
  'stage.eligible': 'Elegíveis',
  'stage.recommended': 'Recomendadas',
  'stage.fetchedHelp': 'Todo anúncio já coletado, incluindo os encerrados.',
  'stage.activeHelp': 'Ainda abertas na origem.',
  'stage.normalisedHelp': 'A descrição completa da vaga chegou e pode ser lida.',
  'stage.scoredHelp': 'Medidas com as preferências que você tem agora.',
  'stage.deduplicatedHelp': 'Uma por empresa e título; um empregador costuma publicar uma função por '
    + 'cidade.',
  'stage.eligibleHelp': 'Não desqualificada explicitamente. Silêncio não é desqualificação.',
  'stage.recommendedHelp': 'Na nota de pré-seleção ou acima dela.',
  'retrieval.retrieving': 'Buscando…',
  'retrieval.never': 'Nunca buscado por esta interface.',
  'retrieval.finished': 'Concluído',
  'retrieval.cancelled': 'Cancelado; o banco de dados está intacto',
  'prefs.savedRescore': 'Salvo. Suas correspondências estão desatualizadas e precisam ser '
    + 'recalculadas.',
  'prefs.saved': 'Salvo.',
  'profile.notSaved': 'Essa mudança não foi salva.',
  'profile.editLeadLocal':
    'Conte o que faz sentido para você agora. Usamos essas respostas para colocar '
    + 'na frente as vagas mais próximas do que você procura. Suas recomendações '
    + 'são atualizadas quando você salva.',
  'profile.editLeadFirst':
    'Conte o que faz sentido para você agora. Usamos essas respostas para colocar '
    + 'na frente as vagas mais próximas do que você procura.',
  'badge.confidenceHelp': 'Quanto o empregador realmente escreveu. Isto NÃO é sua chance de ser '
    + 'contratada. Um anúncio curto pontua baixo aqui por melhor que a vaga'
    + 'seja.',
  'badge.matchHelp': 'O quanto esta vaga se aproxima do trabalho que você quer, calculado a '
    + 'partir das palavras do anúncio e do modelo de busca. Suas evidências de carreira não entram neste número.',
  'badge.eligibilityUnresolvedHelp': 'Este anúncio nunca disse onde a empresa pode contratar. Não dizer nada '
    + 'não é o mesmo que dizer sim, então isto continua uma pergunta em aberto.',
  'badge.eligibilityHelp': 'Se você poderia aceitar esta vaga. Uma pergunta separada de quão bem ela '
    + 'corresponde.',
  'badge.notMeasured': 'não medido',
  'prominence.PRIMARY': 'central para esta vaga',
  'prominence.SECONDARY': 'usada com frequência',
  'prominence.INCIDENTAL': 'citada uma vez',
  'prominence.OTHER': 'citada',
  'period.YEAR': 'Ano',
  'period.MONTH': 'Mês',
  'period.WEEK': 'Semana',
  'period.DAY': 'Dia',
  'period.HOUR': 'Hora',
  'period.NOT_STATED': 'Não informado',
  'region.LATAM': 'América Latina',
  'region.EMEA': 'EMEA',
  'region.APAC': 'APAC',
  'region.AMERICAS': 'Américas',
  'region.NORTH_AMERICA': 'América do Norte',
  'region.WORLDWIDE': 'Mundo todo',
  'access.ATS_STRUCTURED': 'ATS estruturado',
  'access.AGGREGATOR_API': 'API de agregador',
  'access.ATS_HTML': 'HTML de ATS',
  'access.MANUAL_IMPORT': 'Importação manual',
  'sources.oneFamily': '1 família de conectores',
  'sources.families': '{n} famílias de conectores',
  // The card's accessible name. The title and the company are the employer's
  // words and are substituted, never translated.
  'card.openLabel': '{title} em {company}. Pressione Enter para abrir os detalhes.',
  // The screen-reader label on the sort control.
  'order.sortLabel': 'Ordenar resultados por',
  // Hiding one posting by hand. The fourth reason a job is off the screen,
  // and the only one she chose: distinct from an employer ruling her out,
  // from her search calling it other work, and from a rejection.
  'card.hide': 'Ocultar',
  'card.unhide': 'Trazer de volta',
  'card.hideLabel': 'Ocultar {title} destas listas',
  'card.unhideLabel': 'Trazer {title} de volta para estas listas',
  'action.undo': 'Desfazer',
  'flash.hidden': 'Ocultada. Ela continua no banco de dados e nada mais mudou.',
  'flash.unhidden': 'De volta à lista.',
  'hidden.byYou': '{count} ocultas porque você as deixou de lado.',
  'hidden.byYouShowing': 'Mostrando as que você deixou de lado, junto com as demais.',
  'hidden.byYouReveal': 'Mostrar também',
  'hidden.restoreView': 'Ver só o que você deixou de lado',
  // One of them, said in the singular. Portuguese inflects the noun, the
  // article and the pronoun together, so a count spliced into a plural
  // sentence reads as broken grammar rather than as a number.
  'hidden.eligibilityOne': '1 oculta, porque declara uma exigência que você não atende: um país, uma '
    + 'autorização de trabalho, um credenciamento de segurança ou algo'
    + 'parecido.',
  'hidden.offTargetOne': '1 oculta por ser um tipo de trabalho diferente do que você descreveu. '
    + 'Não há nada de errado com ela: não correspondeu ao que você disse'
    + 'procurar.',
  'hidden.byYouOne': '1 oculta porque você a deixou de lado.',
  // Contextual help, where the word is. It replaced a permanent block under
  // the toolbar that was nowhere near either number.
  'help.about': 'O que {what} significa',
  'help.theseNumbers': 'estes números',
  'help.eligibility': 'Se você poderia aceitar esta vaga é uma TERCEIRA resposta, mantida '
    + 'separada dos dois números. Um empregador declara uma exigência ou não'
    + 'diz nada; silêncio não é permissão nem recusa.',
  // Exploring. The one status a person sets before deciding anything, and
  // the one most likely to be mistaken for a signal about the job.
  'filters.preset.interested': 'As que eu tenho interesse',
  'filters.preset.interestedHelp': 'Só as vagas que você marcou como Tenho interesse. Dizer isso é um recado '
    + 'para você mesma: não muda nenhuma nota, nenhuma resposta de'
    + 'elegibilidade e não conta nada a nenhum empregador.',
  // The questions this product cannot answer for her, asked where their
  // absence is visible. Not a wizard: every screen works with none of them
  // answered, and each says what stops working without it.
  'tags.remove': 'Remover {value}',
  'tags.noWeight': 'Estes ficam guardados como estão. Nada digitado aqui vira uma expressão '
    + 'que o comparador procura, e nada aqui muda uma nota.',
  'ask.start': 'Responder agora',
  'ask.step': 'Pergunta {n} de {of}',
  'ask.save': 'Salvar e continuar',
  'ask.skip': 'Pular esta',
  'ask.close': 'Fechar',
  'ask.takeMeThere': 'Me leve até lá',
  'ask.finished': 'Isto é tudo o que dava para perguntar. Você pode mudar qualquer coisa na '
    + 'página Perfil de carreira.',
  'ask.nothingToAsk': 'Não falta nada que uma pergunta pudesse preencher.',
  'ask.nothingTyped': 'Nada foi digitado, então não há o que salvar.',
  'ask.countriesHint': 'Duas letras cada, uma de cada vez. Um país aqui diz que um empregador '
    + 'que contrata especificamente ali pode contratar você. Não cria peso de'
    + 'pontuação e não é uma preferência.',
  'ask.residence': 'Onde você mora?',
  'ask.residenceWhy': 'É o dado que o portão de geografia compara com um anúncio. Sem ele, um '
    + 'anúncio que diz onde contrata não tem com o que ser comparado.',
  'ask.scopes': 'Quem pode te contratar?',
  'ask.scopesWhy': 'Esta é uma pergunta diferente de onde você mora, e é a que decide '
    + 'elegibilidade. Sem nenhuma marcada, nenhum anúncio consegue passar pelo '
    + 'portão de geografia: o corpus inteiro fica sem resposta.',
  'ask.pay': 'Qual é o seu alvo?',
  'ask.payWhy': 'Sem um alvo, a parte de remuneração de cada nota não tem com o que '
    + 'comparar e não concede nada. Um anúncio que informa salário continua'
    + 'aparecendo; só não dá para pontuá-lo por isso.',
  'ask.evidence': 'O que você realmente fez?',
  'ask.evidenceWhy': 'Até algo ser confirmado, cada exigência de cada anúncio aparece como '
    + 'lacuna. Ler seu currículo propõe afirmações; você confirma, corrige ou'
    + 'rejeita cada uma, e nada é acreditado por ter sido lido.',
  'ask.work': 'Que tipo de trabalho você procura?',
  'ask.workWhy': 'As expressões que você procura são o que um anúncio é comparado. '
    + 'Diferente das respostas acima, editar estas MUDA como cada anúncio é'
    + 'pontuado, então elas ficam em uma tela própria e dizem isso.',
  // The Career Profile page. The SERVER composed every heading, lede and
  // product-authored row label on it, so a Portuguese reader got an English
  // page inside a translated shell. A signal's own label and every stored
  // value stay exactly as the configuration wrote them.
  'profileSection.about': 'Sobre sua busca',
  'profileSection.aboutLead': 'O nome pelo qual esta busca é conhecida.',
  'profileSection.signals_desired': 'Sinais desejados',
  'profileSection.signals_desiredLead': 'Expressões que aumentam a correspondência quando um anúncio as contém. ',
  'profileSection.signals_negative': 'Sinais negativos',
  'profileSection.signals_negativeLead': 'Expressões que reduzem a correspondência sem excluir a vaga. ',
  'profileSection.signals_excluded': 'Exclusões absolutas',
  'profileSection.signals_excludedLead': 'Expressões que tiram uma vaga da visão de elegíveis. O anúncio precisa '
    + 'DIZER uma delas; silêncio nunca exclui.',
  'profileSection.place': 'Onde você pode trabalhar',
  'profileSection.placeLead': 'Um anúncio cuja região de contratação não inclui nenhuma destas não passa '
    + 'no portão de geografia. '
    + 'Um anúncio que não diz nada fica sem resposta -- silêncio não é recusa.',
  'profileSection.blockers': 'O que descarta uma vaga',
  'profileSection.blockersLead': 'Coisas que um empregador declara e você não atende. Cada uma precisa '
    + 'citar a frase em que se baseou, então nada é descartado por suposição.',
  'profileSection.shape': 'O formato do trabalho',
  'profileSection.shapeLead': 'Preferências, não portões. Um anúncio que discorda perde pontos e '
    + 'continua visível.',
  'profileSection.pay': 'Remuneração',
  'profileSection.payLead': 'Vale no máximo cinco pontos, e nunca é motivo para descartar uma vaga. '
    + 'Um anúncio que não informa salário não é penalizado por isso.',
  'profileRow.searchName': 'Você chama esta busca de',
  'profileRow.youAreIn': 'Você está em',
  'profileRow.hiredFrom': 'Você pode ser contratada de',
  'profileRow.workModels': 'Formas de trabalho que você aceita',
  'profileRow.contractsPreferred': 'Contratos que você prefere',
  'profileRow.contractsUnwanted': 'Contratos que você não quer',
  'profileRow.levels': 'Níveis que você procura',
  'profileRow.travel': 'Viagens que você aceita',
  'profileRow.aimingFor': 'Você tem como alvo',
  'profileRow.crossCurrency': 'Comparação entre moedas',
  'profileValue.travelUpTo': 'até {pct}% do tempo',
  'field.work_models': 'Como você se sente sobre cada formato de trabalho?',
  'field.require_remote': 'Mostrar apenas vagas totalmente remotas',
  'field.contract_preferred': 'Quais formatos de contratação funcionam para você?',
  'field.contract_unwanted': 'Entre eles, existe algum que você prefere menos?',
  'field.seniority_preferred': 'Quais níveis fazem sentido para você agora?',
  'field.seniority_excluded': 'Existe algum nível que você não quer receber?',
  'field.travel_max_pct': 'Quanto você se sentiria confortável viajando a trabalho?',
  'field.compensation_target': 'Quanto você gostaria de ganhar por mês?',
  'field.compensation_currency': 'Em qual moeda?',
  'field.candidate_country': 'Onde você mora atualmente?',
  'field.eligible_scopes': 'Em quais regiões você pode trabalhar?',
  'field.eligible_countries': 'Em quais países você pode ser contratado diretamente?',

  'fieldHelp.work_models': 'Preferir soma um pouco à aderência da vaga e evitar tira um pouco. Nunca mostrar também '
    + 'esconde essas vagas em Descobrir, com um clique para vê-las de novo. Nada disso muda '
    + 'onde você pode ser contratado: remoto não significa que a empresa contrata de qualquer lugar.',
  'fieldHelp.require_remote':
    'Calculado a partir das suas respostas: ligado quando híbrido e presencial nunca aparecem.',
  'fieldHelp.contract_preferred':
    'Funciona para mim soma um pouco à aderência da vaga e prefiro não tira um pouco. Muitas '
    + 'vagas não dizem, e então nada muda.',
  'fieldHelp.contract_unwanted':
    'Formatos que você prefere não ter. Uma vaga com um deles perde um pouco de aderência.',
  'fieldHelp.seniority_preferred':
    'Guardado para sua referência; ainda não muda notas. Para tirar um nível de Descobrir, '
    + 'use a próxima pergunta.',
  'fieldHelp.seniority_excluded':
    'Vagas nesses níveis não serão recomendadas para você. O que você já acompanha continua onde está.',
  'fieldHelp.travel_max_pct': 'Guardado para sua referência; ainda não muda notas nem esconde vagas.',
  'fieldHelp.compensation_target':
    'Use sua meta atual. Vagas que não informam salário continuam aparecendo normalmente.',
  'fieldHelp.compensation_currency': 'Moeda',
  'fieldHelp.candidate_country':
    'Isso nos ajuda a verificar quais vagas podem contratar pessoas de onde você está.',
  'fieldHelp.eligible_scopes':
    'Usamos isso para verificar se a região em que a vaga contrata inclui você.',
  'fieldHelp.eligible_countries':
    'Países onde uma empresa poderia colocar você na folha sem passar por mais '
    + 'ninguém. Deixe vazio se não houver nenhum.',

  'travel.none': 'Sem viagens',
  'travel.occasional': 'Viagens ocasionais',
  'travel.some': 'Algumas viagens',
  'travel.frequent': 'Viagens frequentes',
  'travel.heavy': 'Viajar é boa parte do trabalho',
  'travel.readout': '{pct}% do seu tempo de trabalho · {band}',

  'choiceGroup.where': 'Onde e como você quer trabalhar',
  'choiceGroup.whereLede': 'Onde você está, e até onde uma vaga consegue chegar.',
  'choiceGroup.what': 'O trabalho que você procura',
  'choiceGroup.whatLede': 'Nível, contrato e quanta viagem você toparia.',
  'choiceGroup.pay': 'Remuneração',
  'choiceGroup.payLede': 'Quanto você quer ganhar por mês.',
  'region.EUROPE': 'Europa',
  // The Save button says how much is pending, so pressing it is not a guess
  // about what it will write.
  'profile.saveOne': 'Salvar 1 mudança',
  'profile.saveMany': 'Salvar {n} mudanças',
  // Provenance, never quality. An excerpt from an aggregator and a genuinely
  // brief posting are the same length and opposite facts, and only one of
  // them has more to say somewhere else.
  'content.PARTIAL_CONTENT': 'Só um trecho',
  'content.METADATA_ONLY': 'Sem descrição guardada',
  'content.partialHelp': 'Esta fonte devolve um trecho curto e não oferece nenhuma forma de buscar '
    + 'o resto, então o que foi pontuado aqui é parte do anúncio. A página do '
    + 'próprio empregador tem o texto inteiro. Nada na nota é ajustado por'
    + 'isso: o texto foi lido exatamente como chegou.',
  'content.metadataHelp': 'Nenhum texto de descrição foi guardado para este anúncio, então não '
    + 'havia o que ler. O link continua indo para o empregador.',
  'facet.content_completeness': 'Quanto do anúncio nós temos',
  // Taking the selection away. The workflow ends at the employer's own form,
  // so the last useful thing this product can do is hand her the sentences
  // she already confirmed.
  'resume.copy': 'Copiar esta seleção',
  'resume.copied': 'Copiado. São suas próprias frases, nesta ordem, com as lacunas.',
  'resume.copyFailed': 'Seu navegador não deixou esta página escrever na área de transferência. '
    + 'O texto está selecionável abaixo.',
  'resume.exportLede': 'O que você levaria: estas frases, nesta ordem, e as lacunas embaixo '
    + 'delas. Nada nisso foi escrito por este programa sobre você.',
  // A slider at zero is not asking for anything, which is different from
  // asking for zero.
  'filters.anyValue': 'qualquer',
  // Server-composed English on the profile page. The ownership rule is
  // quoted by `doctor` too, which is why the server keeps sending the English
  // beside the key.
  'profile.bothDescribeYou': 'Os dois arquivos descrevem você. Onde discordam, o comparador lê as '
    + 'configurações de busca -- e a divergência é mostrada em vez de'
    + 'resolvida.',
  'profile.ownershipRule': 'O Perfil de Candidata é dono dos fatos sobre a pessoa; a configuração de '
    + 'busca é dona da maquinaria de correspondência.',

  // -- THE CAREER PROFILE, WHICH IS NOW A PERSON AS WELL AS A SEARCH ------
  'profileTab.overview': 'Visão geral',
  'profileTab.experience': 'Experiência',
  'profileTab.skills': 'Habilidades',
  'profileTab.preferences': 'Preferências',
  'profileFold.reading': 'Como uma vaga é lida',
  'profileFold.phrases': 'Grupos de frases',

  'profile.glanceHeading': 'O que você confirmou',
  // "no seu lugar", not "por você": the English says nobody confirmed these
  // ON HER BEHALF, and the first draft of this line said the opposite of
  // that -- that she had confirmed none of them.
  'profile.glanceLead': 'Fatos pelos quais você respondeu pessoalmente. Nada aqui foi '
    + 'confirmado no seu lugar.',
  'profile.countWork': 'Coisas que você fez',
  'profile.countSkills': 'Habilidades e ferramentas',
  'profile.countQuals': 'Certificados e formação',
  'profile.recentWork': 'Trabalho mais recente',
  'profile.yourSkills': 'Suas habilidades',
  'profile.andMoreSkills': 'E mais {n}, em Habilidades.',
  'profile.confirmedNote': 'Confirmar algo não muda uma recomendação. Suas evidências são lidas '
    + 'quando você prepara uma candidatura, e é de lá que vêm as palavras dessa candidatura.',
  'profile.openEvidence': 'Abrir suas evidências de carreira',
  'profile.nothingConfirmed': 'Nada confirmado ainda',
  'profile.nothingConfirmedLead': 'É aqui que aparece o trabalho que você já fez, depois que '
    + 'você disser quais partes dele são verdadeiras. Nada é confirmado no seu lugar.',

  'profile.employerNotStated': 'Empregador não informado',
  'profile.periodRange': '{start} a {end}',
  'profile.periodFrom': 'A partir de {start}',
  'profile.periodUntil': 'Até {end}',
  'profile.periodUnknown': 'Datas não informadas',
  'profile.showAllLines': 'Mostrar todas as {n}',
  'profile.showOlderRoles': 'Mostrar mais {n}',
  'profile.experienceLead': 'Agrupado por empregador e período, do mais recente para o mais '
    + 'antigo. São suas próprias frases confirmadas, palavra por palavra.',
  'profile.experienceNote': 'Um currículo e uma exportação do LinkedIn costumam nomear o mesmo '
    + 'empregador de formas diferentes, e nada aqui junta os dois: decidir que dois nomes são '
    + 'uma empresa só cabe a você. Dá para corrigir um nome em Evidências de carreira.',
  'profile.skillsHeading': 'Habilidades e ferramentas',
  'profile.skillsLead': 'Cada uma destas é uma palavra que você confirmou. Não há nota de '
    + 'proficiência aqui, porque nada neste produto mede isso.',
  'profile.qualsHeading': 'Certificados e cursos',
  'profile.educationHeading': 'Formação',
  'profile.certIssued': 'Emitido em {date}',
  'profile.certExpires': 'Expira em {date}',
  'profile.certNoExpiry': 'Sem expiração',
  'profile.certCredential': 'ID da credencial: {id}',

  // -- CAREER EVIDENCE: WHAT NEEDS YOU, THEN WHAT YOU HAVE ----------------
  'attend.waiting': '{n} itens precisam da sua revisão',
  'attend.oneWaiting': '1 item precisa da sua revisão',
  'attend.waitingLede': 'O Career Agent leu seus documentos e anotou o que acha que você '
    + 'fez. Nada disso vale até você dizer que sim.',
  'attend.continue': 'Revisar agora',
  'attend.clear': 'Você está em dia',
  'attend.clearLede': 'Tudo que foi lido dos seus documentos já foi respondido. O que você '
    + 'confirmou está abaixo.',
  'attend.nothingYet': 'Ainda não foi lido nada dos seus documentos. Adicione um currículo '
    + 'em Fontes e importações, ou escreva algo você mesma.',
  'attend.summary': '{confirmed} confirmadas, {retired} de lado',

  'intake.importTitle': '{documents}, lidos em {when}',
  'intake.importFrom': 'De {file}. {who}',
  'intake.reviewDone': 'Revisão completa. {n} revisadas.',
  'intake.needReview': '{n} ainda precisam de revisão',
  'intake.nothingIn': 'Nada foi lido de dentro dela',
  'intake.conflictCount': '{n} discordâncias para resolver antes',
  'intake.historyHeading': 'Importações anteriores ({n})',

  'ledger.details': 'Detalhes',
  'ledger.twoNames': 'Um currículo e uma exportação do LinkedIn costumam nomear o mesmo '
    + 'empregador de formas diferentes, e nada aqui junta os dois: decidir que dois nomes '
    + 'são uma empresa só cabe a você.',
  'ledger.cameFrom': 'De onde isto veio: {source}',
  'ledger.cameFromAt': 'De onde isto veio: {source}, {employer}',
  'ledger.employerNotStated': 'Empregador não informado',
  'ledger.groupWhen': '{who}, {start} a {end}',
  'ledger.groupFrom': '{who}, a partir de {start}',
  'ledger.filterLabel': 'Quais evidências mostrar',
  'ledger.state.ALL': 'Todas',
  'ledger.state.CONFIRMED': 'Confirmadas',
  'ledger.state.ASIDE': 'De lado',
  'ledger.sourceShort.RESUME': 'Currículo',
  'ledger.sourceShort.LINKEDIN': 'LinkedIn',
  'ledger.sourceShort.SELF_ATTESTED': 'Você adicionou',
  'ledger.sourceShort.DOCUMENT': 'Documento',

  // -- WHEN SOMETHING GOES WRONG ------------------------------------------
  //
  // Written for whoever is reading the screen, which is the whole point of
  // them. Three things every one of these does: it says what happened in
  // words nobody has to decode, it says whether anything was changed, and it
  // gives one thing to try. No status number, no filename, no command to run
  // in a terminal she may never have opened.
  //
  // `api.js` picks between these and the server's own sentence, and the
  // server decides which by marking a message as written for a reader.
  'error.serverFault': 'Algo deu errado dentro do Career Agent e esta mudança não foi feita. '
    + 'Tudo que já estava salvo continua seguro. Tente de novo e, se continuar acontecendo, '
    + 'feche o Career Agent e abra outra vez.',
  'error.notThere': 'Isso não está mais aqui. Recarregue a página para ver o que está.',
  'error.refused': 'Esse pedido foi recusado porque não veio desta página. Recarregue e tente '
    + 'de novo.',
  'error.tooBig': 'Esse arquivo é grande demais para enviar. Tente um menor.',
  'error.didNotWork': 'Isso não funcionou, e nada foi alterado. Tente de novo daqui a pouco.',
  'error.unreadable': 'A resposta voltou ilegível, então nada foi alterado. Tente de novo.',
  'error.notAnswering': 'O Career Agent não está respondendo. Ele roda no seu próprio '
    + 'computador, então normalmente isso quer dizer que ele foi fechado. Abra de novo e '
    + 'recarregue esta página.',
  'error.cancelled': 'Cancelado. Nada foi alterado.',
  'error.localModelOff': 'O modelo local não está rodando. Todo o resto desta página funciona '
    + 'sem ele, então nada mais é afetado.',
  'error.noCandidateHere': 'Esta prévia tem vagas e ninguém para comparar com elas. Abra o '
    + 'Career Agent de verdade para adicionar seu CV, confirmar o que você já fez e preparar '
    + 'uma candidatura.',
  'error.noFixture': 'Os dados da prévia não puderam ser carregados.',
  'error.noImportHere': 'Adicionar uma vaga à mão está desligado nesta prévia.',
  'setup.progress': 'Passo {n}',
  'setup.later': 'Fazer isso depois',
  'setup.back': 'Voltar',
  'setup.skip': 'Pular por enquanto',
  'setup.continue': 'Continuar',
  'setup.saving': 'Salvando…',
  'setup.pickOne': 'Escolha pelo menos uma opção, ou escolha Pular por enquanto.',
  'setup.welcome.title': 'Vamos configurar sua busca de emprego',
  'setup.welcome.why': 'Algumas perguntas curtas, uma de cada vez. Cada resposta é salva na hora, e você pode mudar '
    + 'qualquer uma depois em Configurações e fontes.',
  'setup.welcome.point1': 'Descreva, com suas palavras, o tipo de trabalho que você quer.',
  'setup.welcome.point2': 'Diga onde você mora e onde empresas podem te contratar, para o Career Agent saber quais '
    + 'vagas estão abertas para você.',
  'setup.welcome.point3': 'Depois, encontre suas primeiras vagas em sites de emprego públicos.',
  'setup.welcome.privacy': 'Tudo fica neste computador. Nada sobre você é enviado aos sites de vagas, e o Career '
    + 'Agent nunca se candidata a uma vaga por você.',
  'setup.welcome.start': 'Começar',
  'setup.work.title': 'Que trabalho você quer fazer a seguir?',
  'setup.work.why': 'O Career Agent lê a descrição inteira da vaga, não só o título, então descreva o trabalho em si.',
  'setup.work.label': 'Tipos de trabalho, um por linha',
  'setup.work.example': 'Por exemplo: onboarding de clientes, administração de folha de pagamento, análise de dados.',
  'setup.work.skillsLabel': 'Ferramentas ou métodos que você quer usar a seguir, um por linha (opcional)',
  'setup.work.note': 'Escolha estes de propósito. Eles descrevem o que você quer encontrar, não são copiados do '
    + 'seu currículo e não são uma afirmação sobre a sua experiência.',
  'setup.roles.title': 'Você tem cargos específicos em mente?',
  'setup.roles.why':
    'Opcional. Diga alguns cargos e o Career Agent também vai buscar por eles, e por cargos que '
    + 'significam o mesmo trabalho. São âncoras de busca, não limites.',
  'setup.review.roles': 'Cargos em mente',
  'roles.label': 'Cargos em mente',
  'roles.hint': 'Até {n} cargos, nas suas palavras. Pressione Enter depois de cada um.',
  'roles.placeholder': 'Por exemplo: Executivo de Contas',
  'roles.suggestions': 'Do seu Perfil de Carreira. Adicione só se quiser esse cargo de novo:',
  'roles.add': 'Adicionar {role}',
  'roles.suggestionsNote': 'Cargos que você já teve não são adicionados a menos que você os escolha.',
  'roles.aliases': 'Também buscamos por: {list}',
  'roles.notLimits':
    'Eles ajudam o Career Agent a fazer perguntas melhores às fontes de vagas. Vagas com outros títulos '
    + 'continuam aparecendo, e esses cargos nunca mudam um Search Fit.',
  'roles.full': 'Você pode indicar até {n} cargos.',
  'roles.saving': 'Salvando...',
  'roles.saved': 'Salvo.',
  'roles.notSaved': 'Não foi possível salvar seus cargos. Tente de novo ou pule esta etapa.',
  'roles.settingsTitle': 'Cargos em mente',
  'setup.work.required': 'Escreva pelo menos um tipo de trabalho, ou escolha Pular por enquanto.',
  'setup.work.already': 'Sua busca já descreve o trabalho que você quer ({n} frases). Você pode mudá-las depois em '
    + 'Configurações e fontes.',
  'setup.home.title': 'Onde você mora?',
  'setup.home.why': 'As vagas são comparadas com o lugar onde você está. Só o país é salvo, e só neste computador.',
  'setup.home.label': 'País',
  'setup.hire.title': 'Onde empresas podem te contratar?',
  'setup.hire.why': 'Muitas vagas remotas só contratam pessoas em certos países. Diga onde um empregador poderia te '
    + 'colocar na folha de pagamento diretamente, para o Career Agent saber quais vagas estão abertas para você.',
  'setup.hire.homeQuestion': 'Uma empresa pode te contratar diretamente em {country}?',
  'setup.hire.yes': 'Sim, posso trabalhar para empregadores em {country}',
  'setup.hire.unsure': 'Ainda não tenho certeza',
  'setup.hire.othersLabel': 'Outros países onde você pode ser contratado (opcional)',
  'setup.hire.countriesLabel': 'Países onde você pode ser contratado',
  'setup.hire.add': 'Adicionar',
  'setup.hire.remove': 'Remover {country}',
  'setup.hire.note': 'Não tem certeza? Deixe assim. As vagas vão dizer “ainda não se sabe” em vez de adivinhar.',
  'setup.regions.title': 'Alguma região de contratação inclui você?',
  'setup.regions.why': 'Algumas vagas citam uma região em vez de países, como “América Latina”. Marque uma região só '
    + 'se empresas que contratam nela podem contratar alguém que mora onde você mora.',
  'setup.regions.legend':
    'Regiões que incluem {country}',
  'setup.regions.note': 'Deixar uma região sem marcar nunca conta como um não.',
  'setup.region.WORLDWIDE': 'Qualquer lugar do mundo',
  'setup.region.AMERICAS': 'As Américas (do Norte, Central e do Sul)',
  'setup.region.LATAM': 'América Latina',
  'setup.region.NORTH_AMERICA': 'América do Norte (Estados Unidos e Canadá)',
  'setup.region.EMEA': 'Europa, Oriente Médio e África',
  'setup.region.APAC': 'Ásia e Pacífico',
  'setup.level.title': 'Algum nível que você quer tirar da sua lista?',
  'setup.level.why': 'Vagas nos níveis que você marcar ficam escondidas em Descobrir. Um clique mostra de novo, e '
    + 'nada é apagado.',
  'setup.level.legend': 'Esconder vagas nestes níveis',
  'setup.level.note': 'A maioria das pessoas deixa todos sem marcar.',
  'setup.pay.title': 'Que salário você busca?',
  'setup.pay.why': 'Usado para comparar com o salário que a vaga informa. Você pode pular.',
  'setup.pay.label': 'Valor por mês',
  'setup.pay.currency': 'Moeda',
  'setup.pay.chooseCurrency': 'Escolha uma moeda',
  'setup.pay.note': 'Fica neste computador.',
  'setup.pay.invalid': 'Digite um valor acima de zero, ou deixe em branco.',
  'setup.pay.needCurrency': 'Escolha a moeda deste valor.',
  'setup.ready.title':
    'Tudo pronto. E agora?',
  'setup.ready.why':
    'Encontre suas primeiras vagas agora, ou volte a isso pelo Início quando quiser.',
  'setup.ready.phrases': '{n} frases',
  'setup.ready.notAnswered': 'Não respondido',
  'setup.ready.change': 'Mudar',
  'setup.ready.changeLabel': 'Mudar {what}',
  'setup.ready.note': 'Encontrar vagas consulta sites de emprego públicos em busca de anúncios novos e pode levar '
    + 'alguns minutos. Nada sobre você é enviado, e nenhuma candidatura é feita.',
  'setup.ready.find': 'Encontrar vagas agora',
  'setup.ready.starting': 'Começando…',
  'setup.ready.finding': 'Encontrando vagas…',
  'setup.ready.progress': '{done} de {total} fontes de vagas consultadas',
  'setup.ready.progressLabel': 'Fontes de vagas consultadas',
  'setup.ready.keepUsing': 'Você pode continuar usando o Career Agent enquanto isso roda.',
  'setup.ready.stop': 'Parar',
  'setup.ready.findAgain': 'Procurar de novo',
  'setup.ready.cancelled': 'Parado. As vagas das {ok} fontes já consultadas foram mantidas.',
  'setup.ready.failed': 'A busca de vagas parou por causa de um problema. Nada do que você salvou foi alterado. '
    + 'Tente de novo em instantes.',
  'setup.ready.see': 'Ver suas vagas',
  'setup.ready.toHome': 'Ir para o Início',
  'empty.findJobs': 'Encontrar vagas agora',
  'settings.setupHead': 'Suas respostas',
  'settings.setupLede': 'Onde você mora, onde pode ser contratado, o trabalho que quer e o salário alvo. Revise uma '
    + 'de cada vez, a partir do que já está salvo.',
  'settings.setupOpen': 'Mudar minhas respostas',
  'pagehead.eyebrow.setup': 'Primeiros passos',
  'pagehead.title.setup': 'Configure sua busca',
  'setup.ready.none': 'Nenhum',
  'setup.progressLabel':
    'Progresso da configuração',
  'setup.saveAndReturn':
    'Salvar e voltar',
  'setup.work.savedRoles':
    'Tipos de trabalho que você informou',
  'setup.work.savedSkills':
    'Ferramentas e habilidades que você informou',
  'setup.work.changeInSettings':
    'Para mudar, abra Configurações e fontes, Frases de busca: cada uma aparece com quantas vagas alcança.',
  'setup.home.placeholder':
    'Comece a digitar um país',
  'setup.home.note':
    'Morar num lugar não quer dizer que toda empresa de lá pode te contratar. A próxima pergunta trata disso.',
  'setup.home.unknown':
    'Escolha um país da lista, ou deixe em branco.',
  'setup.workmodel.title':
    'Como você quer trabalhar?',
  'setup.workmodel.why':
    'Vagas num formato que você prefere sobem um pouco, e as que você prefere evitar descem um pouco.',
  'setup.workmodel.legend':
    'Sua resposta para cada formato de trabalho',
  'setup.workmodel.note':
    'Nunca mostrar esconde essas vagas em Descobrir, com um clique para vê-las de novo. Remoto é sobre '
    + 'como você trabalha, não sobre onde as empresas podem te contratar: isso é a pergunta de contratação '
    + 'que você já respondeu.',
  'setup.arrangement.title':
    'Quais formatos de contratação funcionam para você?',
  'setup.arrangement.why':
    'Quando a vaga diz como contrata, os formatos que funcionam para você sobem um pouco. Muitas vagas não dizem.',
  'setup.arrangement.legend':
    'Sua resposta para cada formato',
  'setup.arrangement.note':
    'Nada aqui esconde uma vaga.',
  'setup.cv.title':
    'Adicione seu currículo (opcional)',
  'setup.cv.why':
    'Ele é lido aqui, neste computador, e nada nele conta até você confirmar.',
  'setup.cv.noNeed':
    'Você pode procurar vagas sem adicionar um currículo.',
  'setup.cv.helps':
    'Adicionar seu currículo ajuda o Career Agent a montar suas Evidências de carreira e deixa a '
    + 'preparação do currículo mais útil. Cada linha vira uma afirmação para você confirmar ou recusar '
    + 'depois, em Evidências de carreira.',
  'setup.cv.added':
    'Dados de carreira adicionados. Revise quando quiser em Evidências de carreira.',
  'setup.cv.choose':
    'Escolher um arquivo',
  'setup.cv.read':
    'Ler este currículo',
  'setup.cv.chooseFirst':
    'Escolha um arquivo primeiro.',
  'setup.cv.reading':
    'Lendo {name}…',
  'setup.cv.found':
    '{n} afirmações encontradas. Nenhuma conta até você confirmar em Evidências de carreira.',
  'setup.cv.privacy':
    'Lê {kinds}. O arquivo não é guardado, e nada é enviado para lugar nenhum.',
  'setup.cv.skip':
    'Pular por enquanto',
  'setup.review.title':
    'Isto é o que você contou ao Career Agent',
  'setup.review.why':
    'Mude o que quiser aqui, ou depois em Configurações e fontes.',
  'setup.review.work':
    'Tipos de trabalho',
  'setup.review.home':
    'Mora em',
  'setup.review.hire':
    'Pode ser contratado em',
  'setup.review.workmodel':
    'Formatos de trabalho',
  'setup.review.arrangement':
    'Formatos de contratação',
  'setup.review.level':
    'Níveis escondidos',
  'setup.review.pay':
    'Salário alvo',
  'setup.review.cv':
    'Dados de carreira',
  'setup.review.hireUnknown':
    'Ainda não sabe',
  'setup.review.noPreference':
    'Sem preferência',
  'setup.review.cvAdded':
    'Currículo adicionado',
  'setup.review.cvNotAdded':
    'Ainda não adicionado',
  'setup.review.note':
    'Todas estas respostas continuam editáveis em Configurações e fontes.',
  'setup.review.looksRight':
    'Está certo',
  'setup.ready.noCv':
    'Você pode encontrar vagas sem currículo. Evidências de carreira e a preparação do currículo ficam '
    + 'úteis quando você adiciona um.',
  'setup.ready.addCv':
    'Adicionar dados de carreira',
  'setup.ready.settings':
    'Revisar configurações',
  'evstart.title': 'Monte seu banco de evidências',
  'evstart.body': 'Importe seu currículo e o Career Agent lista o que ele diz, uma linha de cada vez. Nada conta '
    + 'como sua experiência até você confirmar, e você pode editar ou recusar qualquer linha.',
  'evstart.import': 'Importar seu currículo',
  'evstart.hint': 'PDF, Word, texto ou Markdown. Ele é lido neste computador e o arquivo não é guardado. Você também '
    + 'pode adicionar uma experiência à mão abaixo.',
  'career.aboutEvidence': 'Exemplos, e para que isto serve',
  'board.empty': 'Nenhuma candidatura acompanhada ainda. Salve uma vaga ou mude o status dela em Descobrir e ela '
    + 'aparece aqui.',
  'board.toDiscover': 'Ir para Descobrir',
  'settings.sourceEach': 'Cada fonte de vagas ({n}, {paused} pausadas): situação e frequência de atualização',
  'workModel.REMOTE': 'Remoto',
  'workModel.HYBRID': 'Híbrido',
  'workModel.ONSITE': 'Presencial',
  'workModel.answer.prefer': 'Prefiro',
  'workModel.answer.fine': 'Tanto faz',
  'workModel.answer.avoid': 'Prefiro evitar',
  'workModel.answer.never': 'Nunca mostrar',
  'workModel.summary.prefer': 'Prefere {model}',
  'workModel.summary.avoid': 'Prefere evitar {model}',
  'workModel.summary.never': 'Nunca mostrar {model}',
  'arrangement.FULL_TIME_EMPLOYEE': 'Empregado, na folha da empresa (por exemplo CLT)',
  'arrangement.CONTRACTOR_B2B': 'Prestador: você emite nota para a empresa (por exemplo PJ ou B2B)',
  'arrangement.EOR': 'Contratado por um empregador de registro (EOR)',
  'arrangement.short.FULL_TIME_EMPLOYEE': 'empregado',
  'arrangement.short.CONTRACTOR_B2B': 'prestador',
  'arrangement.short.EOR': 'empregador de registro',
  'arrangement.answer.yes': 'Funciona para mim',
  'arrangement.answer.none': 'Sem preferência',
  'arrangement.answer.no': 'Prefiro não',
  'arrangement.summary.yes': 'Funciona: {kind}',
  'arrangement.summary.no': 'Prefere não: {kind}',
  'profileRow.workModelsAvoided': 'Formatos de trabalho que você prefere evitar',
  'profileRow.workModelsExcluded': 'Formatos de trabalho que nunca aparecem em Descobrir',
  'collect.now': 'Lendo agora {source} ({time})',
  'collect.elapsed': '{time} até agora',
  'collect.deferred': '{n} fontes puladas: pausadas, ou fora dos lugares onde você pode trabalhar',
  'collect.slow': 'Algumas fontes levam alguns minutos para ler. Continua funcionando.',
  'collect.noEta': 'O tempo restante não aparece: cada fonte leva um tempo diferente, então uma estimativa '
    + 'seria um palpite.',
  'collect.scoring': 'Avaliando as vagas que chegaram: {done} de {total}',
  'collect.scoringStart': 'Avaliando as vagas que chegaram…',
  'collect.scoringLabel': 'Vagas avaliadas',
  'collect.took': 'A leitura das fontes levou {time}.',
  'collect.stopping': 'Parando depois desta fonte…',
  'collect.finished': 'Pronto: {ok} de {total} fontes responderam, em {time}.',
  'collect.lastRun': 'Busca de vagas',
  'collect.show': 'Ver progresso',
  'collect.dismiss': 'Dispensar',
  'time.seconds': '{s} s',
  'time.minutes': '{m} min {s} s',
  'time.hours': '{h} h {m} min',
  'date.months': 'jan fev mar abr mai jun jul ago set out nov dez',
  'firstrun.finishTitle': 'Concluir configuração · faltam {n}',
  'firstrun.finished': 'Configuração concluída',
  'firstrun.ledeLeft': 'O que falta da sua configuração. Nada é obrigatório, e cada item diz o que permite ao '
    + 'Career Agent concluir. Mude respostas anteriores quando quiser em Configurações e fontes.',
  'tailor.needsCv': 'O Resume Tailor Beta trabalha a partir do seu currículo, e o Career Agent ainda não tem nada '
    + 'sobre a sua carreira. Adicione seu currículo, ou escreva o que você já fez, em Evidências de carreira '
    + 'primeiro.',
  'tailor.addCv': 'Adicionar seu currículo',
  'tailor.openOwn': 'Já deu seu currículo ao Resume Tailor? Abrir Resume Tailor Beta',
  'setup.work.tooMany': 'São {n} linhas. Fique com as 20 que mais importam: cada uma é procurada em todas as vagas.',
  'setup.work.tooLong': 'A linha {line} está longa para uma frase de busca. Use poucas palavras em cada linha, '
    + 'como um cargo ou uma ferramenta.',
  // -- Career Workspace: profile, evidence, documents, guided import --
  'nav.documents': 'Documentos',
  'nav.manage': 'Gerenciar afirmações',
  'pagehead.eyebrow.documents': 'Documentos',
  'pagehead.title.documents': 'Seus documentos',
  'pagehead.sub.documents': 'Os currículos e documentos que o Career Agent leu, e o que falta revisar.',
  'pagehead.eyebrow.manage': 'Avançado',
  'pagehead.title.manage': 'Todas as afirmações',
  'pagehead.sub.profile': 'Quem você é e o que você procura.',
  'pagehead.sub.evidence': 'A prova por trás da sua experiência: projetos, conquistas e certificações.',
  'profileHead.import': 'Importar currículo',
  'profileHead.edit': 'Editar perfil',
  'ui.period': '{start} a {end}',
  'ui.periodCurrent': '{start} até hoje',
  'ui.periodPresentOnly': 'Atual',
  'ui.datesNotStated': 'Datas não informadas',
  'ui.dateUnknown': 'Não informado',
  'ui.removeChip': 'Remover {name}',
  'ui.removeLine': 'Remover: {text}',
  'ui.cancel': 'Cancelar',
  'ui.undo': 'Desfazer',
  'ui.undone': 'Desfeito.',
  'ui.close': 'Fechar',
  'ui.noLine': 'De {where}. Nenhuma linha exata foi guardada.',
  'ui.writtenByYou': 'Escrito por você',
  'ui.yourDocument': 'Seu documento',
  'ui.line': 'linha {n}',
  'ui.source': 'Fonte: {where}',
  'ui.viewSource': 'Ver fonte',
  'ui.asWritten': 'Como está no arquivo',
  'xp.heading': 'Experiência',
  'xp.listLabel': 'Suas experiências, das mais recentes às mais antigas',
  'xp.add': 'Adicionar experiência',
  'xp.addHint': 'Um cargo, um trabalho freelance ou um projeto paralelo.',
  'xp.empty': 'Nenhuma experiência no seu perfil ainda. Importe seu currículo ou adicione uma você mesma.',
  'xp.editingEyebrow': 'Editando perfil',
  'xp.editingText': 'As mudanças aqui atualizam o que a preparação de candidaturas pode usar. O Search Fit não muda.',
  'xp.doneEditing': 'Concluir edição',
  'xp.waitingNote': '{n} detalhes dos seus documentos estão esperando a sua revisão.',
  'xp.waitingNoteOne': '1 detalhe dos seus documentos está esperando a sua revisão.',
  'xp.continueReview': 'Continuar revisão',
  'xp.roleNotStated': 'Cargo não informado',
  'xp.companyNotStated': 'Empresa não informada',
  'xp.edit': 'Editar',
  'xp.editLabel': 'Editar {role} em {company}',
  'xp.remove': 'Remover',
  'xp.removeLabel': 'Remover {role} em {company} do seu perfil',
  'xp.removeQuestion': 'Remover esta experiência do seu perfil?',
  'xp.removeDetail': 'Os detalhes dela continuam nas suas evidências, fora de uma experiência. Dá para desfazer.',
  'xp.removeConfirm': 'Remover do perfil',
  'xp.removed': '{role} removida do seu perfil.',
  'xp.cardLabel': '{role} em {company}',
  'xp.skillsLabel': 'Habilidades usadas',
  'xp.detailsWaiting': '{n} detalhes para revisar',
  'xp.detailsWaitingOne': '1 detalhe para revisar',
  'xp.reviewThem': 'Revisar',
  'xp.reviewThemLabel': 'Revisar {n} detalhes de {role}',
  'xp.reviewThemLabelOne': 'Revisar 1 detalhe de {role}',
  'xp.noMonth': 'Mês (opcional)',
  'xp.monthOf': '{label}: mês',
  'xp.yearOf': '{label}: ano',
  'xp.yearPlaceholder': 'Ano',
  'xp.start': 'Início',
  'xp.end': 'Fim',
  'xp.current': 'Trabalho aqui atualmente',
  'xp.role': 'Cargo',
  'xp.company': 'Empresa',
  'xp.description': 'O que você fazia',
  'xp.highlights': 'Destaques',
  'xp.highlightsHint': 'Um resultado ou responsabilidade por linha. Cada um é salvo como uma afirmação sua.',
  'xp.addHighlight': '+ Adicionar destaque',
  'xp.highlightPlaceholder': 'O que você fez, e o que mudou por causa disso',
  'xp.skills': 'Habilidades usadas',
  'xp.skillPlaceholder': 'Digite uma habilidade e aperte Enter',
  'xp.changeType': 'Mudar o tipo',
  'xp.typeLabel': 'Tipo de experiência',
  'xp.toolsFromHighlights': 'Também citados nos seus destaques: {names}. Altere o destaque para removê-los.',
  'xp.save': 'Salvar alterações',
  'xp.saved': 'Alterações salvas.',
  'xp.cancel': 'Cancelar',
  'xp.newEyebrow': 'Nova experiência',
  'xp.editEyebrow': 'Editar experiência',
  'xp.editorNote': 'Usada ao preparar candidaturas',
  'xp.newLabel': 'Nova experiência',
  'xp.editingLabel': 'Editando {role}',
  'evp.heading': 'Evidências profissionais',
  'evp.lede': 'Provas que você confirmou. A preparação de candidaturas as cita; elas não mudam o Search Fit.',
  'evp.add': '+ Adicionar evidência',
  'evp.emptyTitle': 'Nenhuma evidência ainda',
  'evp.emptyBody': 'Adicione um projeto, uma conquista ou uma certificação, ou importe seu currículo em Documentos.',
  'evp.emptyImport': 'Importar seu currículo',
  'evp.emptyAdd': 'Adicionar você mesmo',
  'evp.group.PROJECT': 'Projetos',
  'evp.group.ACHIEVEMENT': 'Conquistas',
  'evp.group.CERTIFICATION': 'Certificações',
  'evp.group.EDUCATION': 'Formação',
  'evp.type.PROJECT': 'Projeto',
  'evp.type.ACHIEVEMENT': 'Conquista',
  'evp.type.CERTIFICATION': 'Certificação',
  'evp.type.EDUCATION': 'Formação',
  'evp.typeHint.PROJECT': 'Algo que você construiu ou liderou',
  'evp.typeHint.ACHIEVEMENT': 'Uma palestra, prêmio ou reconhecimento',
  'evp.typeHint.CERTIFICATION': 'Um curso, exame ou credencial',
  'evp.entries': '{n} itens',
  'evp.entriesOne': '1 item',
  'evp.foldTitle': '{name} ({n})',
  'evp.asideTitle': 'Fora de uso ({n})',
  'evp.asideLede': 'Afirmações que você deixou de usar, e outras anotadas mas nunca confirmadas. '
    + 'Elas mantêm o histórico.',
  'evp.draft': 'Ainda não confirmada',
  'evp.retired': 'Fora de uso',
  'evp.confirmDraft': 'Confirmar',
  'evp.useAgain': 'Usar de novo',
  'evp.backInUse': 'De volta ao uso.',
  'evp.open': 'Abrir',
  'evp.openLabel': 'Abrir {title}',
  'evp.stopUsing': 'Deixar de usar',
  'evp.stopLabel': 'Deixar de usar {title}',
  'evp.stopQuestion': 'Deixar de usar esta evidência?',
  'evp.stopDetail': 'Ela continua, com o histórico, e a preparação de candidaturas deixa de citá-la. '
    + 'Dá para desfazer.',
  'evp.stopConfirm': 'Deixar de usar',
  'evp.stopped': 'Fora de uso.',
  'evp.skills': 'Habilidades',
  'evp.linkedTo': 'Vinculada a {role} · {company}',
  'evp.roleNotStated': 'Cargo não informado',
  'evp.untitled': 'Evidência',
  'evp.history': 'Histórico ({n} versões)',
  'evp.historyOne': 'Histórico (1 versão)',
  'evp.revision': 'Versão {n}',
  'evp.sourceHeading': 'De onde veio',
  'evp.close': 'Fechar',
  'evp.edit': 'Editar',
  'evp.newEyebrow': 'Nova evidência',
  'evp.editEyebrow': 'Editar evidência',
  'evp.addTitle': 'Adicionar evidência',
  'evp.editTitle': 'Editar evidência',
  'evp.addLede': 'A prova por trás da sua experiência. A preparação de candidaturas a cita ao explicar uma vaga.',
  'evp.addForRequirement': 'Para o requisito "{requirement}". '
    + 'Escreva o que você fez; fica salvo como uma afirmação sua.',
  'evp.typeLabel': 'Tipo',
  'evp.title': 'Título',
  'evp.titlePlaceholder': 'ex.: Redesenho do checkout',
  'evp.when': 'Quando (opcional)',
  'evp.whenHint': 'Deixe vazio se não lembrar o mês.',
  'evp.linkedExperience': 'Experiência vinculada',
  'evp.notLinked': 'Sem vínculo',
  'evp.description': 'Descrição',
  'evp.descriptionPlaceholder': 'O que foi, e qual foi a sua parte.',
  'evp.skillPlaceholder': 'Digite uma habilidade e aperte Enter',
  'evp.writtenNote': 'Salva como algo que você escreveu. Nunca aparece como citação de um documento.',
  'evp.editKeepsQuote': 'Suas palavras viram uma nova versão. A linha do seu documento continua ao lado.',
  'evp.needTitle': 'Dê um título primeiro.',
  'evp.save': 'Salvar evidência',
  'evp.saved': 'Evidência adicionada.',
  'evp.savedEdit': 'Evidência salva.',
  'evp.cancel': 'Cancelar',
  'docs.heading': 'Documentos',
  'docs.importTitle': 'Importar seu currículo',
  'docs.importBody': 'O Career Agent lê e lista o que encontrou, experiência por experiência. Nada '
    + 'conta como seu até você confirmar.',
  'docs.finds.experiences': 'Experiências, com empresa, cargo e datas',
  'docs.finds.skills': 'Habilidades e ferramentas',
  'docs.finds.certifications': 'Certificações e cursos',
  'docs.finds.education': 'Formação',
  'docs.choose': 'Escolher um arquivo',
  'docs.noFile': 'PDF, Word, texto ou Markdown',
  'docs.privacy': 'Lido neste computador. O arquivo em si não é guardado.',
  'docs.reading': 'Lendo {name}...',
  'docs.yourImports': 'Suas importações',
  'docs.empty': 'Nada importado ainda.',
  'docs.archivedFold': 'Arquivados ({n})',
  'docs.status.archived': 'Arquivado',
  'docs.status.replaced': 'Substituído por uma leitura mais nova',
  'docs.status.waiting': '{n} para revisar',
  'docs.status.done': 'Tudo respondido',
  'docs.kind.cv': 'Currículo',
  'docs.kind.package': 'Pacote de documentos',
  'docs.meta': '{kind} · lido em {date} · {experiences} · {confirmed}',
  'docs.experiences': '{n} experiências',
  'docs.experiencesOne': '1 experiência',
  'docs.confirmed': '{n} confirmadas',
  'docs.confirmedOne': '1 confirmada',
  'docs.continue': 'Continuar revisão',
  'docs.lookAgain': 'Ver de novo',
  'docs.reviewLabel': 'Revisar {name}',
  'docs.restore': 'Restaurar',
  'docs.restored': '{name} restaurado.',
  'docs.lookInside': 'Ver o conteúdo',
  'docs.useThis': 'Usar esta leitura',
  'docs.inUse': '{name} agora é a leitura em uso.',
  'docs.archive': 'Arquivar',
  'docs.archiveLabel': 'Arquivar {name}',
  'docs.archived': '{name} arquivado. Nada nele espera por você.',
  'docs.delete': 'Excluir...',
  'docs.deleteLabel': 'Excluir {name}',
  'docs.deleteQuestion': 'Excluir {name} permanentemente?',
  'docs.deleteAll': 'Todas as {removed} sugestões dele saem ({pending} sem resposta). Nada do que '
    + 'você confirmou veio dele.',
  'docs.deleteKeeps': '{removed} sugestões não confirmadas saem ({pending} sem resposta). As '
    + '{confirmed} que você confirmou ficam, com as linhas de onde vieram.',
  'docs.deleteForever': 'Isso não pode ser desfeito. Para manter tudo, arquive em vez de excluir.',
  'docs.deleteConfirm': 'Excluir permanentemente',
  'docs.deleted': '{name} excluído.',
  'imp.heading': 'Revise o que seu documento diz',
  'imp.railLabel': 'Etapas da revisão',
  'imp.readOn': 'Lido em {date}',
  'imp.savedAsYouGo': 'Cada resposta é salva na hora. Você pode parar e voltar depois.',
  'imp.step.experiences': 'Experiências',
  'imp.step.skills': 'Habilidades',
  'imp.step.certifications': 'Certificações e cursos',
  'imp.step.education': 'Formação e idiomas',
  'imp.step.other': 'Outros detalhes',
  'imp.step.summary': 'Resumo',
  'imp.unnamed': 'Sem nome',
  'imp.archivedNote': 'Esta importação está arquivada. Restaure em Documentos para responder.',
  'imp.replacedNote': 'Uma leitura mais nova dos seus documentos está em uso. Escolha "Usar esta '
    + 'leitura" em Documentos para revisar esta.',
  'imp.noExperiences': 'Nenhuma experiência encontrada',
  'imp.noExperiencesBody': 'O documento não citou empresa ou cargo que o Career Agent conseguisse '
    + 'ler. O resto vem a seguir.',
  'imp.continue': 'Continuar',
  'imp.experienceOf': 'Experiência {n} de {total}',
  'imp.experienceLabel': '{role} em {company}',
  'imp.roleNotStated': 'Cargo não informado',
  'imp.companyNotStated': 'Empresa não informada',
  'imp.state.new': 'Nova',
  'imp.state.newBody': 'Ainda não está no seu perfil.',
  'imp.state.existing': 'Já está no seu perfil',
  'imp.state.unchangedBody': 'Sem mudanças. Nada novo aqui.',
  'imp.state.newDetailsBody': '{n} detalhes novos encontrados.',
  'imp.state.newDetailsBodyOne': '1 detalhe novo encontrado.',
  'imp.state.dates': 'Confira as datas',
  'imp.state.datesBody': 'Seu documento e seu perfil discordam.',
  'imp.state.help': 'Precisa da sua ajuda',
  'imp.state.helpBody': 'O Career Agent não conseguiu identificar a empresa ou o cargo.',
  'imp.placeNew': 'Adicione ao seu perfil para revisar os detalhes lá também. Isso não confirma nada.',
  'imp.addToProfile': 'Adicionar ao perfil',
  'imp.added': '{role} adicionada ao seu perfil.',
  'imp.placeExisting': 'Estes detalhes são de {role} em {company} no seu perfil.',
  'imp.keepTogether': 'Manter juntos',
  'imp.keptTogether': 'Mantidos com essa experiência.',
  'imp.whichDates': 'Quais datas estão certas?',
  'imp.inProfile': 'No seu perfil',
  'imp.inDocument': 'No seu documento',
  'imp.useDates': 'Usar as datas {where}: {dates}',
  'imp.datesKept': 'As datas do seu perfil foram mantidas.',
  'imp.datesKeptNote': 'As datas do seu perfil foram mantidas: {dates}.',
  'imp.datesUpdated': 'Datas atualizadas a partir do documento.',
  'imp.helpAsk': 'Diga ao Career Agent o que é isto. Nada é adivinhado.',
  'imp.role': 'Cargo',
  'imp.company': 'Empresa',
  'imp.saveStructure': 'Salvar',
  'imp.structureSaved': 'Salvo.',
  'imp.details': '{n} detalhes encontrados',
  'imp.detailsOne': '1 detalhe encontrado',
  'imp.tally': '{confirmed} confirmados · {waiting} para revisar',
  'imp.noDetails': 'Nenhum detalhe nesta experiência.',
  'imp.headingSource': 'O que o documento dizia sobre este trabalho',
  'imp.sourceLine': 'Linha {n}: {text}',
  'imp.dontImport': 'Não importar',
  'imp.dontImportLabel': 'Não importar os detalhes sem resposta de {role}',
  'imp.skipped': '{n} detalhes deixados de fora.',
  'imp.skippedOne': '1 detalhe deixado de fora.',
  'imp.previous': 'Anterior',
  'imp.nextExperience': 'Próxima experiência',
  'imp.continueTo': 'Continuar para {step}',
  'imp.edit': 'Editar',
  'imp.editLabel': 'Suas palavras',
  'imp.editItemLabel': 'Editar: {text}',
  'imp.cancel': 'Cancelar',
  'imp.confirmMyWords': 'Confirmar minhas palavras',
  'imp.notSure': 'Ainda não sei',
  'imp.notSureLabel': 'Ainda não sei: {text}',
  'imp.reject': 'Rejeitar',
  'imp.rejectLabel': 'Rejeitar: {text}',
  'imp.confirm': 'Confirmar',
  'imp.confirmLabel': 'Confirmar: {text}',
  'imp.reopen': 'Reabrir',
  'imp.confirmed': 'Confirmado.',
  'imp.confirmedEdit': 'Confirmado com suas palavras.',
  'imp.rejected': 'Rejeitado.',
  'imp.unsure': 'Deixado para depois.',
  'imp.reopened': 'De volta para revisão.',
  'imp.itemState.waiting': 'Para revisar',
  'imp.itemState.unsure': 'Ainda não sei',
  'imp.itemState.confirmed': 'Confirmado',
  'imp.itemState.rejected': 'Rejeitado',
  'imp.stateOf': '{state}: {name}',
  'imp.alreadyThere': 'Já está no seu perfil',
  'imp.duplicate': 'Aparece duas vezes',
  'imp.conflictNote': 'Seus documentos discordam sobre isto. Escolha a versão certa.',
  'imp.chooseThis': 'Esta está certa',
  'imp.youChanged': 'O Career Agent leu primeiro: {text}',
  'imp.found.skills': '{n} habilidades encontradas',
  'imp.found.skillsOne': '1 habilidade encontrada',
  'imp.found.certifications': '{n} certificações e cursos encontrados',
  'imp.found.certificationsOne': '1 certificação ou curso encontrado',
  'imp.found.education': '{n} itens de formação e idiomas encontrados',
  'imp.found.educationOne': '1 item de formação ou idioma encontrado',
  'imp.found.other': '{n} outros detalhes encontrados',
  'imp.found.otherOne': '1 outro detalhe encontrado',
  'imp.oneByOne': 'Cada um é uma resposta, salva na hora.',
  'imp.keepExisting': 'Manter o que já está no perfil',
  'imp.keepExistingLabel': 'Manter o que já está no seu perfil: {text}',
  'imp.keptExisting': 'Mantido o que já estava no seu perfil. Nada foi adicionado.',
  'imp.leftToAnswer': '{n} para responder',
  'imp.legendNew': '+ toque para manter',
  'imp.legendIn': '✓ mantida',
  'imp.legendOut': '× deixar de fora',
  'imp.legendHint': 'Toque numa habilidade para manter, ou × para deixar de fora.',
  'imp.keepLabel': 'Manter {name}',
  'imp.leaveOutLabel': 'Deixar de fora {name}',
  'imp.kept': '{name} mantida.',
  'imp.leftOut': '{name} deixada de fora.',
  'imp.summaryWaiting': 'Quase lá',
  'imp.summaryDone': 'Revisão concluída',
  'imp.tileConfirmed': 'confirmados',
  'imp.tileWaiting': 'ainda para revisar',
  'imp.tileRejected': 'deixados de fora',
  'imp.tileExperiences': 'experiências no seu perfil',
  'imp.summaryNote': 'Tudo o que você respondeu já está salvo. Os detalhes confirmados são o que a '
    + 'preparação de candidaturas pode usar; o Search Fit não muda.',
  'imp.reviewWaiting': 'Revisar o que falta',
  'imp.finish': 'Voltar aos documentos',
  'ai.head': 'IA e correspondência semântica',
  'ai.intro': 'O Career Agent pode pedir a uma IA que reconheça o trabalho que você quer em vagas que o descrevem '
    + 'com outras palavras. A IA só interpreta: o Career Agent confere cada citação com a vaga e faz todo o '
    + 'cálculo da pontuação.',
  'ai.enabled': 'Usar correspondência semântica com IA',
  'ai.provider': 'Provedor',
  'ai.mode.auto': 'Automático',
  'ai.mode.deepseek': 'DeepSeek Flash',
  'ai.mode.codex': 'Codex',
  'ai.mode.claude_code': 'Claude Code',
  'ai.mode.laya': 'Laya (local)',
  'ai.mode.deterministic': 'Somente determinístico',
  'ai.mode.fake': 'Provedor de teste',
  'ai.summary.ready': 'Correspondência semântica com IA: pronta',
  'ai.summary.off': 'A correspondência semântica com IA está desligada. A aderência usa só as suas frases.',
  'ai.summary.deterministic': 'Somente determinístico. A aderência usa as suas frases, sem IA.',
  'ai.summary.unavailable': 'Nenhum provedor de IA está disponível. A aderência usa só as suas frases.',
  'ai.summary.demo': 'O modo demonstração nunca usa IA.',
  'ai.using': 'Usando {provider}',
  'ai.fellBack': '{preferred} não está disponível, então {used} é usado no lugar.',
  'ai.fellBackDeterministic': '{preferred} não está disponível. A aderência usa só as suas frases até que esteja.',
  'ai.saved': 'Salvo.',
  'ai.providersHead': 'Provedores e privacidade',
  'ai.state.AVAILABLE': 'Disponível',
  'ai.state.CONNECTED': 'Conectado',
  'ai.state.NOT_INSTALLED': 'Não instalado',
  'ai.state.SIGN_IN_REQUIRED': 'Login necessário',
  'ai.state.KEY_MISSING': 'Chave de API ausente',
  'ai.state.CONNECTION_FAILED': 'Falha na conexão',
  'ai.state.LIMIT_OR_ERROR': 'Indisponível ou limite atingido',
  'ai.state.UNSUPPORTED': 'Não suportado para correspondência semântica',
  'ai.authApiKey': 'Conectado com uma chave de API, que cobraria a API em vez da sua assinatura. Entre com a sua '
    + 'assinatura para usar aqui.',
  'ai.billing.METERED_API': 'Pago por uso com a sua própria chave de API. O Career Agent estima cada execução e '
    + 'para no seu orçamento.',
  'ai.billing.SUBSCRIPTION': 'Usa a sua própria assinatura e os limites dela. O Career Agent não tem como saber um '
    + 'preço.',
  'ai.billing.LOCAL': 'Roda neste computador.',
  'ai.sends.deepseek': 'Envia a sua intenção de busca e cada vaga avaliada para a DeepSeek.',
  'ai.sends.codex': 'Envia a sua intenção de busca e cada vaga avaliada pelo seu próprio login do Codex.',
  'ai.sends.claude_code': 'Envia a sua intenção de busca e cada vaga avaliada pelo seu próprio login do Claude Code.',
  'ai.sends.laya': 'Nada sai deste computador.',
  'ai.laya.why': 'O Laya responde sem citar a vaga, então as respostas dele não podem ser conferidas como '
    + 'evidência. Ele não é usado na aderência.',
  'ai.check': 'Verificar conexão',
  'ai.checking': 'Verificando',
  'ai.key.label': 'Chave de API da DeepSeek',
  'ai.key.placeholder': 'Cole a sua chave',
  'ai.key.configured': 'Há uma chave de API configurada neste computador.',
  'ai.key.missing': 'Ainda não há chave de API.',
  'ai.key.add': 'Salvar chave',
  'ai.key.replace': 'Substituir chave',
  'ai.key.remove': 'Remover chave',
  'ai.key.help': 'Fica só no arquivo .env deste computador. Nunca é mostrada de novo, nunca é salva no banco de '
    + 'dados e nunca é enviada a nenhum lugar além da DeepSeek.',
  'ai.key.saved': 'Chave salva.',
  'ai.key.removed': 'Chave removida.',
  'ai.privacyHead': 'O que sai deste computador',
  'ai.privacy.sends': 'Enviado ao provedor: o trabalho, as ferramentas e os outros sinais que você disse querer, e '
    + 'o título e o texto de cada vaga avaliada.',
  'ai.privacy.never': 'Nunca enviado: seu Perfil de Carreira, suas evidências, arquivos de currículo, histórico de '
    + 'candidaturas, outras vagas ou o seu banco de dados.',
  'ai.runHead': 'Avaliar vagas',
  'ai.budget': 'Orçamento da DeepSeek por execução (USD)',
  'ai.budgetHelp': 'Um limite rígido: a execução termina antes de poder gastar mais.',
  'ai.estimate': 'Estimar',
  'ai.plan': '{candidates} vagas nesta execução, de {eligible} que combinam com a sua intenção. Estimativa de '
    + '{expected}, no máximo {worst}, dentro de um orçamento de {budget}.',
  'ai.planSubscription': '{candidates} vagas nesta execução, de {eligible} que combinam com a sua intenção. Usa os '
    + 'limites da sua assinatura.',
  'ai.planNone': 'Nada novo para avaliar.',
  'ai.run': 'Avaliar {count} vagas',
  'ai.running': 'Avaliando {done} de {total}',
  'ai.cancel': 'Parar',
  'ai.recalc': 'A aderência será recalculada para as vagas avaliadas.',
  'ai.last': 'Última execução: {published} vagas avaliadas, {failed} falharam, {calls} chamadas, {tokens} tokens, '
    + '{spent} gastos.',
  'ai.lastSubscription': 'Última execução: {published} vagas avaliadas, {failed} falharam, {calls} chamadas, '
    + '{tokens} tokens.',
  'readiness.NOT_READY': 'A aderência à busca ainda não está pronta: diga ao Career Agent que trabalho você quer a '
    + 'seguir.',
  'readiness.PARTIAL': 'A aderência à busca já pode ser usada. Ela fica mais precisa quando você também informa '
    + '{missing}.',
  'readiness.READY': 'A aderência à busca está pronta: ela conhece o trabalho, o nível e o jeito de trabalhar que '
    + 'você quer.',
  'readiness.missing.level': 'o nível que você quer',
  'readiness.missing.work_model': 'como você quer trabalhar',
  'readiness.missing.work': 'o trabalho que você quer',
  'readiness.join': ' e ',
  'badge.notReady': 'Não pronta',
  'badge.notReadyHelp': 'A aderência à busca precisa saber que trabalho você quer a seguir. Informe isso na '
    + 'configuração guiada ou em Configurações.',
  'component.responsibilities': 'Trabalho que você quer',
  'component.technologies': 'Ferramentas e métodos',
  'component.automation_integration': 'Outros sinais desejados',
  'drawer.notConfigured': 'Não faz parte da sua busca: você não listou nada aqui, então isto não conta a favor nem '
    + 'contra esta vaga.',
  'drawer.alreadyCounted': 'Já contado',
  'drawer.semanticFinding': 'Reconhecido por IA, citado da vaga',
  'drawer.semanticUsed': '{provider} ajudou a reconhecer o trabalho que você quer; cada citação abaixo foi '
    + 'conferida com a vaga.',
  'setup.work.intent': 'Esta é a sua intenção de busca: o trabalho que você quer a seguir, que pode ser diferente '
    + 'do que você já fez. A sua trajetória fica no seu Perfil de Carreira e nunca é usada como frase de busca.',
  'ai.error.failed': 'Não funcionou. Nada foi alterado; tente de novo em instantes.',
  'ai.error.busy': 'Outra coisa está em andamento. Tente de novo quando terminar.',
  'ai.error.key': 'Isso não parece uma chave de API da DeepSeek.',
  'ai.error.setting': 'Essa configuração não é válida.',
  'ai.stop.BUDGET': 'A execução parou no orçamento.',
  'ai.stop.PROVIDER_STOPPED': 'A execução parou porque o provedor deixou de responder.',
  'ai.stop.CANCELLED': 'A execução foi interrompida.',
  'ai.stop.NOTHING_NEW': 'Nada novo para avaliar.',
  'ai.stop.NO_PROVIDER': 'Nenhum provedor de IA está disponível.',
  'ai.stop.NO_INTENT': 'Diga primeiro ao Career Agent que trabalho você quer.',
  'ai.stop.NO_INDEX': 'Recalcule a aderência primeiro e tente de novo.',
  'ai.stop.PRICE_UNKNOWN': 'Este provedor não tem preço conhecido, então não pode ser limitado por um orçamento.',
  'ai.stop.ERROR': 'A execução parou por causa de um erro.',
  'drawer.toolsGuard':
    'Limitado à metade: esta vaga usa ferramentas que você quer, e nenhum trabalho que você quer foi '
    + 'encontrado nela.',
};

const CATALOGUES = { en: EN, 'pt-BR': PT_BR };

let current = DEFAULT_LOCALE;

/**
 * The locale to start in: an explicit past choice, or English.
 *
 * **Nothing is inferred.** Not `navigator.language`, not a timezone, not the
 * candidate's country, not the language a posting happens to be written in.
 * This read `navigator.language` for one afternoon and the reasons it is gone
 * are worth keeping written down:
 *
 * A guess is indistinguishable from a decision once it is on screen. Somebody
 * whose browser reports `pt-BR` because of an operating system they installed
 * years ago has not asked for a Portuguese interface, and a product that
 * decides for them has to be argued with rather than used.
 *
 * And it made the whole test suite depend on the machine. The build box here
 * reports `pt-BR`, so every English assertion in the browser suite passed or
 * failed on an operating system setting -- the same "tests must not depend on
 * local configuration" rule this repository had to fix once already for a
 * config path.
 *
 * The candidate's COUNTRY is a separate matter and stays separate. Living in
 * Brazil is not a request to read software in Portuguese, and wiring the two
 * together would make a UI preference a consequence of an eligibility fact.
 *
 * Wrapped, because `localStorage` throws in a private window and in browsers
 * set to block site data, and a remembered preference is not worth a blank
 * page.
 */
export function initialLocale() {
  try {
    const stored = window.localStorage.getItem(LOCALE_KEY);
    if (stored && LOCALES.includes(stored)) return stored;
  } catch (error) {
    /* fall through to English */
  }
  return DEFAULT_LOCALE;
}

/**
 * Switch, and tell the page what language it is in.
 *
 * `persist` is FALSE by default and that is the important half. The first
 * paint calls this with whatever `initialLocale` guessed from the browser, and
 * writing that guess down would make it indistinguishable from a choice: the
 * person would be pinned to a language they never picked, and changing their
 * browser's language afterwards would do nothing.
 *
 * Only a click persists. Everything else is a default that stays a default.
 */
export function setLocale(locale, { persist = false } = {}) {
  current = LOCALES.includes(locale) ? locale : DEFAULT_LOCALE;
  if (persist) {
    try {
      window.localStorage.setItem(LOCALE_KEY, current);
    } catch (error) {
      /* applies for this visit either way */
    }
  }
  // A screen reader pronounces the page according to this attribute, and
  // getting it wrong is worse than leaving it English: Portuguese read with
  // English phonemes is harder to follow than English is.
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.lang = current;
  }
  return current;
}

export function getLocale() {
  return current;
}

/**
 * One user-facing string.
 *
 * Falls back to English rather than to the key, and to the key rather than to
 * nothing, so the worst case a reader ever sees is an untranslated sentence
 * instead of `hidden.offTarget` or a blank.
 *
 * `params` substitutes `{name}` placeholders. Deliberately not a template
 * engine: a catalogue entry is a sentence with holes in it, and anything more
 * expressive would let logic move into the translations, where nobody tests it.
 */
/**
 * `t`, said in the singular when the count is one.
 *
 * A key may carry a `One` twin ('evp.entries' and 'evp.entriesOne'); when
 * `params.n` is 1 and the twin exists, the twin is used, so nobody reads
 * "1 entries" or "1 detalhes".
 */
export function tCount(key, params = null) {
  if (params && Number(params.n) === 1) {
    const catalogue = CATALOGUES[current] || EN;
    if (catalogue[`${key}One`] !== undefined || EN[`${key}One`] !== undefined) return t(`${key}One`, params);
  }
  return t(key, params);
}

export function t(key, params = null) {
  const catalogue = CATALOGUES[current] || EN;
  let text = catalogue[key];
  if (text === undefined) text = EN[key];
  if (text === undefined) return key;
  if (!params) return text;
  return String(text).replace(/\{(\w+)\}/g, (whole, name) =>
    (params[name] === undefined ? whole : String(params[name])));
}

/**
 * A stored enum's label, or the value itself when nothing is translated.
 *
 * The bridge between our internal vocabulary and a reader, and the reason the
 * two never merge: `VERIFIED_NOT_ELIGIBLE` stays exactly that in the database,
 * in the query string and in every test, and only what a person reads moves.
 */
export function tState(kind, value, fallback = null) {
  if (value === null || value === undefined || value === '') {
    return fallback === null ? t('absent.generic') : fallback;
  }
  const key = `${kind}.${String(value).toUpperCase()}`;
  const translated = t(key);
  return translated === key ? (fallback === null ? String(value) : fallback) : translated;
}

//: The state families a bare enum value could belong to, in the order they are
//: tried. Short and closed on purpose: a value that is in two of them would be
//: ambiguous, and a test asserts none is.
const STATE_FAMILIES = [
  'eligibility', 'band', 'seniority', 'status', 'work_model', 'contract', 'jobType',
  'period', 'region', 'access',
];

/**
 * A stored enum, translated, without the caller knowing which family it is in.
 *
 * `vocabLabel` receives bare values -- `SENIOR`, `STRONG`, `APPLIED` -- from
 * half a dozen call sites that have no reason to know whether they are holding
 * a level or a band. Searching the families keeps that ignorance intact, and
 * the families are disjoint so the search cannot pick the wrong one.
 *
 * Returns `null` when nothing is translated, which is how the caller tells
 * "no catalogue entry" apart from "translated to something short".
 */
export function tVocab(value) {
  if (value === null || value === undefined || value === '') return null;
  const upper = String(value).trim().toUpperCase();
  for (const family of STATE_FAMILIES) {
    const key = `${family}.${upper}`;
    const translated = t(key);
    if (translated !== key) return translated;
  }
  return null;
}

/** Every key the English catalogue defines. Used by the tests, not by the page. */
export function catalogueKeys(locale = DEFAULT_LOCALE) {
  return Object.keys(CATALOGUES[locale] || {});
}
