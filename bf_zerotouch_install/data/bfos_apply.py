#!/usr/bin/env python3
"""Blue Fox OS install-time policy applier — runs in the Anaconda %post (chroot).

Reads the policy JSON staged by bfos_provision.py (copied into the target by the
post-install no-chroot step) and applies the *install block* to the freshly
installed system:

  - /etc/hostname, /etc/locale.conf, /etc/vconsole.conf, /etc/localtime
  - keyboard: /etc/vconsole.conf (console) AND the graphical layout, which is a
    separate setting entirely — /etc/X11/xorg.conf.d/00-keyboard.conf plus a
    /etc/skel/.config/kxkbrc seed so the account created below starts with it
  - root account locked/enabled per policy
  - seat login: when login.mode == 'sssd', write /etc/sssd/sssd.conf pointed at
    the Authentik LDAP outpost, enable offline credential caching per the policy,
    and select the sssd profile with home-dir creation (login synced to Authentik)
  - when login.mode == 'local', create the provisioning user as a local account

The *session block* (mounts, PWAs, theme) is left in
/var/lib/bluefox-welcome/provisioning.json for the firstboot welcome agent.

stdlib-only. Render functions are pure (return file contents) so they unit-test
without a target system; apply() performs the side effects behind injectable seams.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

STAGED_JSON = "/var/lib/bluefox-welcome/provisioning.json"

# A single DNS label or dotted name; must start/end alphanumeric. Anything else
# (spaces, newlines, control or shell chars) is rejected → safe default.
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$")
# POSIX-ish login name; gates the value handed to `useradd` in local mode.
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")
# XKB layout / variant / options tokens, e.g. "ca", "multix", "ca,us",
# "grp:alt_shift_toggle". Deliberately narrow: these values are interpolated
# into an xorg.conf Section and an INI file, both written as root.
_XKB_RE = re.compile(r"^[A-Za-z0-9_,:+()-]*$")


def _exiger(condition, message):
    """Leve si la condition est fausse — sert a faire ECHOUER une action que le
    journal d'installation afficherait sinon en OK. Une action qui ne verifie
    rien rend un feu vert sur ce qu'on n'a pas regarde."""
    if not condition:
        raise ValueError(message)


def _ini_safe(value, default="") -> str:
    """Sanitize a policy-supplied value before it lands in an INI-style config
    (sssd.conf): drop CR/LF and control chars so a crafted value can't inject
    extra directives. Defense-in-depth — the policy is first-party over TLS, but
    this file is written as root, so we never trust its contents verbatim."""
    s = str(value if value is not None else default)
    s = s.replace("\r", "").replace("\n", "")
    s = "".join(ch for ch in s if ord(ch) >= 0x20)
    return s.strip()


def render_hostname(policy) -> str:
    name = (policy.get("install", {}).get("hostname") or "").strip()
    if not _HOSTNAME_RE.match(name):
        name = "blue-fox-os"
    return name + "\n"


def render_locale_conf(policy) -> str:
    locale = policy.get("install", {}).get("locale", "fr_CA.UTF-8")
    return f"LANG={locale}\n"


def _xkb(policy) -> tuple:
    """Resolve (layout, variant, options) for the *graphical* session.

    `keymap` alone is not enough: it only ever reaches /etc/vconsole.conf, which
    the console reads and the desktop ignores. The policy may carry an explicit
    x_layout (e.g. keymap 'ca' but layout 'ca' variant 'multix' for the CSA
    Canadian Multilingual layout); when it doesn't, the console keymap is the
    best available guess. Anything failing _XKB_RE degrades to the default
    rather than landing verbatim in a root-written config.
    """
    install = policy.get("install", {})

    def clean(key, default=""):
        value = str(install.get(key) or "").strip()
        return value if _XKB_RE.match(value) else default

    layout = clean("x_layout") or clean("keymap", "ca") or "ca"
    return layout, clean("x_variant"), clean("x_options")


def render_vconsole_conf(policy) -> str:
    """Console keymap + the XKB triple systemd-localed also records here.

    localectl keeps both in this file; writing the XKB keys means a later
    `localectl status` reports what we actually provisioned instead of showing
    the layout as unset.
    """
    keymap = policy.get("install", {}).get("keymap", "ca")
    layout, variant, options = _xkb(policy)
    out = f"KEYMAP={keymap}\nXKBLAYOUT={layout}\n"
    if variant:
        out += f"XKBVARIANT={variant}\n"
    if options:
        out += f"XKBOPTIONS={options}\n"
    return out


def render_x11_keymap_conf(policy) -> str:
    """/etc/X11/xorg.conf.d/00-keyboard.conf — the system-wide graphical layout.

    Canonical location written by `localectl set-x11-keymap`; honoured by X11
    and by Wayland compositors that fall back to the system default.
    """
    layout, variant, options = _xkb(policy)
    lines = [
        "# Written by Blue Fox OS install-time provisioning (bfos_apply.py).",
        "# Change it via the org/user policy in Odoo, not by hand: a re-provision",
        "# rewrites this file.",
        'Section "InputClass"',
        '        Identifier "system-keyboard"',
        '        MatchIsKeyboard "on"',
        f'        Option "XkbLayout" "{layout}"',
    ]
    if variant:
        lines.append(f'        Option "XkbVariant" "{variant}"')
    if options:
        lines.append(f'        Option "XkbOptions" "{options}"')
    lines.append("EndSection")
    return "\n".join(lines) + "\n"


def render_kxkbrc(policy) -> str:
    """/etc/skel/.config/kxkbrc — Plasma's own keyboard config.

    KWin reads kxkbrc first and only consults the system default when the user
    has none, so seeding skel is what makes the layout stick for the account
    created moments later in local mode. `Use=true` is required — without it
    Plasma treats the layout list as inactive.
    """
    layout, variant, options = _xkb(policy)
    out = ("[Layout]\n"
           f"LayoutList={layout}\n"
           f"VariantList={variant}\n"
           "Use=true\n"
           "SwitchMode=Global\n")
    if options:
        out += f"Options={options}\nResetOldOptions=true\n"
    return out


def _seat_username(policy) -> str:
    """Nom de compte de siege, en forme courte.

    Le login Odoo est une adresse ; le compte Unix porte la partie qui precede
    l'arobase, en mode local comme en mode sssd. Les deux chemins doivent tirer
    le MEME nom : `simple_allow_users` recevait l'adresse entiere alors que
    l'annuaire sert le nom court, donc la regle d'acces ne designait personne et
    sssd refusait la session apres avoir pourtant valide le mot de passe.
    """
    login = policy.get("user", {}).get("login", "") or ""
    return login.split("@")[0]


def render_sssd_conf(policy) -> str:
    """Render /etc/sssd/sssd.conf binding the seat login to the Authentik LDAP
    outpost, with offline credential caching gated by the org policy."""
    install = policy.get("install", {})
    login = install.get("login", {})
    pol = policy.get("policies", {})
    offline = pol.get("offline_login", {}) if isinstance(pol, dict) else {}
    username = _ini_safe(_seat_username(policy))

    uri = _ini_safe(login.get("ldap_uri", ""))
    cache = "true" if offline.get("enabled", True) else "false"
    expire = int(offline.get("max_offline_days", 0) or 0)

    # TLS : une URI ldaps:// est chiffree des la poignee de main. Y ajouter
    # StartTLS revient a demander a negocier TLS DANS un canal qui l'est deja,
    # et sssd echoue. StartTLS ne vaut que pour une URI ldap:// en clair. La
    # version precedente posait `true` en dur a cote d'une URI ldaps://.
    start_tls = "true" if uri.startswith("ldap://") else "false"

    # L'avant-poste LDAP d'Authentik ne sert PAS les recherches anonymes. Sans
    # identite de liaison, sssd ne resout aucun utilisateur : la connexion
    # echoue avant meme qu'un mot de passe soit demande. Le fichier est ecrit en
    # 0600, c'est ce qui rend le secret tenable sur le poste.
    bind_dn = _ini_safe(login.get("bind_dn", ""))
    bind_pw = _ini_safe(login.get("bind_password", ""))
    bind_lines = ""
    if bind_dn:
        bind_lines = f"ldap_default_bind_dn = {bind_dn}\n"
        if bind_pw:
            bind_lines += f"ldap_default_authtok = {bind_pw}\n"

    allow_line = f"simple_allow_users = {username}\n" if username else ""
    return (
        "[sssd]\n"
        "config_file_version = 2\n"
        "services = nss, pam\n"
        "domains = bluefox\n"
        "\n"
        "[domain/bluefox]\n"
        "id_provider = ldap\n"
        "auth_provider = ldap\n"
        "access_provider = simple\n"
        f"ldap_uri = {uri}\n"
        f"ldap_search_base = {_ini_safe(login.get('ldap_base_dn', ''))}\n"
        "ldap_schema = rfc2307bis\n"
        "ldap_user_object_class = user\n"
        "ldap_group_object_class = group\n"
        # ⚠️ MESURE, pas une valeur documentee. En rfc2307bis,
        # sssd nomme l'utilisateur par `uid` — et l'avant-poste d'Authentik sert
        # dans `uid` une empreinte de 64 caracteres, pas le nom du compte. Sans
        # cette ligne, la machine cree un compte nomme
        # « b1057f273c9dccc30d3e57e96b026ffbab02c6bb5ebe24385ce7f1d13acb9f17 ».
        # Le nom utilisable est `cn`, celui-la meme que porte le DN.
        "ldap_user_name = cn\n"
        # ⚠️ MESURE avec sssd branche sur un annuaire vivant. Le
        # traitement des groupes imbriques echoue a CHAQUE recherche —
        # « sdap_nested_group_single_step_done: Error processing direct
        # membership [22]: Invalid argument » — et il ne se contente pas de
        # journaliser : il TRONQUE la liste des membres. Avec, un groupe de
        # deux membres n'en rendait qu'un ; sans, il rend les deux. L'avant-poste
        # n'imbrique rien, la resolution ne perd donc rien a s'en passer.
        "ldap_group_nesting_level = 0\n"
        f"{bind_lines}"
        f"ldap_id_use_start_tls = {start_tls}\n"
        "ldap_tls_reqcert = demand\n"
        # L'annuaire ne sert pas forcement homeDirectory ni loginShell ; sans
        # ces deux replis, un compte resolu ouvre une session sans repertoire
        # personnel et avec /bin/sh.
        "fallback_homedir = /home/%u\n"
        "default_shell = /bin/bash\n"
        # ⚠️ MESURE. L'annuaire sert « Prenom », majuscule
        # comprise, parce que c'est le nom du compte Authentik ; la politique,
        # elle, porte le login Odoo « prenom@… » dont on tire « prenom ». Les
        # deux ne se rencontrent jamais si sssd compare a la casse : la regle
        # d'acces designerait un compte inexistant, et l'utilisateur devrait
        # taper son nom avec la bonne majuscule a l'ecran de connexion. En
        # insensible, sssd replie tout en minuscules et les deux formes se
        # rejoignent.
        "case_sensitive = false\n"
        f"cache_credentials = {cache}\n"
        "enumerate = false\n"
        f"{allow_line}"
        "\n"
        "[pam]\n"
        f"offline_credentials_expiration = {expire}\n"
    )


# Cle(s) a retirer avant de rendre la politique lisible par l'usager. Le bloc
# login porte le mot de passe de liaison LDAP : c'est un
# compte de service de l'annuaire, il n'a rien a faire dans un fichier que la
# session peut lire.
_SECRETS_DE_POLITIQUE = (("install", "login", "bind_password"),)

PUBLIC_JSON = "/var/lib/bluefox-welcome/policy-public.json"

# Meme nom que COMPTE_SECOURS dans bfos_provision.py, qui le cree au %pre.
COMPTE_SECOURS = "bfos-secours"


def render_public_policy(policy) -> str:
    """Rend la politique EXPURGEE, destinee a l'agent d'accueil.

    ⚠️ POURQUOI CE FICHIER EXISTE
    La politique complete est deposee en 0600 root — elle porte un secret. Or
    l'agent d'accueil demarre depuis /etc/xdg/autostart, donc EN TANT QUE
    L'USAGER : il ne peut pas la lire. `load_provisioning` avalait l'erreur de
    permission et rendait {}, et l'agent basculait alors sur l'assistant manuel
    en 5 pages — celui qui redemande clavier, langue, fuseau et theme que la
    politique fixe deja. Aucun montage, aucune PWA, et pas un mot a l'ecran.

    Le fichier expurge est ecrit en 0644 : tout ce dont la session a besoin,
    rien de ce qu'elle ne doit pas voir.
    """
    import copy as _copy
    public = _copy.deepcopy(policy)
    for chemin in _SECRETS_DE_POLITIQUE:
        noeud = public
        for cle in chemin[:-1]:
            noeud = noeud.get(cle) if isinstance(noeud, dict) else None
            if not isinstance(noeud, dict):
                noeud = None
                break
        if isinstance(noeud, dict):
            noeud.pop(chemin[-1], None)
    return json.dumps(public, indent=2, ensure_ascii=False) + "\n"


def secours_voulu(policy):
    """Le compte de secours est-il voulu par la politique ?

    Cle `install.login.break_glass`, pilotee par bf_policy.
    ⚠️ ABSENTE = OUI. Une politique anterieure a cette cle ne doit pas perdre en
    silence un chemin de recuperation : seule une politique qui dit
    EXPLICITEMENT non retire le compte. Cette fonction existe a l'identique
    dans bfos_provision.py et bfos_apply.py — deux scripts embarques separement
    dans le kickstart, qui ne peuvent pas s'importer l'un l'autre. Un test
    exige qu'ils tranchent pareil.
    """
    login = (((policy or {}).get("install") or {}).get("login") or {})
    valeur = login.get("break_glass")
    return True if valeur is None else bool(valeur)


def render_seat_sudoers(policy) -> str:
    """Droit d'administration du compte de siege, en mode sssd.

    En mode local, `useradd -m -G wheel` donne sudo au passage. En mode sssd le
    compte vient de l'annuaire : il n'est membre d'aucun groupe LOCAL, donc
    `wheel` ne le couvre pas, et la machine se retrouve sans personne pour
    l'administrer une fois root verrouille. On nomme l'utilisateur plutot que
    d'esperer qu'un groupe de l'annuaire porte le gid 10 — un gid local ne se
    reclame pas depuis LDAP.
    """
    username = _seat_username(policy)
    return ("# Genere par bfos_apply.py depuis la politique (mode sssd).\n"
            "# Ne pas editer a la main.\n"
            f"{username} ALL=(ALL) ALL\n")


def render_flatpak_list(app_ids, kind) -> str:
    """Rend un fichier de liste Flatpak pour system-flatpak-setup.

    Format : un ID par ligne, `#` en commentaire. Ce sont les fichiers
    /etc/bluebuild/default-flatpaks/system/{install,remove}, que
    system-flatpak-setup (root, premier demarrage + minuterie) combine ainsi :

        (liste bakee dans l'image  −  /etc remove)  +  /etc install

    D'ou une semantique d'ajout et de retrait par-dessus la base de l'image,
    sans que nous ayons a installer quoi que ce soit nous-memes : on ecrit deux
    fichiers texte et le service amont fait le travail, y compris l'ajout du
    remote Flathub. La minuterie fait aussi qu'un changement de politique
    finit par converger sur une machine deja installee.
    """
    # ⚠️ Aucune validation de forme ici : c'est bf.policy.app qui la porte, cote
    # Odoo, et son motif a ete valide contre les 3 269 identifiants reels de
    # Flathub (14 d'entre eux ont un label commencant par « _ », convention
    # Flatpak quand le segment de domaine commence par un chiffre). Revalider un
    # id ici, avec un motif invente, ne ferait que reintroduire ce faux rejet.
    header = [
        f"# Genere par bfos_apply.py depuis la politique ({kind}).",
        "# Un ID Flatpak par ligne. Ne pas editer a la main : ce fichier est",
        "# reecrit a chaque application de la politique.",
        "",
    ]
    return "\n".join(header + list(app_ids)) + "\n"


def apply(policy, root="/", run=subprocess.run, writer=None):
    """Apply the install block to the target rooted at `root`.

    Returns a list of (action, ok, detail) tuples. Best-effort: a failed action
    is recorded but does not abort the others (the install must still complete).
    """
    writer = writer or _default_writer(root)
    install = policy.get("install", {})
    login = install.get("login", {})
    results = []

    def record(action, fn):
        try:
            fn()
            results.append((action, True, ""))
        except Exception as exc:  # noqa: BLE001
            results.append((action, False, str(exc)))

    record("hostname", lambda: writer("/etc/hostname", render_hostname(policy)))
    record("locale", lambda: writer("/etc/locale.conf", render_locale_conf(policy)))
    record("vconsole", lambda: writer("/etc/vconsole.conf", render_vconsole_conf(policy)))
    record("x11-keymap", lambda: writer(
        "/etc/X11/xorg.conf.d/00-keyboard.conf", render_x11_keymap_conf(policy)))
    # Must precede the useradd below: skel is copied at account creation, so a
    # kxkbrc written afterwards would never reach the user's home.
    record("skel-kxkbrc", lambda: writer(
        "/etc/skel/.config/kxkbrc", render_kxkbrc(policy)))

    # Applications Flatpak choisies dans la politique (bloc additif : une
    # politique d'avant cette version n'a pas de cle "apps" et on n'ecrit alors
    # AUCUN fichier, ce qui laisse la base de l'image telle quelle).
    apps = policy.get("apps") or {}
    for key, path in (
        ("install", "/etc/bluebuild/default-flatpaks/system/install"),
        ("remove", "/etc/bluebuild/default-flatpaks/system/remove"),
    ):
        ids = [str(a).strip() for a in (apps.get(key) or []) if str(a).strip()]
        if not ids:
            continue
        record(f"flatpak-{key}", lambda p=path, i=ids, k=key: writer(
            p, render_flatpak_list(i, k)))

    tz = install.get("timezone")
    if tz:
        record("timezone", lambda: run(
            ["ln", "-sf", f"/usr/share/zoneinfo/{tz}",
             os.path.join(root, "etc/localtime")], check=False))

    if install.get("root") == "locked":
        record("root-lock", lambda: run(
            _chroot(root, ["passwd", "-l", "root"]), check=False))

    # Compte de secours refuse par la politique (choix de l'organisation).
    # Le %pre l'a laisse VERROUILLE pour satisfaire Anaconda, qui
    # refuse de commencer sans usager quand root est verrouille ; on le retire
    # ici, installation faite.
    #
    # ⚠️ GARDE : on ne le retire QUE si la connexion passe par l'annuaire. En
    # mode local, avec root verrouille, le supprimer laisserait une machine
    # sans AUCUNE porte d'entree — pas un choix de politique, une machine
    # murée. Dans ce cas on le garde, et on le dit.
    if not secours_voulu(policy):
        if login.get("mode") == "sssd":
            record("secours-retire", lambda: run(
                _chroot(root, ["userdel", "-r", COMPTE_SECOURS]), check=False))
        else:
            record("secours-garde", lambda: print(
                f"[bfos-apply] la politique refuse {COMPTE_SECOURS}, mais la "
                "connexion n'est pas en mode sssd : le retirer laisserait la "
                "machine sans aucune porte d'entree. Compte conserve."))

    if login.get("mode") == "sssd":
        # Une politique en mode sssd sans URI d'annuaire ou sans identite de
        # liaison produit EXACTEMENT une machine qui n'ouvre aucune session. On le
        # dit ici, plutot que d'ecrire une configuration dont on sait deja
        # qu'elle ne peut pas resoudre un utilisateur.
        record("sssd-gate", lambda: _exiger(
            login.get("ldap_uri") and login.get("bind_dn"),
            "politique en mode sssd sans ldap_uri ou sans bind_dn"))
        record("sssd-conf", lambda: writer(
            "/etc/sssd/sssd.conf", render_sssd_conf(policy), mode=0o600))
        # ⚠️ check=True, et c'est le coeur du correctif. Ces deux gestes sont la
        # difference entre une machine qui ouvre une session et une qui n'en
        # ouvre aucune. En check=False, un paquet absent de l'image les faisait
        # echouer SANS AUCUNE TRACE : le journal d'installation affichait OK
        # pour les deux, et le defaut ne se voyait qu'a l'ecran de connexion.
        record("sssd-profile", lambda: run(
            _chroot(root, ["authselect", "select", "sssd", "with-mkhomedir",
                           "--force"]), check=True))
        record("sssd-enable", lambda: run(
            _chroot(root, ["systemctl", "enable", "sssd.service",
                           "oddjobd.service"]), check=True))
        seat = _seat_username(policy)
        if seat and _USERNAME_RE.match(seat):
            record("seat-sudo", lambda: writer(
                "/etc/sudoers.d/10-bluefox-seat", render_seat_sudoers(policy),
                mode=0o440))
    # La copie expurgee, pour l'agent d'accueil qui tourne en tant que l'usager.
    # Ecrite QUELLE QUE SOIT la branche de connexion : c'est elle qui porte les
    # preferences de session, les montages et les PWA.
    record("policy-public", lambda: writer(
        PUBLIC_JSON, render_public_policy(policy), mode=0o644))

    if login.get("mode") == "local":
        username = _seat_username(policy)
        if username and _USERNAME_RE.match(username):
            record("local-user", lambda: run(
                _chroot(root, ["useradd", "-m", "-G", "wheel", username]),
                check=False))

    return results


def _chroot(root, argv):
    return argv if root == "/" else ["chroot", root] + argv


def _default_writer(root):
    def write(path, content, mode=None):
        full = os.path.join(root, path.lstrip("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if mode is None:
            with open(full, "w") as fh:
                fh.write(content)
            return
        # `mode` n'est passe que pour restreindre — /etc/sssd/sssd.conf et ses
        # identifiants de liaison. Ecrire puis chmoder laissait le fichier
        # lisible par tous entre les deux instructions ; c'est le noyau qui
        # applique le mode a la creation. Le fchmod ne couvre que le fichier
        # deja present, dont O_CREAT ignore le mode.
        fd = os.open(full, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            os.fchmod(fd, mode)
            fh = os.fdopen(fd, "w")
        except BaseException:
            os.close(fd)
            raise
        with fh:
            fh.write(content)
    return write


def main(argv=None):
    path = (argv[1] if argv and len(argv) > 1 else STAGED_JSON)
    try:
        with open(path) as fh:
            policy = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"[bfos-apply] cannot read {path}: {exc}", file=sys.stderr)
        return 0  # do not fail the install
    if not (isinstance(policy, dict) and policy.get("schema") == "bf-policy/v2"
            and isinstance(policy.get("install"), dict)):
        print("[bfos-apply] staged policy missing or not bf-policy/v2 ; skipping",
              file=sys.stderr)
        return 0
    for action, ok, detail in apply(policy):
        print(f"[bfos-apply] {'OK' if ok else 'FAIL'} {action} {detail}".rstrip(),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
