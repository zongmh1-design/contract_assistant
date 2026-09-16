"""add LLM semantic rule evaluations

Revision ID: 8a7b6c5d4e3f
Revises: 6f1f2a3c4d5e
Create Date: 2026-09-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a7b6c5d4e3f"
down_revision: Union[str, Sequence[str], None] = "6f1f2a3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


old_match_mode = sa.Enum(
    "field_missing", "numeric_threshold", "keyword", "presence", "future_llm",
    name="review_rule_match_mode", native_enum=False, create_constraint=True,
)
new_match_mode = sa.Enum(
    "field_missing", "numeric_threshold", "keyword", "presence", "future_llm", "llm_semantic",
    name="review_rule_match_mode", native_enum=False, create_constraint=True,
)


def upgrade() -> None:
    with op.batch_alter_table("review_rules") as batch_op:
        batch_op.alter_column(
            "match_mode", existing_type=old_match_mode, type_=new_match_mode, nullable=False
        )
    op.create_table(
        "llm_rule_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("contract_parse_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=False),
        sa.Column("rule_version", sa.String(length=50), nullable=False),
        sa.Column("evaluation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=True),
        sa.Column("evaluation_status", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence_position", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("rule_hit_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["contract_parse_id"], ["contract_parses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rule_id"], ["review_rules.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rule_hit_id"], ["rule_hits.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_llm_rule_evaluations_contract_parse_id"), "llm_rule_evaluations", ["contract_parse_id"])
    op.create_index(op.f("ix_llm_rule_evaluations_rule_id"), "llm_rule_evaluations", ["rule_id"])
    op.create_index(op.f("ix_llm_rule_evaluations_rule_hit_id"), "llm_rule_evaluations", ["rule_hit_id"])
    op.create_index(op.f("ix_llm_rule_evaluations_evaluation_fingerprint"), "llm_rule_evaluations", ["evaluation_fingerprint"])


def downgrade() -> None:
    op.drop_index(op.f("ix_llm_rule_evaluations_evaluation_fingerprint"), table_name="llm_rule_evaluations")
    op.drop_index(op.f("ix_llm_rule_evaluations_rule_hit_id"), table_name="llm_rule_evaluations")
    op.drop_index(op.f("ix_llm_rule_evaluations_rule_id"), table_name="llm_rule_evaluations")
    op.drop_index(op.f("ix_llm_rule_evaluations_contract_parse_id"), table_name="llm_rule_evaluations")
    op.drop_table("llm_rule_evaluations")
    with op.batch_alter_table("review_rules") as batch_op:
        batch_op.alter_column(
            "match_mode", existing_type=new_match_mode, type_=old_match_mode, nullable=False
        )
