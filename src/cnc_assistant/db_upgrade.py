"""Minimal schema upgrade: add columns that exist on a model but not yet in the database.

db.create_all() creates missing tables but never changes existing ones. This fills the
common gap of "I added a new optional field to a model" without losing data. It only
ADDS nullable columns; renames, type changes and deletions still need a manual fix
(or deleting the database during development).
"""

from sqlalchemy import inspect, text

from .extensions import db


def add_missing_columns():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # create_all() handles brand-new tables
            existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_cols:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"Can't automatically add required column {table.name}.{column.name}. "
                        "Make it nullable, or delete the database to recreate it."
                    )
                col_type = column.type.compile(dialect=db.engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'))
                print(f"[db_upgrade] added column {table.name}.{column.name}")


# Old value -> new value, for columns whose allowed values changed.
LEGACY_VALUES = {
    # Material categories were renamed to match the rules engine's MaterialType.
    ("material", "category"): {"plastic": "acrylic", "wood": "wood_soft"},
}


def fix_legacy_values():
    with db.engine.begin() as conn:
        for (table, column), mapping in LEGACY_VALUES.items():
            for old, new in mapping.items():
                result = conn.execute(
                    text(f'UPDATE "{table}" SET "{column}" = :new WHERE "{column}" = :old'),
                    {"old": old, "new": new},
                )
                if result.rowcount:
                    print(f"[db_upgrade] {table}.{column}: {old!r} -> {new!r} ({result.rowcount} rows)")
