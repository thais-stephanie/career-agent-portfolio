# RemoteSource fixtures

Invented to the contract measured on 2026-09-11 (`docs/product/
source-leads-2026-09-11.md`, section 9), not captured: the real pages carry
real employers' postings, and a fixture that names one would be a fixture the
screenshot guard could not accept. Every employer here is made up.

* `sitemap.xml`: the index, seven shards.
* `shard-0.xml`: one shard, with the non-posting URLs the real shards carry
  beside the `/jobs/` ones, and one posting URL with no `-at-` employer key.
* `job-greenhouse.html`, `job-lever.html`, `job-workday.html`,
  `job-recruitee.html`, `job-workable.html`, `job-embedded.html`,
  `job-no-origin.html`: one page each, in the escaped Next.js payload shape
  the site serves (`\"applicationUrl\":\"...\"`, `\"sourceType\":\"ats\"`,
  `\"company\":{...}`).
