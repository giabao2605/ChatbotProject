from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class Result:
    row: Any = None
    rows: tuple[Any, ...] = ()
    scalar_value: Any = None
    rowcount: int = 0

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)

    def scalar(self):
        return self.scalar_value


class Connection:
    def __init__(self, outcomes: Iterable[Any] = ()):
        self._outcomes = deque(outcomes)
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        outcome = self._outcomes.popleft() if self._outcomes else Result()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Engine:
    def __init__(self, outcomes: Iterable[Any] = (), *, connect_error=None, begin_error=None):
        self.connection = Connection(outcomes)
        self._connect_error = connect_error
        self._begin_error = begin_error

    def connect(self):
        if self._connect_error is not None:
            raise self._connect_error
        return self.connection

    def begin(self):
        if self._begin_error is not None:
            raise self._begin_error
        return self.connection
