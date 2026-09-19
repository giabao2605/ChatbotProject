"""Durable session revocations in the application's existing SQL database."""

import hashlib

from sqlalchemy import BigInteger, Column, Integer, MetaData, String, Table, select


class SqlSessionRevocations:
    def __init__(self, engine):
        self.engine = engine
        self.table = Table(
            'AppSessionRevocations', MetaData(),
            Column('RevocationID', Integer, primary_key=True, autoincrement=True),
            Column('KeyHash', String(64), nullable=False, index=True),
            Column('ExpiresAt', BigInteger, nullable=False, index=True),
        )

    def create_schema(self):
        self.table.create(self.engine, checkfirst=True)

    @staticmethod
    def _hash(key):
        return hashlib.sha256(key.encode('utf-8')).hexdigest()

    def revoke(self, keys, expires_at, now):
        if expires_at <= now or not keys:
            return
        # Append-only writes avoid a read/update race between logout requests.
        # A shorter subsequent revocation never replaces a longer one.
        rows = [
            {'KeyHash': self._hash(key), 'ExpiresAt': int(expires_at)}
            for key in set(keys)
        ]
        with self.engine.begin() as connection:
            connection.execute(self.table.delete().where(self.table.c.ExpiresAt <= now))
            connection.execute(self.table.insert(), rows)

    def is_revoked(self, keys, now):
        if not keys:
            return False
        statement = select(self.table.c.RevocationID).where(
            self.table.c.KeyHash.in_([self._hash(key) for key in keys]),
            self.table.c.ExpiresAt > now,
        ).limit(1)
        with self.engine.connect() as connection:
            return connection.execute(statement).first() is not None
