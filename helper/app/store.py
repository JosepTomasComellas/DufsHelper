"""Emmagatzematge d'usuaris i generació de la configuració de dufs.

Font de veritat: ``users.json``. Se'n deriva ``dufs.yaml`` (només la clau
``auth``) que dufs llegeix amb ``--config``.
"""
import json
import os
import re
import secrets
import string
import tempfile

from passlib.hash import sha512_crypt

USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
PATH_BAD = re.compile(r"[,:@|\x00-\x1f]")
PERMS = ("ro", "rw")
MIN_PASSWORD = 8
_HASH_ROUNDS = 20000  # dufs verifica el hash a cada petició: no el pugem gaire


class ValidationError(ValueError):
    """Dada d'entrada no vàlida (el missatge és apte per mostrar a l'usuari)."""


# --------------------------------------------------------------- contrasenyes
def hash_password(password: str, check: bool = True) -> str:
    if check and len(password) < MIN_PASSWORD:
        raise ValidationError(f"La contrasenya ha de tenir almenys {MIN_PASSWORD} caràcters.")
    return sha512_crypt.using(rounds=_HASH_ROUNDS).hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return sha512_crypt.verify(password, hashed)
    except ValueError:
        return False


def generate_password(length: int = 12) -> str:
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "lIO01")
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ------------------------------------------------------------------- regles
def validate_username(name: str) -> str:
    if not USER_RE.match(name or ""):
        raise ValidationError("Nom d'usuari no vàlid (1-32 caràcters: lletres, xifres, . _ -).")
    return name


def parse_rules(text: str) -> dict:
    """Converteix text ``/Carpeta:rw`` (una regla per línia) en un dict."""
    rules = {}
    for raw in re.split(r"[\n\r]+", text or ""):
        raw = raw.strip()
        if not raw:
            continue
        path, sep, perm = raw.rpartition(":")
        path, perm = path.strip(), perm.strip().lower()
        if not sep or perm not in PERMS:
            raise ValidationError(f"Regla no vàlida «{raw}»: usa el format /Carpeta:rw o /Carpeta:ro.")
        if not path.startswith("/") or PATH_BAD.search(path) or ".." in path.split("/"):
            raise ValidationError(f"Ruta no vàlida «{path}»: ha de començar per / i no pot contenir , : @ | ni ..")
        rules[path] = perm
    return rules


def format_rules(rules: dict) -> str:
    return "\n".join(f"{p}:{m}" for p, m in rules.items())


# ------------------------------------------------------------------- estat
class Store:
    def __init__(self, state_dir: str, data_dir: str):
        self.state_dir = state_dir
        self.data_dir = data_dir
        self.users_file = os.path.join(state_dir, "users.json")
        self.yaml_file = os.path.join(state_dir, "dufs.yaml")

    def load(self) -> dict:
        try:
            with open(self.users_file, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"anonymous": {}, "users": {}}
        data.setdefault("anonymous", {})
        data.setdefault("users", {})
        return data

    def save(self, data: dict) -> None:
        """Desa users.json i regenera dufs.yaml (escriptura atòmica, mode 600)."""
        os.makedirs(self.state_dir, exist_ok=True)
        self._atomic_write(self.users_file, json.dumps(data, indent=2, ensure_ascii=False))
        self._atomic_write(self.yaml_file, render_dufs_yaml(data))

    def _atomic_write(self, path: str, content: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.state_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def ensure_yaml(self) -> None:
        """Garanteix que dufs.yaml existeix (dufs no arrenca sense ell)."""
        if not os.path.exists(self.yaml_file):
            self.save(self.load())

    def make_dirs(self, rules: dict) -> None:
        """Crea les carpetes de les regles que no existeixen (mai en copia ni n'esborra)."""
        root = os.path.realpath(self.data_dir)
        for path in rules:
            target = os.path.realpath(os.path.join(root, path.lstrip("/")))
            if target != root and not target.startswith(root + os.sep):
                raise ValidationError(f"Ruta fora de la carpeta de dades: {path}")
            os.makedirs(target, exist_ok=True)

    def set_user(self, data: dict, name: str, password: str | None, rules: dict | None) -> None:
        user = data["users"].setdefault(name, {"hash": "", "rules": {}})
        if password is not None:
            user["hash"] = hash_password(password)
        if rules is not None:
            if not rules:
                raise ValidationError("L'usuari ha de tenir almenys una regla d'accés.")
            self.make_dirs(rules)
            user["rules"] = rules


def render_dufs_yaml(data: dict) -> str:
    """Genera ``auth:`` per a dufs. Cada entrada és un string JSON (YAML vàlid)."""
    entries = []
    anon = data.get("anonymous") or {}
    if anon:
        entries.append("@" + ",".join(f"{p}:{m}" for p, m in anon.items()))
    for name, u in data["users"].items():
        if u.get("hash") and u.get("rules"):
            rules = ",".join(f"{p}:{m}" for p, m in u["rules"].items())
            entries.append(f"{name}:{u['hash']}@{rules}")
    head = "# Generat per DufsHelper. No editar a mà.\n"
    if not entries:
        return head + "auth: []\n"
    return head + "auth:\n" + "".join(f"  - {json.dumps(e, ensure_ascii=False)}\n" for e in entries)


# --------------------------------------------------------------- importació
def parse_env(text: str) -> dict:
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        env[k.strip()] = v
    return env


def extract_auth_args(compose_text: str) -> list:
    """Troba els valors de ``"--auth", "<valor>"`` d'un compose.yaml."""
    return re.findall(r"""["']--auth["']\s*,\s*["']([^"']*)["']""", compose_text)


def import_auth(auth_args: list, env: dict) -> dict:
    """Converteix arguments ``--auth`` de dufs (amb ``${VAR}`` resolts amb *env*) a l'estat."""
    data = {"anonymous": {}, "users": {}}
    for arg in auth_args:
        for var in re.findall(r"\$\{(\w+)\}", arg):
            if not env.get(var):  # mai deixar que una variable buida degeneri en accés anònim
                raise ValidationError(f"La variable {var} no existeix o és buida al .env.")
            arg = arg.replace("${" + var + "}", env[var])
        cred, _, rules_txt = arg.rpartition("@")
        rules = parse_rules(rules_txt.replace(",", "\n"))
        if not cred:
            data["anonymous"].update(rules)
            continue
        name, _, password = cred.partition(":")
        if not password:
            raise ValidationError(f"Credencial sense contrasenya per a «{name}» (variable buida?).")
        validate_username(name)
        is_hash = re.match(r"^\$(1|5|6|2[aby])\$", password)
        hashed = password if is_hash else hash_password(password, check=False)
        data["users"][name] = {"hash": hashed, "rules": rules}
    return data
