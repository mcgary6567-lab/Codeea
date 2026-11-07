"""the ERP's Update Subscription form: status remarks, referred by, the books grid

Revision ID: a1d3e5f7b246
Revises: f3c5a7b9d124
Create Date: 2026-11-07 12:00:00.000000

"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1d3e5f7b246'
down_revision: Union[str, Sequence[str], None] = 'f3c5a7b9d124'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status_remarks', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('referred_by_client_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(batch_op.f('fk_subscriptions_referred_by_client_id_clients'), 'clients',
                                    ['referred_by_client_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'subscription_books',
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), nullable=False),
        sa.Column('starting_point', sa.String(length=100), nullable=True),
        sa.Column('open_sequence', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['book_id'], ['books.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('subscription_books', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_subscription_books_subscription_id'), ['subscription_id'], unique=False)

    # the books already chosen on each subscription (a JSON list of ids) become rows of the grid, in that order
    conn = op.get_bind()
    books = {r[0] for r in conn.execute(sa.text("SELECT id FROM books"))}
    now = datetime.utcnow()
    rows = []
    for sid, raw in conn.execute(sa.text("SELECT id, books FROM subscriptions")):
        ids = raw if isinstance(raw, list) else (__import__("json").loads(raw) if raw else [])
        for seq, bid in enumerate([int(b) for b in ids or [] if str(b).isdigit() and int(b) in books], start=1):
            rows.append({"subscription_id": sid, "book_id": bid, "open_sequence": seq, "status": "active",
                         "created_at": now, "updated_at": now})
    if rows:
        conn.execute(sa.text("INSERT INTO subscription_books (subscription_id, book_id, open_sequence, status, created_at, updated_at) "
                             "VALUES (:subscription_id, :book_id, :open_sequence, :status, :created_at, :updated_at)"), rows)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('subscription_books', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_subscription_books_subscription_id'))
    op.drop_table('subscription_books')
    with op.batch_alter_table('subscriptions', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_subscriptions_referred_by_client_id_clients'), type_='foreignkey')
        batch_op.drop_column('referred_by_client_id')
        batch_op.drop_column('status_remarks')
