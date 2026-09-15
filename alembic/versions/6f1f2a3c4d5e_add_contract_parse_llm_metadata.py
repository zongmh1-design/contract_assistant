"""add ContractParse LLM metadata

Revision ID: 6f1f2a3c4d5e
Revises: c22007620669
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6f1f2a3c4d5e"
down_revision: Union[str, Sequence[str], None] = "c22007620669"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contract_parses",
        sa.Column("llm_metadata_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contract_parses", "llm_metadata_json")
