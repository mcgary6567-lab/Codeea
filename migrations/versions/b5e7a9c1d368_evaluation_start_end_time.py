"""evaluation start and end time (the ERP's Evaluations columns)

Revision ID: b5e7a9c1d368
Revises: a1d3e5f7b246
Create Date: 2026-11-07 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5e7a9c1d368'
down_revision: Union[str, Sequence[str], None] = 'a1d3e5f7b246'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('evaluations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('start_time', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('end_time', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('evaluations', schema=None) as batch_op:
        batch_op.drop_column('end_time')
        batch_op.drop_column('start_time')
