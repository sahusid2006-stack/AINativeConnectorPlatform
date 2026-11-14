"""Frozen owner/session and encrypted connection-secret storage."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    ALTER TABLE approvals ADD COLUMN policy_revision integer NOT NULL DEFAULT 1;
    CREATE TABLE owner_epoch (id integer PRIMARY KEY, fingerprint varchar(64) NOT NULL);
    CREATE TABLE owner_sessions (
      token_hash varchar(64) PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
      fingerprint varchar(64) NOT NULL, csrf_token varchar(64) NOT NULL, expires_at timestamptz NOT NULL);
    CREATE TABLE login_limits (id integer PRIMARY KEY, attempts integer NOT NULL, window_start timestamptz NOT NULL);
    CREATE TABLE connection_secrets (
      id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
      connection_id uuid NOT NULL UNIQUE REFERENCES connections(id), ciphertext bytea NOT NULL, revoked_at timestamptz);
    CREATE FUNCTION protect_approval_binding() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Approval evidence must be retained'; END IF;
      IF ROW(NEW.id, NEW.created_at, NEW.connection_id, NEW.version_id, NEW.operation, NEW.arguments_digest,
             NEW.actor, NEW.expires_at, NEW.policy_revision)
         IS DISTINCT FROM ROW(OLD.id, OLD.created_at, OLD.connection_id, OLD.version_id, OLD.operation, OLD.arguments_digest,
             OLD.actor, OLD.expires_at, OLD.policy_revision)
         OR (OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at)
         OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at) THEN
        RAISE EXCEPTION 'Approval binding and spent/revoked state are immutable';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER approval_guard BEFORE UPDATE OR DELETE ON approvals
      FOR EACH ROW EXECUTE FUNCTION protect_approval_binding();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup instead")
