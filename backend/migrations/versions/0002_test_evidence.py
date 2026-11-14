"""Retained independent reports are immutable after recording."""
from alembic import op

revision="0002"
down_revision="0001"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
    CREATE FUNCTION protect_test_report() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'Independent test reports are immutable';
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER test_report_guard BEFORE UPDATE OR DELETE ON test_runs
      FOR EACH ROW EXECUTE FUNCTION protect_test_report();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup instead")
