from sqlalchemy import select, update
from connector_studio import models as m
from connector_studio.access import AccessError
from connector_studio.policy import Permit, DriverFailure
from connector_studio.redaction import redacted
from tests.security_support import SecurityBase, FakeDriver, SECRET, OWNER, POLICY, RESOURCES


class ConnectionTests(SecurityBase):
    def test_ciphertext_and_references_not_plaintext_are_persisted(self):
        connection, version, operation = self.connection()
        permit = Permit(connection,version,operation,1,False)
        with self.engine.connect() as conn:
            ciphertext = conn.scalar(select(m.connection_secrets.c.ciphertext).where(m.connection_secrets.c.connection_id==connection))
            reference = conn.scalar(select(m.connections.c.credential_reference).where(m.connections.c.id==connection))
        self.assertNotIn(SECRET.encode(),ciphertext)
        self.assertTrue(reference.startswith("secret://"))
        self.assertEqual(self.security.resolve(permit),SECRET)
        self.assertNotIn(SECRET,str(redacted({"diagnostic":SECRET,"client_secret":SECRET})))
        self.assertNotIn(OWNER,str(redacted({"diagnostic":OWNER})))

    def test_rotation_and_revocation_use_current_reference(self):
        connection,version,operation = self.connection()
        old = Permit(connection,version,operation,1,False)
        self.security.change(connection,credential="new-synthetic-provider-secret-123456789012345")
        with self.assertRaises(AccessError): self.security.resolve(old)
        new = Permit(connection,version,operation,2,False)
        self.assertEqual(self.security.resolve(new),"new-synthetic-provider-secret-123456789012345")
        self.security.change(connection,revoke=True)
        with self.assertRaises(AccessError): self.security.resolve(Permit(connection,version,operation,3,False))

    def test_ciphertext_tampering_and_cross_connection_substitution_fail_closed(self):
        first,version,operation = self.connection()
        second,_,_ = self.connection()
        with self.engine.begin() as conn:
            ciphertext = conn.scalar(select(m.connection_secrets.c.ciphertext).where(m.connection_secrets.c.connection_id==first))
            conn.execute(update(m.connection_secrets).where(m.connection_secrets.c.connection_id==second).values(ciphertext=ciphertext))
        with self.assertRaises(AccessError): self.security.resolve(Permit(second,version,operation,1,False))

    def test_connection_failures_classify_without_rewrite_or_sensitive_messages(self):
        connection,_,_ = self.connection()
        for category in ("authentication","permission","unreachable","unsupported_auth"):
            result = self.security.test(FakeDriver(failure=DriverFailure(category)),connection)
            self.assertEqual(result["category"],category)
            self.assertFalse(result["rewrite_adapter"])
            self.assertIn("next_action",result)
            self.assertNotIn(SECRET,str(result))

    def test_denied_connection_test_never_contacts_provider(self):
        connection,_,_ = self.connection()
        driver = FakeDriver(resources={**RESOURCES,"destination":"http://169.254.169.254"})
        with self.assertRaises(AccessError): self.security.test(driver,connection)
        self.assertEqual(driver.calls,0)

    def test_successful_diagnostics_are_redacted(self):
        connection,_,_ = self.connection()
        result = self.security.test(FakeDriver(),connection)
        self.assertEqual(result["category"],"success")
        self.assertNotIn(SECRET,str(result))
