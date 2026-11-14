"""Frozen source intake metadata, proposal and selection evidence."""
from alembic import op
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE source_intake_metadata (
      source_id uuid PRIMARY KEY REFERENCES source_snapshots(id), original_digest varchar(64) NOT NULL,
      redacted boolean NOT NULL, requested_origin varchar NOT NULL, approved_origins jsonb NOT NULL);
    CREATE TABLE source_proposals (
      id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(), source_ids jsonb NOT NULL, document jsonb NOT NULL);
    CREATE TABLE source_selections (
      id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
      proposal_id uuid NOT NULL REFERENCES source_proposals(id), operation_ids jsonb NOT NULL);
    CREATE TRIGGER source_intake_guard BEFORE UPDATE OR DELETE ON source_intake_metadata
      FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
    CREATE TRIGGER source_proposal_guard BEFORE UPDATE OR DELETE ON source_proposals
      FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
    CREATE TRIGGER source_selection_guard BEFORE UPDATE OR DELETE ON source_selections
      FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup instead")
