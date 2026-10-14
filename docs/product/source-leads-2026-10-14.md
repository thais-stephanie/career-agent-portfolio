# Five source leads, classified with evidence (2026-10-14)

Policy applied (ADR-0018): for collection, the absence of a prohibition is
permission under five limits; an explicit prohibition in terms, or a robots
rule for our agent or path, is respected; a technical block is never
circumvented; an unreadable robots file refuses. Each line below is what was
read on 2026-10-14 with a handful of bounded requests from this machine. No
posting was stored and the catalogue was not changed.

Quotes are short excerpts of the cited page.

## 1. Explicit prohibition in the terms

**Remotar** (`remotar.com.br`). `https://remotar.com.br/termos-de-uso`:
copies by "tecnologias de buscador tipo Robot/Crawler" are "expressamente
proibido". Its robots file is absent (`/robots.txt` answers 404), which on
its own would be silence; the terms decide. Not usable.

## 2. Robots restriction by agent or by path

**JobFluent** (`www.jobfluent.com`). `https://www.jobfluent.com/robots.txt`:
`User-agent: ClaudeBot` and `User-agent: Claude-User` with `Disallow: /`;
for everyone, `Disallow: /api/*`. The terms (`/terms`, `/legal`) were read
for a crawling clause and none was found. So: this agent reads nothing past
robots, and the only structured endpoint is closed to every reader. A
collector under its own name could read public pages; whether those pages
are a usable feed was not measured.

**Gupy** (per company). `https://<company>.gupy.io/robots.txt` (read for
`boticario`): `Allow: /`, `Disallow: /companies`, `Disallow: /candidates`,
and a `Sitemap:` line pointing at the company's feed. The feed host itself
(`job-boards.api.gupy.io/robots.txt`) answers 403. See 4.

## 3. Technical block

**EU Remote Jobs** (`euremotejobs.com`). Its robots file allows everyone
except four named crawlers (not ours) and some search parameters. The home
page and `sitemap.rss` both answered **403 Forbidden** to this client. Not
circumvented; not usable from here.

**InHire** (per company). `https://ecx.inhire.app/robots.txt` today has only
`User-agent: *` and `Disallow: /indique-uma-pessoa/`; the `ClaudeBot`
disallow recorded in the catalogue on 2026-09-11 is gone (the catalogue row
is stale and should be re-read before any decision). But the jobs page
`https://ecx.inhire.app/vagas` is a JavaScript shell ("This website need
Javascript"), with no structured data and no sitemap (`/sitemap.xml` serves
the same shell). The postings come from `api.inhire.app`, whose robots file
answers **403** (unreadable, which refuses), and the documented API at
`docs.inhire.com.br` is a customer API (JWT plus `X-Tenant`). No usable
public access was found.

## 4. Usable public access

**Gupy, per company, with limits.** The feed named in a company's robots
file (`job-boards.api.gupy.io/production/job-board-content?jobBoardName=google&subdomain=<company>`)
redirects to an XML sitemap of that company's posting pages. Four companies
tried: `ambev` 271 posting URLs, `localiza` a 156 KB sitemap; `boticario`
redirects to a file that does not exist (403 from storage); `magazineluiza`
has no Gupy site (404). One posting page (`ambev`) carried a schema.org
`JobPosting` (HTML-escaped) and the page data held the whole advert
(description, responsibilities, experience) with a `workplaceType`.

Limits: the sitemap holds URLs, not adverts, so each posting costs one
request; there is no list of companies, so each must be found from evidence
(an employer's own careers link); not every company has a feed; and Gupy's
terms of use were not found at the obvious addresses (`/termos-de-uso`
answers 404), so the terms are unverified. This is the old portal-HTML
question in a new form, and an owner decision.

## What changes in the 2026-10-13 recommendation

* InHire is withdrawn: its robots file is no longer the obstacle, but no
  public access was found.
* Gupy per company stays, with the limits above and the terms unverified.
* Widening the existing ATS families (Greenhouse, Lever, Ashby, Workable)
  to European employers that hire worldwide or in Latin America needs no new
  source and stays.
