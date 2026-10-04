from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

from enterprise_context.config import get_settings


def check_database() -> bool:
    settings = get_settings()
    try:
        with psycopg.connect(
            settings.database_url, connect_timeout=2, row_factory=dict_row
        ) as connection:
            connection.execute("SELECT 1")
    except psycopg.Error:
        return False
    return True


def get_connection() -> Iterator[psycopg.Connection[dict[str, object]]]:
    settings = get_settings()
    with psycopg.connect(
        settings.database_url, connect_timeout=3, row_factory=dict_row
    ) as connection:
        yield connection
