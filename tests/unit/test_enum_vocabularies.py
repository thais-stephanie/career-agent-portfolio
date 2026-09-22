"""A Python enum and a CHECK constraint are two spellings of one vocabulary.

Nothing tied them together, and it cost a live call.

`StructuredOutput.JSON_OBJECT` was added to the enum, sent on the wire, folded
into `static_digest`, and tested across four adapters and the CLI. Then a real
request went out, a vendor answered, and the insert that would have made the
answer durable was refused:

    IntegrityError: CHECK constraint failed:
        structured_output IN ('STRICT_SCHEMA', 'PLAIN_JSON', 'UNRECORDED')

The save-first lifecycle behaved exactly as designed -- it wrote at the moment
of observation. The write was rejected by a vocabulary nobody had updated, and
the evidence for a spent call was lost.

Three columns duplicate a Python enum in SQL: `runner`, `structured_output` and
`transport`. Each is a place the two can drift apart in silence until a live
call pays for it. These tests insert every member of every one of them, so a
future member cannot reach a vendor before it can reach the table.
"""

from __future__ import annotations

import sqlite3

import pytest

from career_agent.llm.client import Runner, StructuredOutput
from career_agent.llm.failures import Transport
from career_agent.storage.db import connect, migrate

#: The historical value every one of these columns defaults to. Not a member of
#: any enum, and deliberately so: it means "nobody recorded this", which is a
#: statement about our own history rather than about a vendor.
UNRECORDED = "UNRECORDED"


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "vocabulary.db")
    migrate(connection)
    yield connection
    connection.close()


def insert(connection: sqlite3.Connection, **overrides: object) -> None:
    row = {
        "id": "row",
        "job_id": None,
        "cache_key": "sha256:k",
        "execution_id": "exec",
        "family": "description",
        "purpose": "fingerprint",
        "provider": "google",
        "model": "m",
        "prompt_version": "description_v6",
        "schema_version": 3,
        "structured_output": UNRECORDED,
        "input_hash": "sha256:k",
        "raw_output": "{}",
        "parsed_ok": 1,
        "validated_ok": 1,
        "attempt": 1,
        "runner": UNRECORDED,
        "transport": UNRECORDED,
        "created_at": "2026-09-03T00:00:00Z",
    }
    row.update(overrides)
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    connection.execute(
        f"INSERT INTO llm_call ({columns}) VALUES ({placeholders})", tuple(row.values())
    )


@pytest.mark.parametrize("mode", list(StructuredOutput))
def test_every_structured_output_mode_can_be_stored(conn, mode: StructuredOutput) -> None:
    """The regression, member by member.

    A mode the wire accepts and the table refuses is a spent call with no row.
    """
    insert(conn, id=f"row-{mode.value}", structured_output=mode.value)
    stored = conn.execute(
        "SELECT structured_output FROM llm_call WHERE id = ?", (f"row-{mode.value}",)
    ).fetchone()
    assert stored[0] == mode.value


@pytest.mark.parametrize("runner", list(Runner))
def test_every_runner_can_be_stored(conn, runner: Runner) -> None:
    insert(conn, id=f"row-{runner.value}", runner=runner.value)


@pytest.mark.parametrize("transport", list(Transport))
def test_every_transport_state_can_be_stored(conn, transport: Transport) -> None:
    insert(conn, id=f"row-{transport.value}", transport=transport.value)


@pytest.mark.parametrize("column", ["structured_output", "runner", "transport"])
def test_the_historical_default_is_still_accepted(conn, column: str) -> None:
    """UNRECORDED is not an enum member and must remain storable.

    It means "nobody recorded this", which is a fact about our own history and
    the honest state for every row written before the column existed.
    """
    insert(conn, id=f"row-{column}", **{column: UNRECORDED})


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("structured_output", "GUESSED"),
        ("runner", "PROBABLY_PRODUCTION"),
        ("transport", "MAYBE_SENT"),
    ],
)
def test_a_value_no_enum_defines_is_still_refused(conn, column: str, value: str) -> None:
    """The constraint has to keep constraining.

    Widening a vocabulary means adding the member that exists, not removing the
    check that catches the one that does not.
    """
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, id=f"row-{value}", **{column: value})
