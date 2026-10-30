"""client contacts open with a one-time password

Revision ID: d7a3c91e5b20
Revises: c4e81a7d2f10
Create Date: 2026-10-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a3c91e5b20'
down_revision: Union[str, Sequence[str], None] = 'c4e81a7d2f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('otp_configurations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reveal_window_minutes', sa.Integer(), nullable=False, server_default='0'))

    op.create_table(
        'contact_reveal_challenges',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('field', sa.String(length=30), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('channel', sa.String(length=20), nullable=False),
        sa.Column('sent_to', sa.String(length=200), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('contact_reveal_challenges', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contact_reveal_challenges_client_id'), ['client_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_contact_reveal_challenges_user_id'), ['user_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('contact_reveal_challenges', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contact_reveal_challenges_user_id'))
        batch_op.drop_index(batch_op.f('ix_contact_reveal_challenges_client_id'))
    op.drop_table('contact_reveal_challenges')

    with op.batch_alter_table('otp_configurations', schema=None) as batch_op:
        batch_op.drop_column('reveal_window_minutes')
