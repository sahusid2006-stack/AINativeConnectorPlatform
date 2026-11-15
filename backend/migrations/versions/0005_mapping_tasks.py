"""Frozen bounded mapping-task queue; not a general container-launch interface."""
from alembic import op
revision="0005"
down_revision="0004"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE mapping_tasks (
      id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), payload jsonb NOT NULL,
      result jsonb, status varchar NOT NULL DEFAULT 'queued', deadline timestamptz NOT NULL,
      lease_token uuid, lease_until timestamptz,
      CONSTRAINT mapping_task_status CHECK (status IN ('queued','running','completed','failed')));
      CREATE INDEX mapping_task_claim ON mapping_tasks(status,created_at);
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup instead")
