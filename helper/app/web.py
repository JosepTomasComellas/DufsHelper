"""Interfície web de DufsHelper (Flask). Sense JavaScript, sense base de dades."""
import os
import threading
import time
from urllib.parse import urlparse

from flask import Blueprint, Flask, Response, abort, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import store as st
from .dockerctl import DebouncedRestarter

_DUMMY_HASH = st.hash_password("dummy-password-for-timing")
_FAIL_WINDOW, _FAIL_MAX = 300, 10


def config_from_env() -> dict:
    env = os.environ.get
    return {
        "state_dir": env("STATE_DIR", "/state"),
        "data_dir": env("DATA_DIR", "/data"),
        "container": env("DUFS_CONTAINER", "dufs"),
        "base_path": "/" + env("BASE_PATH", "/helper").strip("/"),
        "admins": {a.strip() for a in env("HELPER_ADMINS", "admin").split(",") if a.strip()},
        "restart_delay": float(env("RESTART_DELAY", "5")),
    }


def create_app(cfg: dict | None = None) -> Flask:
    cfg = {**config_from_env(), **(cfg or {})}
    store = st.Store(cfg["state_dir"], cfg["data_dir"])
    store.ensure_yaml()
    restarter = cfg.get("restarter") or DebouncedRestarter(cfg["container"], cfg["restart_delay"])
    lock = threading.Lock()
    fails: dict = {}

    app = Flask(__name__, static_folder=None)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_host=1, x_proto=1)
    bp = Blueprint("h", __name__, url_prefix=cfg["base_path"])

    # ------------------------------------------------------------ seguretat
    @app.after_request
    def headers(resp):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        return resp

    @app.before_request
    def csrf_check():
        # Basic auth s'envia sol des del navegador: validem que el POST ve de la mateixa web.
        if request.method == "POST":
            src = request.headers.get("Origin") or request.headers.get("Referer") or ""
            if urlparse(src).netloc != request.host:
                abort(403)

    def unauthorized(msg="Cal identificar-se"):
        return Response(msg, 401, {"WWW-Authenticate": 'Basic realm="DufsHelper", charset="UTF-8"'})

    def authenticate():
        """Retorna el nom d'usuari autenticat o una Response d'error."""
        ip = request.remote_addr or "?"
        now = time.time()
        recent = [t for t in fails.get(ip, []) if now - t < _FAIL_WINDOW]
        fails[ip] = recent
        if len(recent) >= _FAIL_MAX:
            return Response("Massa intents. Torna-ho a provar d'aquí uns minuts.", 429)
        a = request.authorization
        if a and a.type == "basic" and a.username is not None:
            user = store.load()["users"].get(a.username)
            ok = st.verify_password(a.password or "", user["hash"] if user else _DUMMY_HASH)
            if ok and user:
                return a.username
        if a:
            fails[ip].append(now)
        return unauthorized()

    # ------------------------------------------------------------- pàgina
    def page(who, msg=None, error=None, newpass=None, status=200):
        data = store.load()
        is_admin = who in cfg["admins"]
        html = render_template(
            "index.html", who=who, is_admin=is_admin, base=cfg["base_path"], msg=msg, error=error,
            newpass=newpass, anon=st.format_rules(data["anonymous"]),
            my_rules=data["users"].get(who, {}).get("rules", {}),
            users=[(n, st.format_rules(u["rules"]), n in cfg["admins"]) for n, u in sorted(data["users"].items())],
            restart_error=restarter.last_error, min_pw=st.MIN_PASSWORD,
        )
        return Response(html, status, {"Content-Type": "text/html; charset=utf-8"})

    def handler(admin_only):
        """Decorador: autentica, serialitza i converteix errors de validació en missatges."""
        def deco(fn):
            def wrapper(**kw):
                who = authenticate()
                if isinstance(who, Response):
                    return who
                if admin_only and who not in cfg["admins"]:
                    return page(who, error="Cal ser administrador.", status=403)
                with lock:
                    try:
                        data = store.load()
                        msg, newpass = fn(who, data, **kw)
                    except st.ValidationError as e:
                        return page(who, error=str(e), status=400)
                    store.save(data)
                restarter.schedule()
                return page(who, msg=msg + " Dufs es reiniciarà en uns segons per aplicar-ho.", newpass=newpass)
            wrapper.__name__ = fn.__name__
            return wrapper
        return deco

    def form(name):
        return (request.form.get(name) or "").strip()

    def known(data, name):
        if name not in data["users"]:
            raise st.ValidationError("L'usuari no existeix.")
        return name

    # --------------------------------------------------------------- rutes
    @bp.get("/")
    def index():
        who = authenticate()
        return who if isinstance(who, Response) else page(who)

    @bp.get("/health")
    def health():
        return "ok"

    @bp.post("/password")
    @handler(admin_only=False)
    def change_password(who, data):
        if not st.verify_password(request.form.get("current", ""), data["users"][who]["hash"]):
            raise st.ValidationError("La contrasenya actual no és correcta.")
        new = request.form.get("new", "")
        if new != request.form.get("confirm", ""):
            raise st.ValidationError("Les contrasenyes noves no coincideixen.")
        store.set_user(data, who, new, None)
        return "Contrasenya canviada. El navegador te la tornarà a demanar.", None

    @bp.post("/users")
    @handler(admin_only=True)
    def create_user(who, data):
        name = st.validate_username(form("name"))
        if name in data["users"]:
            raise st.ValidationError("Aquest usuari ja existeix.")
        password = request.form.get("password") or st.generate_password()
        store.set_user(data, name, password, st.parse_rules(request.form.get("rules", "")))
        return f"Usuari «{name}» creat.", (name, password)

    @bp.post("/users/<name>/rules")
    @handler(admin_only=True)
    def edit_rules(who, data, name):
        store.set_user(data, known(data, name), None, st.parse_rules(request.form.get("rules", "")))
        return f"Regles de «{name}» desades.", None

    @bp.post("/users/<name>/password")
    @handler(admin_only=True)
    def reset_password(who, data, name):
        password = request.form.get("password") or st.generate_password()
        store.set_user(data, known(data, name), password, None)
        return f"Contrasenya de «{name}» canviada.", (name, password)

    @bp.post("/users/<name>/delete")
    @handler(admin_only=True)
    def delete_user(who, data, name):
        if known(data, name) in cfg["admins"]:
            raise st.ValidationError("No es pot esborrar un administrador.")
        del data["users"][name]
        return f"Usuari «{name}» esborrat (les seves carpetes no s'han tocat).", None

    @bp.post("/anonymous")
    @handler(admin_only=True)
    def edit_anonymous(who, data):
        rules = st.parse_rules(request.form.get("rules", ""))
        store.make_dirs(rules)
        data["anonymous"] = rules
        return "Accés anònim desat.", None

    app.register_blueprint(bp)
    return app
