# Comeet fixtures

`positions.json`: CAPTURED live 2026-09-11 from the Careers API 2.0 behind
TripleTen's hosted board (`www.comeet.com/jobs/tripleten/98.008`), trimmed to
four vacancies of the 47 and eleven rows of the 335, with the adverts cut to
a few hundred characters and every `token=` query parameter removed. The
rows are the vendor's own shape: a base uid with one location and one row per
further location, `workplace_type`, `employment_type`, `details` sections on
the base row.

* `62.F64`, four rows: Spain, Serbia, Portugal, Poland. The vacancy the
  research followed across five surfaces; Brazil is not among its locations.
* `5E.A6A`, four of its ten rows kept: Brazil on the BASE row, then Bolivia,
  Panama, Argentina.
* `C3.F61`, one row: a single-location vacancy.
* `63.46B`, two rows: `On-site` on the base row, a Brazil variant.

`hosted-page.html`: INVENTED to the measured shape of the hosted page, with a
made-up token. No real token is committed anywhere in this repository.
