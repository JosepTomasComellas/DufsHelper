"""CLI: ``serve`` (per defecte), ``init`` i ``import``."""
import argparse
import os
import sys

from . import store as st


def cmd_import(args) -> int:
    with open(args.compose, encoding="utf-8") as f:
        auth_args = st.extract_auth_args(f.read())
    env = {}
    if args.env and os.path.exists(args.env):
        with open(args.env, encoding="utf-8") as f:
            env = st.parse_env(f.read())
    store = st.Store(args.state, args.data)
    if os.path.exists(store.users_file) and not args.force:
        print(f"{store.users_file} ja existeix (usa --force per sobreescriure).", file=sys.stderr)
        return 1
    data = st.import_auth(auth_args, env)
    store.save(data)
    print(f"Importats {len(data['users'])} usuaris i {len(data['anonymous'])} regles anònimes.")
    for name, u in data["users"].items():
        print(f"  {name}: {st.format_rules(u['rules']).replace(chr(10), ' ')}")
    return 0


def cmd_init(args) -> int:
    """Estat inicial net: només l'administrador, amb contrasenya aleatòria."""
    store = st.Store(args.state, args.data)
    if os.path.exists(store.users_file):
        print("Ja inicialitzat.", file=sys.stderr)
        return 1
    password = os.environ.get("HELPER_INIT_PASSWORD") or st.generate_password(16)
    data = {"anonymous": {}, "users": {}}
    store.set_user(data, args.admin, password, st.parse_rules("/Public:rw"))
    store.save(data)
    print(f"Usuari «{args.admin}» creat. Contrasenya: {password}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="dufshelper")
    p.add_argument("--state", default=os.environ.get("STATE_DIR", "/state"))
    p.add_argument("--data", default=os.environ.get("DATA_DIR", "/data"))
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    i = sub.add_parser("import", help="importa .env + compose.yaml actuals")
    i.add_argument("--compose", default="/project/compose.yaml")
    i.add_argument("--env", default="/project/.env")
    i.add_argument("--force", action="store_true")
    n = sub.add_parser("init", help="crea un estat nou amb un sol administrador")
    n.add_argument("--admin", default="admin")
    args = p.parse_args()

    if args.cmd == "import":
        return cmd_import(args)
    if args.cmd == "init":
        return cmd_init(args)

    from waitress import serve

    from .web import create_app

    serve(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threads=4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
