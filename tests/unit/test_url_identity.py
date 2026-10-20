"""Rule 3 read once per site gives the answers the per-posting scan gave."""

from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from career_agent.pipeline.url_identity import OtherProviderUrls
from career_agent.providers.registry import canonical_posting_url

ROWS = [
    ("j1", "greenhouse", "https://acme.gupy.io/jobs/1?utm=x"),
    ("j2", "lever", "https://ACME.gupy.io/jobs/1/"),  # same posting, later row
    ("j3", "gupy", "https://acme.gupy.io/jobs/2"),  # the collector's own row
    ("j4", "ashby", "https://other.gupy.io/jobs/3"),
    ("j5", "ashby", "https://boards.example.com/x?next=acme.gupy.io/jobs/4"),
]


def _old(conn, provider, url):
    target = canonical_posting_url(url)
    host = urlsplit(url).hostname or ""
    if not target or not host:
        return None
    for job_id, stored in conn.execute(
        "SELECT id, url FROM job WHERE provider != ? AND url LIKE ?", (provider, f"%{host}%")
    ):
        if canonical_posting_url(stored) == target:
            return job_id
    return None


def test_the_same_job_as_the_scan_for_every_kind_of_url():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE job (id TEXT, provider TEXT, url TEXT)")
    conn.executemany("INSERT INTO job VALUES (?, ?, ?)", ROWS)
    urls = [
        "https://acme.gupy.io/jobs/1",  # held twice: the first row wins
        "https://acme.gupy.io/jobs/2",  # only the collector's own row: none
        "https://other.gupy.io/jobs/3",
        "https://acme.gupy.io/jobs/4",  # host only in another row's query
        "https://nobody.gupy.io/jobs/9",
        "",
        "not a url",
    ]
    index = OtherProviderUrls(conn, "gupy")
    assert [index.find(u) for u in urls] == [_old(conn, "gupy", u) for u in urls]
    assert index.find(urls[0]) == "j1" and index.find(urls[1]) is None


def test_one_read_per_site_however_many_postings():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE job (id TEXT, provider TEXT, url TEXT)")
    conn.executemany("INSERT INTO job VALUES (?, ?, ?)", ROWS)
    reads = []
    conn.set_trace_callback(lambda sql: reads.append(sql) if "FROM job" in sql else None)
    index = OtherProviderUrls(conn, "gupy")
    for n in range(50):
        index.find(f"https://acme.gupy.io/jobs/{n}")
        index.find(f"https://other.gupy.io/jobs/{n}")
    assert len(reads) == 1, "acme.gupy.io and other.gupy.io are one site"
