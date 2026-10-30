"""Assistant-owned notes, separate from user memory; no network or chat sending."""

from datetime import datetime
from pathlib import Path
import sqlite3
import time
import uuid

KINDS = {"chat", "afterthought", "wait_for_user", "self_task"}
ACTIVE = {"scheduled", "reviewing", "waiting", "uncertain"}


def parse_due(value, now):
    if not isinstance(value, str):
        raise ValueError("due_at must be an ISO timestamp with timezone")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("Timezone required")
        result = date.timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid due_at with timezone") from exc
    if not now + 1 <= result <= now + 366 * 86400:
        raise ValueError("due_at must be future and within one year")
    return result


class ReminderStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run(
            lambda db: db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,session TEXT,title TEXT,kind TEXT,basis TEXT,due_at REAL,expires_at REAL,cancel_on_reply INTEGER,created REAL,updated REAL,revision INTEGER,state TEXT,token TEXT,lease_until REAL,outcome TEXT);
        CREATE TABLE IF NOT EXISTS wakes(token TEXT PRIMARY KEY,note_id TEXT,revision INTEGER,event_id TEXT,task_id TEXT,state TEXT,created REAL,receipt TEXT);
        CREATE TABLE IF NOT EXISTS observed(session TEXT,message_id TEXT,at REAL,PRIMARY KEY(session,message_id));
        CREATE TABLE IF NOT EXISTS user_state(session TEXT PRIMARY KEY,last_user_at REAL);
        CREATE TABLE IF NOT EXISTS budgets(day TEXT PRIMARY KEY,used INTEGER);
        CREATE TABLE IF NOT EXISTS request_bindings(session TEXT,anchor TEXT,token TEXT,created REAL,PRIMARY KEY(session,anchor));
        """)
        )

    def run(self, fn):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        self.path.chmod(0o600)
        try:
            with db:
                return fn(db)
        finally:
            db.close()

    @staticmethod
    def expiry(kind, due):
        return due + (7 * 86400 if kind == "self_task" else 6 * 3600)

    def create(
        self,
        session,
        title,
        kind,
        due_at,
        basis,
        now,
        *,
        cancel_on_reply=False,
        max_active=50,
    ):
        if (
            not session
            or kind not in KINDS
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 160
        ):
            raise ValueError("Invalid reminder identity/title/kind")
        if not now + 1 <= due_at <= now + 366 * 86400:
            raise ValueError("Reminder time outside future range")
        if not isinstance(basis, str) or len(basis) > 400:
            raise ValueError("Basis exceeds400characters")

        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            same = db.execute(
                "SELECT * FROM notes WHERE session=? AND title=? AND kind=? AND state='scheduled' AND ABS(due_at-?)<60",
                (session, title.strip(), kind, due_at),
            ).fetchone()
            if same:
                return dict(same)
            count = db.execute(
                "SELECT COUNT(*) FROM notes WHERE session=? AND state IN ('scheduled','reviewing','waiting','uncertain')",
                (session,),
            ).fetchone()[0]
            if count >= max_active:
                raise ValueError("Too many open reminders")
            ident = uuid.uuid4().hex
            db.execute(
                "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ident,
                    session,
                    title.strip(),
                    kind,
                    basis,
                    due_at,
                    self.expiry(kind, due_at),
                    int(cancel_on_reply),
                    now,
                    now,
                    1,
                    "scheduled",
                    "",
                    0,
                    "",
                ),
            )
            return dict(
                db.execute("SELECT * FROM notes WHERE id=?", (ident,)).fetchone()
            )

        return self.run(tx)

    def list(self, session, limit=60):
        return self.run(
            lambda db: [
                dict(x)
                for x in db.execute(
                    "SELECT * FROM notes WHERE session=? AND state!='deleted' ORDER BY updated DESC LIMIT ?",
                    (session, min(100, max(1, limit))),
                )
            ]
        )

    def change(self, session, ident, action, values, now):
        if action not in {"update", "complete", "cancel", "delete"}:
            raise ValueError("Unknown action")

        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM notes WHERE id=? AND session=? AND state!='deleted'",
                (ident, session),
            ).fetchone()
            if not row:
                raise ValueError("Unknown reminder for this session")
            item = dict(row)
            if action == "update":
                if set(values) - {"title", "kind", "due_at", "cancel_on_reply"}:
                    raise ValueError("Immutable/unknown reminder fields")
                item.update(values)
                if (
                    item["kind"] not in KINDS
                    or not isinstance(item["title"], str)
                    or not item["title"].strip()
                    or len(item["title"]) > 160
                ):
                    raise ValueError("Invalid title/kind")
                if (
                    "due_at" in values
                    and not now + 1 <= item["due_at"] <= now + 366 * 86400
                ):
                    raise ValueError("New due_at must be future")
                state = "scheduled"
            else:
                state = {
                    "complete": "done",
                    "cancel": "cancelled",
                    "delete": "deleted",
                }[action]
            db.execute(
                "UPDATE notes SET title=?,kind=?,due_at=?,expires_at=?,cancel_on_reply=?,state=?,revision=revision+1,token='',lease_until=0,updated=? WHERE id=?",
                (
                    item["title"].strip(),
                    item["kind"],
                    item["due_at"],
                    self.expiry(item["kind"], item["due_at"]),
                    int(item["cancel_on_reply"]),
                    state,
                    now,
                    ident,
                ),
            )
            return dict(
                db.execute("SELECT * FROM notes WHERE id=?", (ident,)).fetchone()
            )

        return self.run(tx)

    def observe(self, session, message_id, at):
        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "INSERT OR IGNORE INTO observed VALUES(?,?,?)",
                (session, message_id, at),
            ).rowcount:
                return
            last = db.execute(
                "SELECT last_user_at FROM user_state WHERE session=?", (session,)
            ).fetchone()
            if last and at <= last[0]:
                return
            db.execute(
                "INSERT INTO user_state VALUES(?,?) ON CONFLICT(session) DO UPDATE SET last_user_at=excluded.last_user_at",
                (session, at),
            )
            db.execute(
                "UPDATE notes SET state='cancelled',revision=revision+1,token='',updated=? WHERE session=? AND cancel_on_reply=1 AND created<? AND state IN ('scheduled','reviewing','waiting','uncertain','handled')",
                (at, session, at),
            )
            db.execute(
                "UPDATE notes SET state='scheduled',due_at=?,revision=revision+1,token='',updated=? WHERE session=? AND state='waiting' AND expires_at>?",
                (at + 1800, at, session, at),
            )
            db.execute("DELETE FROM observed WHERE at<?", (at - 7 * 86400,))

        self.run(tx)

    def claim(self, now, maximum, day, sessions=None):
        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE notes SET state='uncertain',outcome='interrupted_review' WHERE state='reviewing' AND lease_until<?",
                (now,),
            )
            db.execute(
                "UPDATE notes SET state='expired',revision=revision+1 WHERE state IN ('scheduled','waiting','uncertain') AND expires_at<=?",
                (now,),
            )
            db.execute(
                "DELETE FROM notes WHERE state IN ('done','cancelled','deleted','expired','handled') AND updated<?",
                (now - 30 * 86400,),
            )
            used = db.execute("SELECT used FROM budgets WHERE day=?", (day,)).fetchone()
            if maximum <= 0 or (used and used[0] >= maximum):
                return None
            rows = db.execute(
                "SELECT * FROM notes WHERE state='scheduled' AND due_at<=? AND expires_at>? ORDER BY due_at LIMIT 50",
                (now, now),
            ).fetchall()
            row = next(
                (x for x in rows if sessions is None or x["session"] in sessions), None
            )
            if row is None:
                return None
            item = dict(row)
            token = uuid.uuid4().hex
            event = uuid.uuid4().hex
            db.execute(
                "UPDATE notes SET state='reviewing',token=?,lease_until=?,updated=? WHERE id=?",
                (token, now + 180, now, item["id"]),
            )
            db.execute(
                "INSERT INTO wakes VALUES(?,?,?,?,?,?,?,?)",
                (token, item["id"], item["revision"], event, "", "reviewing", now, ""),
            )
            db.execute(
                "INSERT INTO budgets VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1",
                (day,),
            )
            db.execute("DELETE FROM budgets WHERE day<?", (day,))
            db.execute("DELETE FROM wakes WHERE created<?", (now - 7 * 86400,))
            db.execute("DELETE FROM request_bindings WHERE created<?", (now - 86400,))
            item.update(token=token, event_id=event, state="reviewing")
            return item

        return self.run(tx)

    def current(self, item):
        return self.run(
            lambda db: bool(
                db.execute(
                    "SELECT 1 FROM notes WHERE id=? AND revision=? AND token=? AND state='reviewing'",
                    (item["id"], item["revision"], item["token"]),
                ).fetchone()
            )
        )

    def finish(self, item, state, due, outcome, now):
        if state not in {"done", "scheduled", "waiting", "uncertain", "handled"}:
            raise ValueError("Invalid finish state")
        if state == "scheduled" and (
            due is None or not now + 1 <= due <= now + 366 * 86400
        ):
            raise ValueError("Future reconsideration required")

        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM notes WHERE id=? AND revision=? AND token=? AND state='reviewing'",
                (item["id"], item["revision"], item["token"]),
            ).fetchone():
                return False
            final_outcome = outcome
            if (
                state == "handled"
                and db.execute(
                    "SELECT 1 FROM wakes WHERE token=? AND state='sent'",
                    (item["token"],),
                ).fetchone()
            ):
                final_outcome = "sent_with_platform_receipt"
            next_due = due if due is not None else item["due_at"]
            expiry = (
                self.expiry(item["kind"], next_due)
                if due is not None
                else item["expires_at"]
            )
            db.execute(
                "UPDATE notes SET state=?,due_at=?,expires_at=?,outcome=?,lease_until=0,updated=? WHERE id=?",
                (state, next_due, expiry, str(final_outcome)[:200], now, item["id"]),
            )
            if state not in {"handled", "uncertain"}:
                db.execute(
                    "UPDATE wakes SET state='reviewed' WHERE token=?", (item["token"],)
                )
            return True

        return self.run(tx)

    def begin_contact(self, item):
        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM notes WHERE id=? AND revision=? AND token=? AND state='reviewing'",
                (item["id"], item["revision"], item["token"]),
            ).fetchone():
                return False
            db.execute(
                "UPDATE wakes SET state='queued' WHERE token=?", (item["token"],)
            )
            return True

        return self.run(tx)

    def bind_request(self, session, anchor, task_id="", event_id=""):
        def tx(db):
            db.execute(
                "DELETE FROM request_bindings WHERE session=? AND anchor=?",
                (session, anchor),
            )
            if event_id:
                row = db.execute(
                    "SELECT w.token FROM wakes w JOIN notes n ON n.id=w.note_id WHERE w.event_id=? AND n.session=?",
                    (event_id, session),
                ).fetchone()
                if row:
                    db.execute(
                        "UPDATE wakes SET task_id=? WHERE token=?", (task_id, row[0])
                    )
                    db.execute(
                        "INSERT INTO request_bindings VALUES(?,?,?,?)",
                        (session, anchor, row[0], time.time()),
                    )
            elif task_id:
                row = db.execute(
                    "SELECT w.token FROM wakes w JOIN notes n ON n.id=w.note_id WHERE w.task_id=? AND n.session=?",
                    (task_id, session),
                ).fetchone()
                if row:
                    db.execute(
                        "INSERT INTO request_bindings VALUES(?,?,?,?)",
                        (session, anchor, row[0], time.time()),
                    )

        self.run(tx)

    def task_token(self, session, task_id):
        return self.run(
            lambda db: (
                r[0]
                if (
                    r := db.execute(
                        "SELECT w.token FROM wakes w JOIN notes n ON n.id=w.note_id WHERE w.task_id=? AND n.session=?",
                        (task_id, session),
                    ).fetchone()
                )
                else ""
            )
        )

    def bound(self, session, anchor):
        return self.run(
            lambda db: (
                dict(r)
                if (
                    r := db.execute(
                        "SELECT * FROM request_bindings WHERE session=? AND anchor=?",
                        (session, anchor),
                    ).fetchone()
                )
                else {}
            )
        )

    def can_send(self, token, session, now):
        return self.run(
            lambda db: bool(
                db.execute(
                    "SELECT 1 FROM wakes w JOIN notes n ON n.id=w.note_id AND n.revision=w.revision AND n.token=w.token WHERE w.token=? AND n.session=? AND w.state IN ('queued','sent') AND n.state IN ('reviewing','handled','uncertain') AND w.created>? AND NOT EXISTS(SELECT 1 FROM user_state u WHERE u.session=n.session AND u.last_user_at>w.created)",
                    (token, session, now - 600),
                ).fetchone()
            )
        )

    def receipt(self, token, receipt):
        if not receipt:
            return

        def tx(db):
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE wakes SET state='sent',receipt=? WHERE token=? AND state IN ('queued','sent')",
                (str(receipt)[:200], token),
            ).rowcount
            if changed:
                db.execute(
                    "UPDATE notes SET outcome='sent_with_platform_receipt' WHERE token=? AND state IN ('reviewing','handled','uncertain')",
                    (token,),
                )

        self.run(tx)
