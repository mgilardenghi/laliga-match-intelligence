"""Declarative base and constraint naming convention for the LMI schema.

Every constraint gets a deterministic name derived from the table and the
columns, so a migration can drop or alter it by name on any database that was
created from the same metadata.
"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every ORM model of the LMI database."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
