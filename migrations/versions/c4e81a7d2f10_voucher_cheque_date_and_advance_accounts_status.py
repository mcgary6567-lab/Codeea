"""voucher cheque date and advance accounts status

Revision ID: c4e81a7d2f10
Revises: bf0d6ba94008
Create Date: 2026-10-28 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e81a7d2f10'
down_revision: Union[str, Sequence[str], None] = 'bf0d6ba94008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('journal_entries', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cheque_date', sa.Date(), nullable=True))

    with op.batch_alter_table('salary_advances', schema=None) as batch_op:
        batch_op.add_column(sa.Column('accounts_status', sa.String(length=20), nullable=False,
                                      server_default='pending'))
        batch_op.add_column(sa.Column('accounts_remarks', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('salary_advances', schema=None) as batch_op:
        batch_op.drop_column('accounts_remarks')
        batch_op.drop_column('accounts_status')

    with op.batch_alter_table('journal_entries', schema=None) as batch_op:
        batch_op.drop_column('cheque_date')
