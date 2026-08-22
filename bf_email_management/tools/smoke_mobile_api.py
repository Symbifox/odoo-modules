#!/usr/bin/env python3
"""Contrôle de fumée de l'API mobile courriel, contre une instance VIVANTE.

La suite `tests/` tourne sur un banc ; celui-ci s'adresse à la vraie instance
après un déploiement. Il vérifie deux choses que le banc ne peut pas dire :

1. **Ce qui est réellement en ligne se comporte comme prévu** — routes
   présentes, anonymes refusés, garde anti-redirection en place.
2. **Le contrat n'a pas dérivé** sous les modèles de l'app Android. Les
   fixtures des tests Kotlin sont figées au jour de leur capture ; elles
   continueraient de passer même si le serveur changeait de forme. Avec un
   jeton, ce script compare les clés réellement servies à celles que l'app
   attend.

Sans jeton (défaut) : seule la surface publique est éprouvée — sûr à lancer
contre la production. Avec ``--token`` : la vérification de forme s'ajoute.

    python3 smoke_mobile_api.py https://odoo.example.com
    python3 smoke_mobile_api.py https://… --token "$JETON"

Code de sortie 0 si tout passe, 1 sinon. Pensé pour un cron ou un CI, pas pour
être relu à l'œil.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "/bf_email_management/mobile/v1"

# Clés dont dépendent les modèles du client Android (hors dépôt). Une disparition
# ici casse l'app silencieusement : kotlinx retombe sur les valeurs par défaut.
EXPECTED = {
    "/config": {
        "user_name", "tz", "signature", "accounts", "counts",
        "snooze_presets", "routable_models", "spawn_kinds",
    },
    "/threads?filter=inbox&limit=1": {"threads", "has_more"},
}
EXPECTED_THREAD_KEYS = {
    "id", "thread_key", "direction", "subject", "from", "from_label",
    "date_ms", "preview", "status", "is_handled", "snoozed_until_ms",
    "has_attachments", "attachment_count", "partner_id", "record",
    "message_count", "unread_count", "last_date_ms", "last_id",
}

results = []


def check(name, condition, detail=""):
    results.append((bool(condition), name, detail))
    print(f"  {'OK  ' if condition else 'ÉCHEC'}  {name}"
          + (f"  — {detail}" if detail and not condition else ""))
    return bool(condition)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Suivre les redirections masquerait justement ce qu'on veut observer."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_no_redirect_opener = urllib.request.build_opener(_NoRedirect)


def request_no_redirect(url):
    """(status, location). Ne suit pas la redirection."""
    try:
        with _no_redirect_opener.open(url, timeout=20) as response:
            return response.status, response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", "")
    except Exception as exc:  # noqa: BLE001
        print(f"    (injoignable : {exc})")
        return 0, ""


def request(url, token=None, method="GET", body=None):
    """(status, parsed_json_or_None). N'élève jamais sur un code d'erreur."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(raw)
            except ValueError:
                return response.status, None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, None
    except Exception as exc:  # noqa: BLE001
        print(f"    (injoignable : {exc})")
        return 0, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("instance")
    parser.add_argument("--token", default=None,
                        help="jeton d'appareil ; active la vérification de forme")
    args = parser.parse_args()
    root = args.instance.rstrip("/") + BASE

    print(f"\n== Surface publique — {args.instance} ==")
    status, body = request(root + "/ping")
    check("/ping répond 200", status == 200, f"reçu {status}")
    check("/ping nomme le module",
          bool(body) and body.get("module") == "bf_email_management",
          str(body))
    version = (body or {}).get("version", "?")
    print(f"    version en ligne : {version}")

    print("\n== Aucune route de données ouverte à l'anonyme ==")
    for path in ("/config", "/threads?filter=inbox", "/message?id=1",
                 "/attachment?email_id=1&idx=0", "/records?model=res.partner&q=ab"):
        status, _ = request(root + path)
        check(f"GET {path} → 401", status == 401, f"reçu {status}")
    for path in ("/mark_read", "/handle", "/snooze", "/reply", "/compose",
                 "/route", "/spawn", "/register_push"):
        status, _ = request(root + path, method="POST", body={})
        check(f"POST {path} → 401", status == 401, f"reçu {status}")

    status, _ = request(root + "/config", token="jeton-manifestement-faux")
    check("un jeton bidon → 401", status == 401, f"reçu {status}")

    print("\n== Garde anti-redirection ouverte ==")
    # Sans session Odoo, ``auth="user"`` renvoie d'abord vers /web/login : le
    # contrôle du schéma ne s'exerce qu'une fois authentifié. Ce qui se vérifie
    # ici sans session, c'est qu'AUCUN code ne part vers le domaine étranger —
    # ni en redirection, ni dans le corps. (La garde elle-même, session en
    # main, est couverte par tests/test_mobile_http.py.)
    status, location = request_no_redirect(
        root + "/auth/start?redirect=https://malveillant.test/vol&state=x")
    # Le domaine étranger réapparaît ENCODÉ dans « /web/login?redirect=… » :
    # chercher la sous-chaîne n'importe où ferait échouer un comportement
    # parfaitement sain. Ce qui compte est la CIBLE de la redirection.
    parsed = urllib.parse.urlparse(location or "")
    instance_host = urllib.parse.urlparse(args.instance).hostname
    check("la redirection reste sur l'instance",
          not parsed.hostname or parsed.hostname == instance_host,
          f"cible = {parsed.hostname!r}")
    # Un code ne doit jamais apparaître comme paramètre de PREMIER niveau.
    top_level = urllib.parse.parse_qs(parsed.query)
    check("aucun code d'échange dans la redirection",
          "code" not in top_level,
          f"paramètres = {sorted(top_level)}")
    check("la route existe (pas un 404 silencieux)",
          status in (200, 302, 303, 400, 403),
          f"reçu {status}")

    if args.token:
        print("\n== Forme du contrat (modèles de l'app Android) ==")
        for path, required in EXPECTED.items():
            status, payload = request(root + path, token=args.token)
            if not check(f"GET {path} → 200", status == 200, f"reçu {status}"):
                continue
            missing = required - set(payload or {})
            check(f"{path} : toutes les clés attendues", not missing,
                  f"manquantes : {sorted(missing)}")

        status, payload = request(root + "/threads?filter=all&limit=1",
                                  token=args.token)
        threads = (payload or {}).get("threads") or []
        if threads:
            missing = EXPECTED_THREAD_KEYS - set(threads[0])
            check("une ligne de fil porte toutes les clés attendues",
                  not missing, f"manquantes : {sorted(missing)}")
        else:
            print("    (aucun fil à inspecter — vérification de forme sautée)")
    else:
        print("\n(pas de --token : vérification de forme sautée)")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} contrôles passés ==")
    if failed:
        print("Échecs :")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
