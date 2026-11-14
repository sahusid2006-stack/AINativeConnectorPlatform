"""Durable evidence and lease primitives."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Frozen DDL: historical migrations must not import mutable current models.
    op.execute("\nCREATE TABLE audit_events (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tactor VARCHAR NOT NULL, \n\taction VARCHAR NOT NULL, \n\tresource_id UUID, \n\tdetails JSONB NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n;\n\nCREATE TABLE connectors (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tname VARCHAR NOT NULL, \n\tprotocol VARCHAR NOT NULL, \n\tstatus VARCHAR DEFAULT 'draft' NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n;\n\nCREATE TABLE delivery_deduplication (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tprovider VARCHAR NOT NULL, \n\tdelivery_id VARCHAR NOT NULL, \n\tconsumer_id UUID NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (provider, delivery_id, consumer_id)\n)\n\n;\n\nCREATE TABLE source_snapshots (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\torigin VARCHAR NOT NULL, \n\tmime_type VARCHAR NOT NULL, \n\tcontent BYTEA NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT source_size_limit CHECK (octet_length(content) <= 2097152)\n)\n\n;\n\nCREATE TABLE worker_heartbeats (\n\tworker VARCHAR NOT NULL, \n\tseen_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (worker)\n)\n\n;\n\nCREATE TABLE build_jobs (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tconnector_id UUID, \n\tkind VARCHAR NOT NULL, \n\tstatus VARCHAR DEFAULT 'queued' NOT NULL, \n\tpayload JSONB NOT NULL, \n\tdeadline TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlease_owner VARCHAR, \n\tlease_token UUID, \n\tlease_until TIMESTAMP WITH TIME ZONE, \n\twrite_intent BOOLEAN DEFAULT false NOT NULL, \n\tattempt_count INTEGER DEFAULT '0' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT job_kind CHECK (kind IN ('build', 'read', 'write')), \n\tCONSTRAINT job_status CHECK (status IN ('queued','reading_sources','generating','testing','running','ready_for_review','failed','blocked','outcome_unknown','completed')), \n\tFOREIGN KEY(connector_id) REFERENCES connectors (id)\n)\n\n;\nCREATE INDEX job_claim_index ON build_jobs (status, created_at);\n\nCREATE TABLE connections (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tconnector_id UUID NOT NULL, \n\tcredential_reference VARCHAR, \n\tresource_policy JSONB NOT NULL, \n\tmode VARCHAR NOT NULL, \n\tpolicy_revision INTEGER DEFAULT '1' NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT connection_mode CHECK (mode IN ('simulator', 'live')), \n\tFOREIGN KEY(connector_id) REFERENCES connectors (id)\n)\n\n;\n\nCREATE TABLE connector_versions (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tconnector_id UUID NOT NULL, \n\tpublic_major INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tstatus VARCHAR DEFAULT 'candidate' NOT NULL, \n\tartifact BYTEA NOT NULL, \n\tdigest VARCHAR(64) NOT NULL, \n\tmanifest JSONB NOT NULL, \n\tpublished_by VARCHAR, \n\tpublished_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tCONSTRAINT positive_version CHECK (public_major > 0 AND revision > 0), \n\tCONSTRAINT version_status CHECK (status IN ('candidate', 'published')), \n\tCONSTRAINT artifact_size_limit CHECK (octet_length(artifact) <= 2097152), \n\tUNIQUE (connector_id, public_major, revision), \n\tFOREIGN KEY(connector_id) REFERENCES connectors (id)\n)\n\n;\n\nCREATE TABLE approvals (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tconnection_id UUID NOT NULL, \n\tversion_id UUID NOT NULL, \n\toperation VARCHAR NOT NULL, \n\targuments_digest VARCHAR(64) NOT NULL, \n\tactor VARCHAR NOT NULL, \n\texpires_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tconsumed_at TIMESTAMP WITH TIME ZONE, \n\trevoked_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(connection_id) REFERENCES connections (id), \n\tFOREIGN KEY(version_id) REFERENCES connector_versions (id)\n)\n\n;\n\nCREATE TABLE build_attempts (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tjob_id UUID NOT NULL, \n\tattempt_number INTEGER NOT NULL, \n\tlease_token UUID NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\tevidence JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tfinished_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (id), \n\tUNIQUE (job_id, attempt_number), \n\tFOREIGN KEY(job_id) REFERENCES build_jobs (id)\n)\n\n;\n\nCREATE TABLE invocation_runs (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tversion_id UUID NOT NULL, \n\tstatus VARCHAR NOT NULL, \n\ttrace_id UUID NOT NULL, \n\tmetadata JSONB NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(version_id) REFERENCES connector_versions (id)\n)\n\n;\n\nCREATE TABLE operations (\n\tversion_id UUID NOT NULL, \n\tname VARCHAR NOT NULL, \n\tcontract JSONB NOT NULL, \n\tPRIMARY KEY (version_id, name), \n\tFOREIGN KEY(version_id) REFERENCES connector_versions (id)\n)\n\n;\n\nCREATE TABLE test_runs (\n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tversion_id UUID NOT NULL, \n\tverification_mode VARCHAR NOT NULL, \n\tpassed BOOLEAN NOT NULL, \n\treport JSONB NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT verification_mode CHECK (verification_mode IN ('simulator','live','replay')), \n\tFOREIGN KEY(version_id) REFERENCES connector_versions (id)\n)\n\n;\n\nCREATE TABLE version_references (\n\towner_kind VARCHAR NOT NULL, \n\towner_id UUID NOT NULL, \n\tversion_id UUID NOT NULL, \n\tPRIMARY KEY (owner_kind, owner_id, version_id), \n\tFOREIGN KEY(version_id) REFERENCES connector_versions (id)\n)\n\n;\n\nCREATE TABLE version_sources (\n\tversion_id UUID NOT NULL, \n\tsource_id UUID NOT NULL, \n\tPRIMARY KEY (version_id, source_id), \n\tFOREIGN KEY(version_id) REFERENCES connector_versions (id), \n\tFOREIGN KEY(source_id) REFERENCES source_snapshots (id)\n)\n\n;\n")
    op.execute("""
    CREATE FUNCTION protect_published_version() RETURNS trigger AS $$
    BEGIN
      IF OLD.status = 'published' THEN
        RAISE EXCEPTION 'Published versions are immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER published_version_guard BEFORE UPDATE OR DELETE ON connector_versions
      FOR EACH ROW EXECUTE FUNCTION protect_published_version();
    CREATE FUNCTION protect_version_evidence() RETURNS trigger AS $$
    DECLARE v_id uuid;
    BEGIN
      IF TG_OP = 'INSERT' THEN v_id := NEW.version_id; ELSE v_id := OLD.version_id; END IF;
      IF EXISTS (SELECT 1 FROM connector_versions WHERE id = v_id AND status = 'published') THEN
        RAISE EXCEPTION 'Published version evidence is immutable';
      END IF;
      IF TG_OP = 'UPDATE' AND EXISTS (SELECT 1 FROM connector_versions WHERE id = NEW.version_id AND status = 'published') THEN
        RAISE EXCEPTION 'Published version evidence is immutable';
      END IF;
      IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER operation_evidence_guard BEFORE INSERT OR UPDATE OR DELETE ON operations
      FOR EACH ROW EXECUTE FUNCTION protect_version_evidence();
    CREATE TRIGGER source_evidence_guard BEFORE INSERT OR UPDATE OR DELETE ON version_sources
      FOR EACH ROW EXECUTE FUNCTION protect_version_evidence();
    CREATE FUNCTION protect_snapshot() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'Source snapshots are immutable';
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER snapshot_guard BEFORE UPDATE ON source_snapshots
      FOR EACH ROW EXECUTE FUNCTION protect_snapshot();
    CREATE FUNCTION protect_audit() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'Audit evidence is append only';
    END;
    $$ LANGUAGE plpgsql;
    CREATE TRIGGER audit_guard BEFORE UPDATE OR DELETE ON audit_events
      FOR EACH ROW EXECUTE FUNCTION protect_audit();
    """)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore an approved backup instead")
