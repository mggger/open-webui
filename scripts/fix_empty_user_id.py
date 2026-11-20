#!/usr/bin/env python3
"""Repair Open WebUI users whose primary key is an empty string ("").

A user row with id "" makes the JWT, chat ownership and profile image URLs carry
an empty id (e.g. /api/v1/users//profile/image) and leaves the desktop chat
panels stuck on a loading spinner. This script gives each such user a fresh UUID
and rewrites every column that references it.

Stop Open WebUI and back up the database first. Dry run by default:

    python scripts/fix_empty_user_id.py --db sqlite:///backend/data/webui.db
    python scripts/fix_empty_user_id.py --db sqlite:///backend/data/webui.db --apply
    python scripts/fix_empty_user_id.py --db postgresql://user:pass@host/db --apply

If --db is omitted, $DATABASE_URL is used. Afterwards, sign out and back in.
"""

import argparse
import os
import sys
import uuid

from sqlalchemy import create_engine, inspect, text

REF_COLUMNS = ('user_id', 'updated_by', 'actor_id')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.environ.get('DATABASE_URL'))
    parser.add_argument('--apply', action='store_true', help='write changes (default: dry run)')
    args = parser.parse_args()
    if not args.db:
        parser.error('pass --db or set DATABASE_URL')

    engine = create_engine(args.db)
    insp = inspect(engine)
    tables = insp.get_table_names()
    q = engine.dialect.identifier_preparer.quote

    with engine.begin() as conn:
        users = conn.execute(text(f'SELECT email, name FROM {q("user")} WHERE id = :e'), {'e': ''}).fetchall()
        if not users:
            print('No user with an empty id. Nothing to do.')
            return 0
        if len(users) > 1:
            print(f'Found {len(users)} users with an empty id; they cannot be told apart. Fix manually:')
            for u in users:
                print('  ', u.email, u.name)
            return 1

        new_id = str(uuid.uuid4())
        print(f'User {users[0].email} ({users[0].name}): "" -> {new_id}')

        plan = [('user', 'id'), ('auth', 'id')]
        for t in tables:
            cols = {c['name'] for c in insp.get_columns(t)}
            plan += [(t, c) for c in REF_COLUMNS if c in cols]

        for t, c in plan:
            if t not in tables:
                continue
            n = conn.execute(text(f'SELECT COUNT(*) FROM {q(t)} WHERE {q(c)} = :e'), {'e': ''}).scalar()
            if not n:
                continue
            print(f'  {t}.{c}: {n} row(s)')
            if args.apply:
                conn.execute(text(f'UPDATE {q(t)} SET {q(c)} = :n WHERE {q(c)} = :e'), {'n': new_id, 'e': ''})

        if not args.apply:
            conn.rollback()
            print('\nDry run only. Re-run with --apply to write these changes.')
        else:
            print('\nDone. Restart Open WebUI, then sign out and sign back in.')
    print('Note: user ids stored inside JSON columns (e.g. access_control) are not rewritten.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
