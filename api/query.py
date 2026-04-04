from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from api.dependencies import AuthContext


@dataclass
class SqlWhereBuilder:
    clauses: list[str] = field(default_factory=list)
    params: list[Any] = field(default_factory=list)

    def add(self, clause: str, *params: Any, enabled: bool = True) -> "SqlWhereBuilder":
        if enabled and clause:
            self.clauses.append(clause)
            self.params.extend(params)
        return self

    def add_many(self, clauses: Iterable[tuple[str, list[Any] | tuple[Any, ...]]]) -> "SqlWhereBuilder":
        for clause, params in clauses:
            self.add(clause, *params)
        return self

    def add_auth(self, auth: AuthContext, col: str = "api_key_hash") -> "SqlWhereBuilder":
        clause, params = auth.where_clause(col)
        if clause:
            self.add(clause, *params)
        return self

    def add_date_range(
        self,
        *,
        column: str = "timestamp",
        date_from: str | None = None,
        date_to: str | None = None,
        cast_to_date: bool = False,
    ) -> "SqlWhereBuilder":
        target = f"date({column})" if cast_to_date else column
        if date_from:
            self.add(f"{target} >= ?", date_from)
        if date_to:
            self.add(f"{target} <= ?", date_to if cast_to_date else f"{date_to}T23:59:59")
        return self

    @property
    def where_sql(self) -> str:
        if not self.clauses:
            return ""
        return "WHERE " + " AND ".join(self.clauses)

    @property
    def and_sql(self) -> str:
        if not self.clauses:
            return ""
        return "AND " + " AND ".join(self.clauses)


def validate_sort(value: str, *, allowed: set[str], default: str) -> str:
    return value if value in allowed else default


def validate_order(value: str) -> str:
    return "DESC" if value.lower() == "desc" else "ASC"


def pagination_offset(page: int, page_size: int) -> int:
    safe_page = max(page, 1)
    safe_page_size = max(page_size, 1)
    return (safe_page - 1) * safe_page_size
