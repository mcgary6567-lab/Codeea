"""exports for admins only: Yes (QA change request, 6 Nov 2026)

Revision ID: e2b4f6a8c013
Revises: d7a3c91e5b20
Create Date: 2026-11-06 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2b4f6a8c013'
down_revision: Union[str, Sequence[str], None] = 'd7a3c91e5b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))


def upgrade() -> None:
    """The college chose admins-only exports; the Branch Properties switch stays for the owner to change."""
    op.execute(settings.update().where(settings.c.key == 'exports_admin_only').values(value={"value": True}))


def downgrade() -> None:
    op.execute(settings.update().where(settings.c.key == 'exports_admin_only').values(value={"value": False}))
