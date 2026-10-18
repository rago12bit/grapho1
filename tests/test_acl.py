"""One read-only FalkorDB user per tenant: the database refuses a write, and another tenant's graph."""
import pytest
from fastapi.testclient import TestClient
from redis import exceptions as rx

from faultline import db
from faultline.db import Graph


@pytest.fixture()
def readers(fresh):
    yield
    db.drop_readers()


def test_the_reader_reads_its_own_graph_and_nothing_else(readers):
    assert db.ensure_reader("kestrel")
    own = db.reader_graph("kestrel")
    assert own.ro_query("MATCH (s:Service) RETURN count(s)").result_set[0][0] == 24
    with pytest.raises(rx.NoPermissionError):
        own.query("CREATE (:Probe)")
    with pytest.raises(rx.NoPermissionError):
        db.reader_graph("kestrel", "harbor").ro_query("MATCH (n) RETURN count(n)")
    with pytest.raises(rx.NoPermissionError):
        db.reader("kestrel").execute_command("GRAPH.DELETE", db.graph_name("kestrel"))


def test_tenant_reads_go_through_it_and_writes_do_not(readers):
    db.ensure_reader("kestrel")
    g = Graph("kestrel")
    assert g.ro is not g.g                                     # reads: the tenant's user; writes: the shared one
    assert g.read("MATCH (s:Service {name: 'auth'}) RETURN s.name AS s") == [{"s": "auth"}]
    g.write("MATCH (s:Service {name: 'auth'}) SET s.probe = 1")
    assert g.read("MATCH (s:Service {name: 'auth'}) RETURN s.probe AS p") == [{"p": 1}]
    h = Graph("harbor")
    assert db.reader("harbor") is None and h.ro is h.g                 # a tenant no request asked for: shared user


def test_a_request_for_a_tenant_reads_as_that_tenant(readers):
    with TestClient(__import__("faultline.api", fromlist=["app"]).app) as c:
        assert c.get("/api/kestrel/graph").status_code == 200
        assert db.reader("kestrel") is not None
        assert c.get("/api/engine").json()["acl"]["enabled"]


def test_the_reader_comes_back_after_its_user_is_lost(readers):
    db.ensure_reader("kestrel")
    db.client().connection.execute_command("ACL", "DELUSER", db.reader_user("kestrel"))
    db.reader("kestrel").connection_pool.disconnect()     # as if FalkorDB had restarted
    assert Graph("kestrel").read("MATCH (s:Service) RETURN count(s) AS n")[0]["n"] == 24
