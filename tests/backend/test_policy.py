from concurrent.futures import ThreadPoolExecutor
import time
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from connector_studio import models as m
from connector_studio.access import AccessError
from connector_studio.policy import check_resources, arguments_digest
from tests.security_support import SecurityBase, FakeDriver, SECRET, POLICY, RESOURCES


class PolicyTests(SecurityBase):
    def test_read_requires_approved_actual_resources_before_provider_contact(self):
        connection,version,operation = self.connection()
        for resources in ({**RESOURCES,"repository":"other/repo"}, {**RESOURCES,"destination":"http://other:8000"}, {"destination":"http://fixtures:8000"}):
            driver = FakeDriver(resources)
            with self.assertRaises(AccessError): self.security.call(driver,connection,version,operation,{})
            self.assertEqual(driver.calls,0)
        driver = FakeDriver()
        result = self.security.call(driver,connection,version,operation,{})
        self.assertEqual(driver.calls,1);self.assertNotIn(SECRET,str(result))

    def test_generic_channel_table_and_column_policy(self):
        check_resources({"destinations":["https://example.com"],"channels":["approved"]},{"destination":"https://example.com","channel":"approved"})
        with self.assertRaises(AccessError): check_resources({"destinations":["https://example.com"],"channels":["approved"]},{"destination":"https://example.com","channel":"other"})
        policy = {"destinations":["postgresql://unused"]}
        with self.assertRaises(AccessError): check_resources(policy,{"destination":"postgresql://unused"})
        policy = {"destinations":["https://database-driver.example"],"tables":{"orders":["order_id","status"]}}
        check_resources(policy,{"destination":"https://database-driver.example","table":"orders","columns":["status"]})
        for resource in ({"table":"other","columns":["status"]},{"table":"orders","columns":["private"]},{"table":"orders","columns":[]}):
            with self.assertRaises(AccessError): check_resources(policy,{"destination":"https://database-driver.example",**resource})

    def test_write_requires_exact_single_use_approval(self):
        connection,version,operation = self.connection(write=True)
        driver = FakeDriver()
        with self.assertRaises(AccessError): self.security.call(driver,connection,version,operation,{"title":"a"})
        self.assertEqual(driver.calls,0)
        approval = self.security.approve(connection,version,operation,{"title":"a"})
        with self.assertRaises(AccessError): self.security.call(driver,connection,version,operation,{"title":"b"},approval)
        self.assertEqual(driver.calls,0)
        self.security.call(driver,connection,version,operation,{"title":"a"},approval)
        with self.assertRaises(AccessError): self.security.call(driver,connection,version,operation,{"title":"a"},approval)
        self.assertEqual(driver.calls,1)

    def test_approval_connection_and_version_binding(self):
        connection,version,operation = self.connection(write=True)
        other,other_version,_ = self.connection(write=True)
        approval = self.security.approve(connection,version,operation,{})
        with self.assertRaises(AccessError): self.security.permit(other,other_version,operation,{},RESOURCES,approval)
        with self.assertRaises(AccessError): self.security.permit(connection,other_version,operation,{},RESOURCES,approval)

    def test_concurrent_approval_consumption_permits_at_most_one(self):
        connection,version,operation = self.connection(write=True)
        approval = self.security.approve(connection,version,operation,{})
        def consume(_):
            try:
                self.security.permit(connection,version,operation,{},RESOURCES,approval)
                return True
            except AccessError: return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(consume,range(8))),1)

    def test_expired_revoked_and_policy_changed_approvals_are_rejected(self):
        connection,version,operation = self.connection(write=True)
        expired = self.security.approve(connection,version,operation,{},expires_seconds=1)
        time.sleep(1.1)
        with self.assertRaises(AccessError): self.security.permit(connection,version,operation,{},RESOURCES,expired)
        revoked = self.security.approve(connection,version,operation,{})
        self.security.revoke_approval(revoked)
        with self.assertRaises(AccessError): self.security.permit(connection,version,operation,{},RESOURCES,revoked)
        changed = self.security.approve(connection,version,operation,{})
        self.security.change(connection,policy=POLICY)
        with self.assertRaises(AccessError): self.security.permit(connection,version,operation,{},RESOURCES,changed)

    def test_spent_approval_and_binding_cannot_be_rewritten(self):
        connection,version,operation = self.connection(write=True)
        approval = self.security.approve(connection,version,operation,{})
        self.security.permit(connection,version,operation,{},RESOURCES,approval)
        for values in ({"consumed_at":None},{"arguments_digest":"b"*64}):
            with self.assertRaises(DBAPIError):
                with self.engine.begin() as conn:
                    conn.execute(update(m.approvals).where(m.approvals.c.id==approval).values(**values))

    def test_uncertain_write_retains_intent_and_never_reuses_approval(self):
        connection,version,operation = self.connection(write=True)
        approval = self.security.approve(connection,version,operation,{})
        driver = FakeDriver(failure=TimeoutError())
        with self.assertRaises(AccessError) as raised: self.security.call(driver,connection,version,operation,{},approval)
        self.assertEqual(raised.exception.category,"outcome_unknown")
        with self.engine.connect() as conn:
            unknown = conn.execute(select(m.jobs).where(m.jobs.c.status=="outcome_unknown")).mappings().all()
        self.assertTrue(unknown);self.assertTrue(unknown[-1]["write_intent"])
        with self.assertRaises(AccessError): self.security.call(driver,connection,version,operation,{},approval)
        self.assertEqual(driver.calls,1)

    def test_argument_digest_is_canonical_and_rejects_nonfinite_values(self):
        self.assertEqual(arguments_digest({"b":2,"a":1}),arguments_digest({"a":1,"b":2}))
        with self.assertRaises(ValueError): arguments_digest({"number":float("nan")})
