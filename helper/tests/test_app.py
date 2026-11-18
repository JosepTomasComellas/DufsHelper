import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import store as st  # noqa: E402
from app.web import create_app  # noqa: E402


class FakeRestarter:
    last_error = None
    calls = 0

    def schedule(self):
        self.calls += 1


def basic(user, pw):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode(),
            "Origin": "http://localhost"}


@pytest.fixture
def env(tmp_path):
    state, data = tmp_path / "state", tmp_path / "data"
    data.mkdir()
    s = st.Store(str(state), str(data))
    d = s.load()
    s.set_user(d, "admin", "adminpass1", {"/Public": "rw", "/Admin": "rw"})
    s.set_user(d, "tute", "tutepass1", {"/Public": "rw"})
    d["anonymous"] = {"/Public": "ro"}
    s.save(d)
    rs = FakeRestarter()
    app = create_app({"state_dir": str(state), "data_dir": str(data), "restarter": rs, "admins": {"admin"}})
    return app.test_client(), s, rs, data


def test_rules_parse_and_reject():
    assert st.parse_rules("/A:rw\n/B c:ro") == {"/A": "rw", "/B c": "ro"}
    for bad in ("A:rw", "/A:xx", "/../x:rw", "/a,b:rw", "/a@b:rw"):
        with pytest.raises(st.ValidationError):
            st.parse_rules(bad)


def test_yaml_generation(env):
    _, s, _, _ = env
    y = open(s.yaml_file, encoding="utf-8").read()
    assert y.startswith("# Generat") and 'auth:' in y
    assert '"@/Public:ro"' in y and '"admin:$6$' in y and "@/Public:rw,/Admin:rw" in y
    assert oct(os.stat(s.yaml_file).st_mode & 0o777) == oct(0o600) or os.name == "nt"


def test_requires_auth(env):
    c, *_ = env
    assert c.get("/helper/").status_code == 401
    assert c.get("/helper/", headers=basic("admin", "wrong")).status_code == 401
    assert c.get("/helper/health").data == b"ok"


def test_rate_limit(env):
    c, *_ = env
    for _ in range(10):
        c.get("/helper/", headers=basic("admin", "wrong"))
    assert c.get("/helper/", headers=basic("admin", "adminpass1")).status_code == 429


def test_csrf_blocked(env):
    c, *_ = env
    h = basic("admin", "adminpass1")
    h["Origin"] = "http://evil.example"
    assert c.post("/helper/users", headers=h, data={"name": "x", "rules": "/Public:rw"}).status_code == 403


def test_non_admin_cannot_manage(env):
    c, s, rs, _ = env
    r = c.post("/helper/users", headers=basic("tute", "tutepass1"), data={"name": "x", "rules": "/Public:rw"})
    assert r.status_code == 403 and "x" not in s.load()["users"] and rs.calls == 0
    assert "Usuaris" not in c.get("/helper/", headers=basic("tute", "tutepass1")).get_data(as_text=True)


def test_self_password_change(env):
    c, s, rs, _ = env
    h = basic("tute", "tutepass1")
    r = c.post("/helper/password", headers=h, data={"current": "mal", "new": "nova-pass-1", "confirm": "nova-pass-1"})
    assert r.status_code == 400
    r = c.post("/helper/password", headers=h, data={"current": "tutepass1", "new": "nova-pass-1", "confirm": "nova-pass-1"})
    assert r.status_code == 200 and rs.calls == 1
    assert st.verify_password("nova-pass-1", s.load()["users"]["tute"]["hash"])


def test_admin_crud_and_dirs(env):
    c, s, rs, data = env
    h = basic("admin", "adminpass1")
    r = c.post("/helper/users", headers=h, data={"name": "nou", "password": "", "rules": "/Public:rw\n/Nova:rw"})
    assert r.status_code == 200 and (data / "Nova").is_dir()
    assert "nou" in s.load()["users"]
    assert c.post("/helper/users/nou/delete", headers=h).status_code == 200
    assert "nou" not in s.load()["users"] and (data / "Nova").is_dir()  # carpeta intacta
    assert c.post("/helper/users/admin/delete", headers=h).status_code == 400
    assert rs.calls == 2


def test_path_traversal_blocked(env):
    c, s, _, data = env
    r = c.post("/helper/users", headers=basic("admin", "adminpass1"), data={"name": "t", "rules": "/../fora:rw"})
    assert r.status_code == 400


def test_xss_escaped(env):
    c, s, *_ = env
    d = s.load()
    d["users"]["tute"]["rules"] = {"/<script>": "rw"}
    s.save(d)
    html = c.get("/helper/", headers=basic("admin", "adminpass1")).get_data(as_text=True)
    assert "<script>" not in html


def test_import_from_compose_and_env():
    compose = '''command: [ "--auth", "@/Public:rw,/llegeix-me.html:ro",
      "--auth", "${DUFS_AUTH_ADMIN}@/Public:rw,/Admin:rw" ]'''
    env = st.parse_env("DUFS_AUTH_ADMIN=admin:curta\n")
    d = st.import_auth(st.extract_auth_args(compose), env)
    assert d["anonymous"] == {"/Public": "rw", "/llegeix-me.html": "ro"}
    assert d["users"]["admin"]["rules"] == {"/Public": "rw", "/Admin": "rw"}
    assert st.verify_password("curta", d["users"]["admin"]["hash"])
    with pytest.raises(st.ValidationError):
        st.import_auth(["${X}@/A:rw"], {})
