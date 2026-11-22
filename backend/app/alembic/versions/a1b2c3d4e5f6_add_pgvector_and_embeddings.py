"""add pgvector and embeddings

Revision ID: a1b2c3d4e5f6
Revises: c5e2df51c992
Create Date: 2026-01-08 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'c5e2df51c992'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    # Add embedding column to telegram_message table using pgvector type
    # 768 dimensions for text-embedding-3-small
    op.execute("""
        ALTER TABLE telegram_message
        ADD COLUMN embedding vector(768)
    """)

    # Create HNSW index for fast similarity search
    # Using cosine distance operator (<=>)
    op.execute("""
        CREATE INDEX IF NOT EXISTS telegram_message_embedding_idx
        ON telegram_message
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # Add embedding metadata columns
    op.add_column(
        'telegram_message',
        sa.Column('embedding_generated_at', sa.DateTime(), nullable=True),
    )
    op.add_column(
        'telegram_message',
        sa.Column('embedding_model', sa.String(length=100), nullable=True),
    )

    # Create dedupe_group table for storing duplicate message groups
    op.create_table(
        'dedupe_group',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('primary_message_id', sa.Uuid(), nullable=False),
        sa.Column('confidence_score', sa.Float(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('group_size', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['primary_message_id'], ['telegram_message.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Create dedupe_group_member table for message-to-group relationships
    op.create_table(
        'dedupe_group_member',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('group_id', sa.Uuid(), nullable=False),
        sa.Column('message_id', sa.Uuid(), nullable=False),
        sa.Column('similarity_score', sa.Float(), nullable=False),
        sa.Column('explanation_text', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['group_id'], ['dedupe_group.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['telegram_message.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Add indexes for efficient querying
    op.create_index('idx_dedupe_group_primary_message', 'dedupe_group', ['primary_message_id'])
    op.create_index('idx_dedupe_group_member_group', 'dedupe_group_member', ['group_id'])
    op.create_index('idx_dedupe_group_member_message', 'dedupe_group_member', ['message_id'])

    # Add dedupe_group_id to telegram_message for quick filtering
    op.add_column(
        'telegram_message',
        sa.Column('dedupe_group_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_telegram_message_dedupe_group',
        'telegram_message',
        'dedupe_group',
        ['dedupe_group_id'],
        ['id'],
        ondelete='SET NULL'
    )
    op.create_index('idx_telegram_message_dedupe_group', 'telegram_message', ['dedupe_group_id'])


def downgrade() -> None:
    # Drop dedupe_group_id from telegram_message
    op.drop_index('idx_telegram_message_dedupe_group', table_name='telegram_message')
    op.drop_constraint('fk_telegram_message_dedupe_group', 'telegram_message', type_='foreignkey')
    op.drop_column('telegram_message', 'dedupe_group_id')

    # Drop dedupe tables
    op.drop_index('idx_dedupe_group_member_message', table_name='dedupe_group_member')
    op.drop_index('idx_dedupe_group_member_group', table_name='dedupe_group_member')
    op.drop_table('dedupe_group_member')

    op.drop_index('idx_dedupe_group_primary_message', table_name='dedupe_group')
    op.drop_table('dedupe_group')

    # Drop embedding columns
    op.drop_column('telegram_message', 'embedding_model')
    op.drop_column('telegram_message', 'embedding_generated_at')

    # Drop embedding index and column
    op.execute('DROP INDEX IF EXISTS telegram_message_embedding_idx')
    op.drop_column('telegram_message', 'embedding')

    # Note: We don't drop the pgvector extension as other tables might use it
    # op.execute('DROP EXTENSION IF EXISTS vector')
