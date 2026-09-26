"""BF Policy models — the Blue Fox OS GPO.

bf.policy.org    : per-company defaults (install + login + policies + session)
                   plus the authorization rule for who may provision.
bf.policy.user   : per-user overrides, merged on top of the org defaults.
bf.policy.mount  : a Nextcloud/WebDAV mount, owned by an org (default) or a user.
bf.policy.pwa    : a pinned progressive web app, owned by an org or a user.
bf.policy.machine: an installed endpoint, enrolled at install time so it can
                   re-fetch its policy later without a human present.

`bf.policy.org.get_policy_json(user)` produces the merged payload returned by
the /api/v1/policy/me controller (schema: static/schema/policy.v2.json).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import secrets
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from . import escrow

_logger = logging.getLogger(__name__)

# Photo servie dans `user.avatar`. L'agent d'accueil n'accepte que ces
# deux formats : un avatar genere par Odoo (SVG) ecrirait un fichier que ni
# Plasma ni AccountsService ne savent afficher. On filtre donc ici plutot que
# d'envoyer quelque chose que le poste jettera.
_AVATAR_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")

CLOCK_FORMATS = [("24h", "24 h"), ("12h", "12 h (AM/PM)")]


class EnrolConflict(Exception):
    """Le ré-enrolement vise une machine enrolee pour un autre usager ou une
    autre organisation. Le controleur le traduit en 409."""


class BfPolicyOrg(models.Model):
    _name = "bf.policy.org"
    _description = "Blue Fox Policy — Organization Defaults"
    _rec_name = "company_id"

    company_id = fields.Many2one(
        "res.company", required=True, ondelete="cascade",
        default=lambda self: self.env.company,
        help="Company this policy applies to. One record per company.")
    active = fields.Boolean(default=True)
    domain = fields.Char(
        index=True,
        help="Root domain the /api/v1/policy/me request arrives on (the domain "
             "the operator typed at the GRUB zero-touch entry, mirrored from "
             "bf.zerotouch.tenant). Matched case-insensitively against the "
             "request Host to route the policy in a multi-tenant deployment, "
             "e.g. 'example.com'. Optional: with a single org row, "
             "the controller serves it regardless of host.")

    # --- Authorization: who may provision a machine (menu-driven, not hardcoded) ---
    # Defaut a « group » pour les NOUVELLES fiches (18.0.2.11.0) : une
    # organisation qui n'a rien decide n'autorise personne. Les fiches
    # existantes gardent leur mode : changer un defaut ne reecrit pas la base.
    # Quel que soit le mode, is_user_authorized refuse d'abord les usagers
    # portail, archives, ou etrangers a la societe de l'organisation.
    provision_mode = fields.Selection(
        [("any", "Any internal user of the company"),
         ("group", "Members of selected groups"),
         ("allowlist", "Explicit users")],
        string="Who can provision", default="group", required=True,
        help="Portal users, archived users and users outside this policy's "
             "company are refused in every mode.")
    provision_group_ids = fields.Many2many(
        "res.groups", "bf_policy_org_group_rel", "org_id", "group_id",
        string="Authorized groups")
    provision_user_ids = fields.Many2many(
        "res.users", "bf_policy_org_user_rel", "org_id", "user_id",
        string="Authorized users")

    # --- Identity / IdP ---
    authentik_userinfo_url = fields.Char(
        string="Authentik userinfo endpoint",
        help="OIDC userinfo URL used to validate the bearer token presented to "
             "/api/v1/policy/me, e.g. "
             "https://auth.example.com/application/o/userinfo/")
    # Rattachement du porteur a un usager Odoo : UNE revendication, comparee
    # au login et a rien d'autre, pour qu'un jeton designe un seul usager.
    identity_claim = fields.Selection(
        [("email", "email"),
         ("preferred_username", "preferred_username"),
         ("sub", "sub")],
        string="Identity claim", default="email", required=True,
        help="Userinfo claim matched (exactly, case-insensitively) against the "
             "Odoo login. Several matching users, or none, means refusal.")
    # Verification du client emetteur du jeton (aud/azp). Desactivee par
    # defaut : le point userinfo accepte le jeton de n'importe quel client du
    # meme fournisseur d'identite, et ne dit pas lequel l'a demande. La
    # verification lit donc les revendications du jeton d'acces lui-meme ; elle
    # suppose un jeton JWT et un client d'installation fixe.
    token_audience_check = fields.Boolean(
        string="Require install client in token",
        default=False,
        help="Refuse a bearer whose access token was not issued to the OIDC "
             "Client ID above (checked on the token's aud/azp claims). Needs "
             "the identity provider to issue JWT access tokens.")

    # --- Install block (consumed by the kickstart %pre script) ---
    locale = fields.Char(default="fr_CA.UTF-8", required=True)
    keymap = fields.Char(
        default="ca", required=True,
        help="Console keymap written to /etc/vconsole.conf (`localectl "
             "list-keymaps`). This does NOT set the desktop layout — see the "
             "XKB fields below.")
    timezone = fields.Char(
        default="America/Montreal", required=True,
        help="Fallback timezone. A user whose Odoo preference (Preferences > "
             "Timezone) is set overrides this for their own machines.")
    # The graphical session reads XKB, not the console keymap; provisioning one
    # without the other is what left installed machines on a US desktop layout
    # while the TTY was Canadian.
    x_layout = fields.Char(
        "XKB layout", default="ca",
        help="Desktop keyboard layout, e.g. 'ca' or 'ca,us' (`localectl "
             "list-x11-keymap-layouts`). Blank falls back to the console keymap.")
    x_variant = fields.Char(
        "XKB variant",
        help="Layout variant, e.g. 'multix' for the CSA Canadian Multilingual "
             "layout (`localectl list-x11-keymap-variants ca`). Blank = the "
             "layout's default variant.")
    x_options = fields.Char(
        "XKB options", default="grp:alt_shift_toggle",
        help="Comma-separated XKB options, e.g. 'grp:alt_shift_toggle' to cycle "
             "layouts with Alt+Shift.")
    hostname_pattern = fields.Char(
        default="bf-{username}",
        help="{username} is replaced with the provisioning user's login (local part).")
    root_mode = fields.Selection(
        [("locked", "Locked"), ("enabled", "Enabled")],
        default="locked", required=True)

    # --- Zero-touch install image + OIDC bootstrap (consumed by the public
    #     /blue-fox-install.ks kickstart served by bf_zerotouch_install, BEFORE
    #     the machine has any identity). These used to live in a separate
    #     bf.zerotouch.tenant model; they were folded in here so a tenant is one
    #     Policy row. The kickstart is fetched anonymously at GRUB time, so none
    #     of these are secret (image ref + public OIDC endpoints). ---
    slug = fields.Char(
        help="Short tenant id baked into the kickstart hostname seed + server "
             "logs, e.g. 'bf'. Falls back to the first label of the domain.")
    tenant_name = fields.Char(
        "Tenant Display Name",
        help="Human name baked into the kickstart comment + tenant.json (read by "
             "the firstboot welcome wizard). Falls back to the company name. Kept "
             "distinct because the OS label (e.g. 'Example Inc.') can differ from "
             "the Odoo company record name (e.g. 'Example').")
    oci_image_ref = fields.Char(
        "OCI Image",
        help="ghcr.io image the installer rebases to, e.g. "
             "ghcr.io/example/blue-fox-os:latest. Required to serve "
             "a zero-touch kickstart for this org; blank disables /blue-fox-install.ks.")
    addsupport = fields.Char(
        "Secondary Locale", default="en_CA.UTF-8",
        help="Secondary locale offered at install (kickstart lang --addsupport).")
    keyboard_x = fields.Char(
        "X Layouts", default="'ca','us'",
        help="X keyboard layouts for the installer (kickstart keyboard --xlayouts).")
    authentik_device_url = fields.Char(
        "Authentik Device URL",
        help="OIDC device-authorization endpoint the %pre device flow starts, e.g. "
             "https://auth.example.com/application/o/device/")
    authentik_token_url = fields.Char(
        "Authentik Token URL",
        help="OIDC token endpoint the %pre device flow polls, e.g. "
             "https://auth.example.com/application/o/token/")
    oidc_client_id = fields.Char(
        "OIDC Client ID", default="blue-fox-os",
        help="Public OIDC client id used by the install-time device flow.")

    # --- Nextcloud : identifiants obtenus en arriere-plan -----------------
    # Le %pre echange le jeton de l'operateur (RFC 8693) contre un jeton
    # destine au fournisseur Nextcloud, et s'en sert une fois pour frapper un
    # mot de passe d'application. Le premier demarrage n'a alors plus aucun
    # SSO a redemander a la personne qui vient d'autoriser l'installation.
    #
    # Les deux champs vides = degradation prevue, pas panne : l'agent
    # d'accueil reprend le parcours SSO par navigateur.
    nextcloud_url = fields.Char(
        "Nextcloud URL",
        help="Racine de l'instance Nextcloud du locataire, p. ex. "
             "https://nextcloud.example.com. Sert au %pre pour frapper le mot "
             "de passe d'application, et a l'agent pour le montage rclone.")
    nextcloud_oidc_client_id = fields.Char(
        "Nextcloud OIDC Client ID",
        help="client_id du fournisseur Nextcloud dans Authentik. Sert "
             "d'audience a l'echange de jetons RFC 8693. Ce n'est pas un "
             "secret. ⚠️ Le fournisseur CIBLE doit par ailleurs declarer "
             "federer avec le client d'installation cote Authentik "
             "(jwt_federation_providers), sinon l'echange est refuse avec "
             "invalid_target.")

    # --- Seat login (sssd against the Authentik LDAP outpost) ---
    login_mode = fields.Selection(
        [("local", "Local accounts"),
         ("sssd", "Authentik LDAP (sssd)")],
        default="sssd", required=True,
        help="'sssd' syncs the Linux seat login with Authentik via the LDAP outpost.")
    # Compte de secours local (bfos-secours).
    # ⚠️ DEFAUT A VRAI, et c'est voulu : une organisation qui n'a rien decide ne
    # doit pas perdre en silence un chemin de recuperation. Le poste traite
    # d'ailleurs une cle ABSENTE comme « oui ». Le couper est un choix explicite.
    break_glass_account = fields.Boolean(
        "Compte de secours local", default=True,
        help="Cree bfos-secours, ouvert sur la phrase du disque sequestree. "
             "Decoche : le compte est retire a l'installation. ⚠️ Sans lui, "
             "root verrouille et sssd seul, si l'annuaire est injoignable AVANT "
             "la premiere connexion, personne n'ouvre de session localement ; "
             "la recuperation passe alors par un demarrage de secours et la "
             "phrase LUKS sequestree. Ignore en mode local : le poste garde le "
             "compte plutot que de laisser une machine sans porte d'entree.")
    ldap_uri = fields.Char(string="LDAP outpost URI",
                           help="e.g. ldaps://ldap.example.com:636")
    ldap_base_dn = fields.Char(string="LDAP base DN",
                               help="e.g. dc=ldap,dc=goauthentik,dc=io")
    # --- Identite de liaison de l'annuaire ---------------------------------
    # L'avant-poste LDAP d'Authentik NE SERT PAS les recherches anonymes :
    # sans compte de service, sssd ne resout aucun utilisateur et la session
    # est refusee avant meme qu'un mot de passe soit demande. Mesure
    # sur un avant-poste reel, pas une lecture de documentation.
    ldap_bind_dn = fields.Char(
        string="LDAP bind DN",
        help="Compte de service qui fait les recherches d'annuaire, p. ex. "
             "cn=svc-bfos-ldap,ou=users,DC=example,DC=com. Chez Authentik, un "
             "jeton d'intention app_password sert de mot de passe.")
    # Chiffre au repos avec la MEME cle Fernet que le sequestre de disque : un
    # dump vole ne rend pas le compte de service de l'annuaire. La colonne est
    # sous groups= pour la meme raison que la phrase de passe — administrer
    # Odoo ne doit pas valoir « lire l'annuaire de tout le monde ».
    ldap_bind_password_enc = fields.Char(
        "Mot de passe de liaison (chiffre)", copy=False, readonly=True,
        groups="bf_policy.group_disk_escrow_read")
    # Champ de SAISIE, jamais stocke : il ne rend jamais la valeur, il ne fait
    # que la prendre. Un fields.Char stocke aurait une vraie colonne en clair,
    # et un TransientModel aussi (voir BfPolicyMachineReveal).
    ldap_bind_password = fields.Char(
        string="Mot de passe de liaison", store=False,
        compute="_compute_ldap_bind_password",
        inverse="_inverse_ldap_bind_password",
        help="Saisir pour remplacer. Laisser vide ne change rien : le champ "
             "n'affiche jamais la valeur en place.")

    def _compute_ldap_bind_password(self):
        # Ne jamais rendre la valeur : ce champ est une porte d'entree.
        for rec in self:
            rec.ldap_bind_password = ""

    def _inverse_ldap_bind_password(self):
        for rec in self:
            valeur = (rec.ldap_bind_password or "").strip()
            if not valeur:
                # Un formulaire renvoie toujours "" puisque le compute rend "".
                # Traiter ca comme un effacement viderait le mot de passe a
                # chaque enregistrement de la fiche.
                continue
            rec.sudo().ldap_bind_password_enc = escrow.encrypt(valeur)

    def _read_ldap_bind_password(self):
        """Rend le mot de passe de liaison en clair, ou "" s'il n'y en a pas.

        Sert la politique : le poste en a besoin pour ecrire sssd.conf. Ne
        leve pas — une cle de chiffrement absente ou une valeur illisible
        doivent laisser le reste de la politique partir, l'installateur ayant
        sa propre garde qui refuse bruyamment un mode sssd incomplet.
        """
        self.ensure_one()
        chiffre = self.sudo().ldap_bind_password_enc or ""
        if not chiffre:
            return ""
        try:
            return escrow.decrypt(chiffre)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "bf_policy: mot de passe de liaison LDAP illisible pour "
                "l'organisation %s : %s", self.id, exc)
            return ""

    # --- Policies ---
    offline_login = fields.Boolean(
        string="Allow offline login", default=True,
        help="Log in with the last-used password when offline (sssd credential cache). "
             "First login must be online to populate the cache.")
    offline_max_days = fields.Integer(
        string="Offline validity (days)", default=7,
        help="Days the cached password stays valid offline. 0 = never expires.")
    mfa_required = fields.Boolean(string="MFA required", default=True)
    auto_lock_minutes = fields.Integer(
        string="Auto-lock (minutes)", default=15,
        help="Idle minutes before the session locks. 0 = disabled.")

    # --- TPM2 auto-unlock (LUKS) ---
    # Enrolled at first boot by the welcome agent, not at install time:
    # systemd-cryptenroll needs the existing passphrase, which Anaconda never
    # stores in cleartext.
    tpm_autounlock = fields.Boolean(
        string="TPM2 auto-unlock",
        default=False,
        help="Bind the LUKS volume to the machine's TPM2 so the disk unlocks "
             "without typing the passphrase at boot. The passphrase is KEPT as "
             "a recovery method — enrolling the TPM never removes it.\n"
             "Trade-off: with no PIN, anyone who powers on the machine reaches "
             "the login screen with the disk already unlocked. At-rest "
             "protection then rests on the session password alone.")
    tpm_pcrs = fields.Char(
        string="TPM2 PCRs", default="7",
        help="Platform Configuration Registers the enrolment is sealed to. "
             "7 = Secure Boot state: survives kernel and firmware updates, "
             "breaks (falls back to passphrase) if Secure Boot is turned off. "
             "Adding 0 also seals to the firmware itself — safer, but a BIOS "
             "update then forces the passphrase.")

    # --- Disk passphrase escrow ---
    # The counterpart to tpm_autounlock above. The TPM removes the daily typing;
    # this removes the day the passphrase is lost and the TPM refuses to hand
    # the key back (Secure Boot off, firmware swap, dead board).
    disk_escrow = fields.Boolean(
        string="Escrow disk passphrase",
        default=False,
        help="Have the installer generate the LUKS passphrase and deposit it "
             "here, instead of a human typing one nobody records.\n"
             "Only ever written at install time, while the operator's bearer is "
             "in hand. Stored encrypted with a key that lives OUTSIDE the "
             "database, so a stolen dump does not open a single disk.\n"
             "Off by default, and inert on existing machines: a fleet installed "
             "before this was enabled keeps its typed passphrases.")

    # --- Session defaults ---
    accent_color = fields.Char(default="#29ABE2")
    wallpaper_url = fields.Char()
    clock_format = fields.Selection(
        CLOCK_FORMATS, string="Clock format", default="24h", required=True,
        help="Panel clock format on the org's machines. A person can override "
             "it on their own policy row.")
    mount_ids = fields.One2many("bf.policy.mount", "org_id", string="Default mounts")
    pwa_ids = fields.One2many("bf.policy.pwa", "org_id", string="Default PWAs")

    # --- Applications Flatpak ---
    # Ces deux listes se traduisent en /etc/bluebuild/default-flatpaks/system/
    # {install,remove} sur la machine. Le service system-flatpak-setup (root,
    # premier demarrage + minuterie) combine ainsi :
    #     (liste bakee dans l'image  −  /etc remove)  +  /etc install
    # D'ou une semantique d'AJOUT et de RETRAIT par-dessus la base de l'image,
    # sans code privilegie de notre cote : on ecrit deux fichiers texte.
    app_ids = fields.Many2many(
        "bf.policy.app", "bf_policy_org_app_install_rel", "org_id", "app_id",
        string="Default apps",
        help="Installees en plus de la base bakee dans l'image.")
    app_remove_ids = fields.Many2many(
        "bf.policy.app", "bf_policy_org_app_remove_rel", "org_id", "app_id",
        string="Apps to remove",
        help="Retirees de la base bakee dans l'image (Brave, Thunderbird, "
             "Nextcloud, Bitwarden). Le retrait l'emporte sur l'ajout.")

    # --- Extensions du navigateur ---
    # Imposees a Brave par politique (ExtensionInstallForcelist). Remplacent
    # Floccus, autrefois ecrit en dur par l'agent d'accueil.
    extension_ids = fields.Many2many(
        "bf.policy.extension", "bf_policy_org_extension_rel", "org_id", "extension_id",
        string="Browser extensions",
        help="Installees d'office dans Brave sur les postes de l'organisation. "
             "La personne ne peut pas les retirer.")

    _sql_constraints = [
        ("company_uniq", "unique(company_id)",
         "A policy record already exists for this company."),
    ]

    # ------------------------------------------------------------------ helpers
    @api.model
    def _resolve_for_host(self, host):
        """Find the active org whose domain matches a request Host (port
        stripped, case-insensitive). Returns a recordset (empty if no match).

        Mirrors bf.zerotouch.tenant._resolve_for_host so the policy plane routes
        by the same domain the operator typed at GRUB."""
        if not host:
            return self.browse()
        # X-Forwarded-Host can be a comma chain; keep the first, drop any port.
        h = host.split(",")[0].split(":")[0].strip().lower()
        if not h:
            return self.browse()
        # =ilike with no wildcards = exact case-insensitive match.
        return self.search([("domain", "=ilike", h)], limit=1)

    @api.model
    def _select_for_host(self, host):
        """Pick the org to serve /api/v1/policy/me for a request host.

        Returns ``(org, status)``:
          - ``(org, None)``      — a tenant to serve.
          - ``(empty, 404)``     — host matched nothing and several orgs exist;
                                    refuse rather than leak another tenant.
          - ``(empty, 503)``     — no org configured at all.

        With a single active org, that row is served regardless of host
        (single-tenant back-compat, the common case)."""
        org = self._resolve_for_host(host)
        if org:
            return org, None
        count = self.search_count([])
        if count == 1:
            return self.search([], limit=1), None
        if count == 0:
            return self.browse(), 503
        return self.browse(), 404

    @api.private
    def is_user_authorized(self, user) -> bool:
        """Whether `user` may provision a machine under this org's policy.

        Rejoue a chaque synchronisation d'un poste, pas seulement a
        l'installation. Les trois premiers refus valent dans TOUS les modes :
        « any » veut dire « tout usager interne de la societe », jamais « toute
        session Odoo ».
        """
        self.ensure_one()
        user = user.sudo().with_context(active_test=False)
        if not user or not user.active or user.share:
            return False
        if "company_ids" in user._fields and self.company_id not in user.company_ids:
            return False
        if self.provision_mode == "any":
            return True
        if self.provision_mode == "group":
            return bool(user.groups_id & self.provision_group_ids)
        if self.provision_mode == "allowlist":
            return user in self.provision_user_ids
        return False

    def _install_prefs(self, user, override) -> dict:
        """Resolve the install-block locale/keyboard/timezone for one user.

        Precedence is uniform — explicit per-user override, then the org default
        — with one addition: the timezone also consults the user's own Odoo
        preference (res.users.tz) before falling back to the org. That is the
        value the person already maintains for their calendar and reports, so
        making them restate it in a policy row would just invite drift. An
        explicit override still wins over it, which is what lets you pin a
        shared machine to a timezone that isn't its operator's.
        """
        self.ensure_one()

        def pick(field, fallback):
            value = (override[field] or "").strip() if override else ""
            return value or fallback

        return {
            "locale": pick("locale", self.locale),
            "keymap": pick("keymap", self.keymap),
            "timezone": pick("timezone", (user.tz or "").strip() or self.timezone),
            "x_layout": pick("x_layout", self.x_layout or ""),
            "x_variant": pick("x_variant", self.x_variant or ""),
            "x_options": pick("x_options", self.x_options or ""),
        }

    def _clock_for(self, user, override, timezone) -> dict:
        """The `session.clock` block.

        The second clock is not a setting: it appears exactly when the person's
        effective timezone differs from the org's, showing the org's. That is
        the situation it exists for (someone in Auckland working for a Montreal
        company), and a toggle would only let the two drift apart.
        """
        self.ensure_one()
        fmt = self.clock_format or "24h"
        if override and override.clock_format and override.clock_format != "inherit":
            fmt = override.clock_format
        org_tz = (self.timezone or "").strip()
        second = org_tz if org_tz and timezone and org_tz != timezone else None
        return {"format": fmt, "second_timezone": second}

    def _avatar_for(self, user) -> str:
        """The person's photo as base64, or "".

        The employee record first, since that is where HR keeps the photo; the
        user's own avatar otherwise. 256 px is plenty for a login-screen face
        and keeps the policy small (it is fetched by the installer too).
        """
        self.ensure_one()
        image = False
        if "hr.employee" in self.env:
            employees = self.env["hr.employee"].sudo().with_context(
                active_test=False).search([("user_id", "=", user.id)])
            employee = (employees.filtered(
                lambda e: e.company_id == self.company_id) or employees)[:1]
            image = employee.image_256 if employee else False
        image = image or user.sudo().image_256
        if not image:
            return ""
        if isinstance(image, bytes):
            image = image.decode("ascii")
        try:
            raw = base64.b64decode(image, validate=True)
        except (binascii.Error, ValueError):
            return ""
        if not raw.startswith(_AVATAR_SIGNATURES):
            return ""
        return image

    def _hostname_for(self, user) -> str:
        username = (user.login or "user").split("@")[0]
        return (self.hostname_pattern or "bf-{username}").replace("{username}", username)

    @staticmethod
    def _domain_of(company) -> str:
        site = (company.website or "").strip()
        for prefix in ("https://", "http://"):
            if site.startswith(prefix):
                site = site[len(prefix):]
        return site.strip("/")

    def _effective_domain(self) -> str:
        """The bare host this org routes on: explicit `domain`, else the
        company website with any leading www. stripped."""
        self.ensure_one()
        domain = (self.domain or self._domain_of(self.company_id)).strip()
        if domain.startswith("www."):
            domain = domain[len("www."):]
        return domain

    def _policy_url(self) -> str:
        """The /api/v1/policy/me URL baked into the kickstart, derived from the
        routing domain so it can never drift from where the policy is served."""
        self.ensure_one()
        domain = self._effective_domain()
        return f"https://{domain}/api/v1/policy/me" if domain else ""

    def _kickstart_vars(self) -> dict:
        """Map this org to the {{PLACEHOLDER}} dict the zero-touch kickstart
        template expects. The single source of truth that used to live in
        bf.zerotouch.tenant.to_template_vars(). The controller adds the computed
        GENERATED_AT / PROVISION_SCRIPT / APPLY_SCRIPT placeholders on top."""
        self.ensure_one()
        domain = self._effective_domain()
        slug = (self.slug or domain.split(".")[0] or "bf").strip()
        return {
            "TENANT_SLUG": slug,
            "TENANT_NAME": self.tenant_name or self.company_id.name or "",
            "TENANT_DOMAIN": domain,
            "OCI_IMAGE_REF": self.oci_image_ref or "",
            "LANG": self.locale or "",
            "ADDSUPPORT": self.addsupport or "",
            "KEYBOARD_VC": self.keymap or "",
            "KEYBOARD_X": self.keyboard_x or "",
            "TIMEZONE": self.timezone or "",
            "AUTHENTIK_DEVICE_URL": self.authentik_device_url or "",
            "AUTHENTIK_TOKEN_URL": self.authentik_token_url or "",
            "OIDC_CLIENT_ID": self.oidc_client_id or "",
            "POLICY_URL": self._policy_url(),
        }

    def _extensions_for(self, override) -> list[dict]:
        """Extensions imposees a la personne : org + ajouts, moins ses retraits.

        Meme regle de conflit que les applications : le retrait l'emporte.
        Une extension archivee ne part pas : l'ORM l'ecarte deja des many2many
        (verifie par essai et par mutation, un filtre `active` ici ne mordait pas).
        """
        self.ensure_one()
        skip = set(override.extension_remove_ids.ids) if override else set()
        chosen = self.extension_ids | (override.extension_ids if override else
                                       self.env["bf.policy.extension"])
        domain = self._effective_domain()
        return [ext._policy_entry(domain)
                for ext in chosen.sorted(lambda e: (e.name or "", e.extension_id))
                if ext.id not in skip]

    @api.private
    def get_policy_json(self, user) -> dict:
        """Merged org-defaults + per-user overrides payload for /api/v1/policy/me."""
        self.ensure_one()
        # Une politique en mode sssd sans annuaire ni compte de liaison produit
        # une machine qui n'ouvre AUCUNE session. On le dit dans le
        # journal plutot que par une contrainte : une contrainte casserait la
        # creation de toute organisation laissee au defaut, y compris dans les
        # tests. L'installateur, lui, a sa propre garde et echoue bruyamment.
        if self.login_mode == "sssd" and not (self.ldap_uri and self.ldap_bind_dn):
            _logger.warning(
                "bf_policy: organisation %s servie en login_mode=sssd sans %s — "
                "les machines installees avec cette politique n'ouvriront aucune "
                "session", self.id,
                "ldap_uri" if not self.ldap_uri else "ldap_bind_dn")
        override = self.env["bf.policy.user"].sudo().search(
            [("user_id", "=", user.id), ("company_id", "=", self.company_id.id)],
            limit=1)
        accent = (override.accent_color if override and override.accent_color
                  else self.accent_color)
        wallpaper = (override.wallpaper_url if override and override.wallpaper_url
                     else self.wallpaper_url)
        mounts = list(self.mount_ids) + (list(override.mount_ids) if override else [])
        pwas = list(self.pwa_ids) + (list(override.pwa_ids) if override else [])
        # Applications : org + usager, dedoublonnees en gardant l'ordre.
        # ⚠️ REGLE DE CONFLIT EXPLICITE : le RETRAIT L'EMPORTE. Une app a la fois
        # dans install et dans remove sort de la liste d'installation. Sans cette
        # regle, le resultat dependrait de l'ordre de lecture des deux fichiers
        # par system-flatpak-setup — c'est-a-dire d'un detail d'implementation
        # amont, ce qui est exactement le genre de comportement qu'on ne veut pas
        # avoir a deviner depuis Odoo.
        app_remove = list(dict.fromkeys(
            [a.flatpak_id for a in self.app_remove_ids]
            + ([a.flatpak_id for a in override.app_remove_ids] if override else [])))
        app_install = [
            fid for fid in dict.fromkeys(
                [a.flatpak_id for a in self.app_ids]
                + ([a.flatpak_id for a in override.app_ids] if override else []))
            if fid not in app_remove
        ]
        # TPM : la surcharge par personne est tri-etat, « inherit » retombe sur
        # la valeur de l'organisation.
        install_prefs = self._install_prefs(user, override)
        user_block = {
            "login": user.login,
            "email": user.email or user.login,
            "display_name": user.name,
        }
        avatar = self._avatar_for(user)
        if avatar:
            user_block["avatar"] = avatar
        tpm_override = override.tpm_autounlock_override if override else "inherit"
        if tpm_override == "on":
            tpm_enabled = True
        elif tpm_override == "off":
            tpm_enabled = False
        else:
            tpm_enabled = bool(self.tpm_autounlock)
        return {
            "schema": "bf-policy/v2",
            "org": {
                "company": self.company_id.name,
                # Prefer the explicit routing domain; fall back to the website.
                "domain": self.domain or self._domain_of(self.company_id),
            },
            "user": user_block,
            "install": {
                **install_prefs,
                "hostname": self._hostname_for(user),
                "root": self.root_mode,
                "login": {
                    "mode": self.login_mode,
                    "break_glass": bool(self.break_glass_account),
                    "ldap_uri": self.ldap_uri or "",
                    "ldap_base_dn": self.ldap_base_dn or "",
                    "bind_dn": self.ldap_bind_dn or "",
                    # ⚠️ En clair dans la reponse, et il n'y a pas d'autre
                    # facon : le poste doit l'ecrire dans son sssd.conf. La
                    # reponse part en HTTPS a un porteur de jeton deja
                    # authentifie, et chaque machine du parc detient de toute
                    # facon cette valeur dans un fichier 0600.
                    # Servi SEULEMENT en mode sssd (18.0.2.11.0) : un poste en
                    # comptes locaux n'ecrit pas de sssd.conf et n'a aucune
                    # raison de recevoir le compte de service de l'annuaire.
                    "bind_password": (self._read_ldap_bind_password()
                                      if self.login_mode == "sssd" else ""),
                },
            },
            "policies": {
                "offline_login": {
                    "enabled": bool(self.offline_login),
                    "max_offline_days": self.offline_max_days,
                },
                "mfa_required": bool(self.mfa_required),
                "auto_lock_minutes": self.auto_lock_minutes,
                "tpm_autounlock": {
                    "enabled": tpm_enabled,
                    "pcrs": self.tpm_pcrs or "7",
                    # Toujours vrai : `systemd-cryptenroll --tpm2-device` AJOUTE
                    # un keyslot, il n'enleve jamais la phrase de passe. Le champ
                    # est expose pour que l'agent puisse le dire a l'utilisateur.
                    "fallback_passphrase": True,
                },
                # Lu par le %pre AVANT le partitionnement : c'est lui qui decide
                # si l'installateur tire la phrase de passe lui-meme ou la fait
                # taper. `available` dit si ce serveur-ci saurait la ranger —
                # sans cle configuree, le %pre ne doit meme pas essayer.
                "disk_escrow": {
                    "enabled": bool(self.disk_escrow),
                    "available": bool(self.disk_escrow) and escrow.available(),
                },
            },
            "session": {
                "accent_color": accent,
                "wallpaper_url": wallpaper or "",
                "mounts": [{"name": m.name, "remote_path": m.remote_path,
                            "mount_point": m.mount_point} for m in mounts],
                "pwas": [{"name": p.name, "url": p.url, "pinned": bool(p.pinned)}
                         for p in pwas],
                "clock": self._clock_for(user, override, install_prefs["timezone"]),
            },
            # Services du locataire. Lu par le %pre AVANT le premier
            # demarrage : c'est la que se decide si l'installation frappe
            # elle-meme le mot de passe d'application Nextcloud ou si
            # l'agent devra redemander un SSO. Bloc additif, comme "apps".
            "services": {
                "nextcloud": {
                    "url": self.nextcloud_url or "",
                    "oidc_client_id": self.nextcloud_oidc_client_id or "",
                },
            },
            # Bloc additif : le schema bf-policy/v2 n'interdit pas les cles
            # supplementaires, donc pas de bump de version — un agent d'avant
            # cette version l'ignore simplement et n'installe rien de plus.
            "apps": {
                "install": app_install,
                "remove": app_remove,
            },
            # Bloc additif, meme regle que "apps" : un agent d'avant l'ignore.
            "browser": {
                "extensions": self._extensions_for(override),
            },
            "generated_at": fields.Datetime.now().isoformat() + "Z",
        }


class BfPolicyUser(models.Model):
    _name = "bf.policy.user"
    _description = "Blue Fox Policy — Per-User Overrides"
    _rec_name = "user_id"

    user_id = fields.Many2one("res.users", required=True, ondelete="cascade")
    company_id = fields.Many2one(
        "res.company", required=True, ondelete="cascade",
        default=lambda self: self.env.company)
    accent_color = fields.Char(help="Overrides the org default accent if set.")
    wallpaper_url = fields.Char(help="Overrides the org default wallpaper if set.")
    # Install-block overrides. All blank by default: a user with no override row
    # provisions exactly as before. Timezone is the exception that also has a
    # non-policy source — see BfPolicyOrg._install_prefs.
    locale = fields.Char(help="Overrides the org default locale if set.")
    keymap = fields.Char(help="Overrides the org default console keymap if set.")
    timezone = fields.Char(
        help="Overrides both the org default AND the user's Odoo timezone "
             "preference. Leave blank to let the Odoo preference win.")
    x_layout = fields.Char("XKB layout",
                           help="Overrides the org default desktop layout if set.")
    x_variant = fields.Char("XKB variant",
                            help="Overrides the org default layout variant if set.")
    x_options = fields.Char("XKB options",
                            help="Overrides the org default XKB options if set.")
    # Tri-state on purpose : a plain Boolean cannot distinguish "inherit the
    # org default" from "explicitly off", and TPM auto-unlock is exactly the
    # kind of setting one wants to disable for a single shared machine.
    tpm_autounlock_override = fields.Selection(
        [("inherit", "Inherit from org"), ("on", "Force on"), ("off", "Force off")],
        string="TPM2 auto-unlock", default="inherit", required=True)
    clock_format = fields.Selection(
        [("inherit", "Inherit from org")] + CLOCK_FORMATS,
        string="Clock format", default="inherit", required=True)
    mount_ids = fields.One2many("bf.policy.mount", "user_id", string="Extra mounts")
    pwa_ids = fields.One2many("bf.policy.pwa", "user_id", string="Extra PWAs")
    # Le retrait par personne a un sens ici : une machine BFOS est un poste
    # nominatif (hostname bf-{username}), pas un poste partage.
    app_ids = fields.Many2many(
        "bf.policy.app", "bf_policy_user_app_install_rel", "user_id", "app_id",
        string="Extra apps")
    app_remove_ids = fields.Many2many(
        "bf.policy.app", "bf_policy_user_app_remove_rel", "user_id", "app_id",
        string="Apps to remove")
    extension_ids = fields.Many2many(
        "bf.policy.extension", "bf_policy_user_extension_rel", "user_id", "extension_id",
        string="Extra browser extensions")
    extension_remove_ids = fields.Many2many(
        "bf.policy.extension", "bf_policy_user_extension_remove_rel", "user_id",
        "extension_id", string="Browser extensions to skip",
        help="Extensions de l'organisation qui ne s'imposent pas a cette personne.")

    _sql_constraints = [
        ("user_company_uniq", "unique(user_id, company_id)",
         "This user already has an override record for this company."),
    ]


class BfPolicyMount(models.Model):
    _name = "bf.policy.mount"
    _description = "Blue Fox Policy — Nextcloud/WebDAV Mount"

    name = fields.Char(required=True)
    remote_path = fields.Char(
        required=True, default="/remote.php/dav/files/",
        help="WebDAV path on the Nextcloud server.")
    mount_point = fields.Char(required=True, default="~/Nextcloud")
    org_id = fields.Many2one("bf.policy.org", ondelete="cascade")
    user_id = fields.Many2one("bf.policy.user", ondelete="cascade")

    @api.constrains("org_id", "user_id")
    def _check_owner(self):
        for rec in self:
            if bool(rec.org_id) == bool(rec.user_id):
                from odoo.exceptions import ValidationError
                raise ValidationError(
                    "A mount must belong to exactly one of an org or a user.")


class BfPolicyPwa(models.Model):
    _name = "bf.policy.pwa"
    _description = "Blue Fox Policy — Progressive Web App"

    name = fields.Char(required=True)
    url = fields.Char(required=True)
    pinned = fields.Boolean(default=False)
    org_id = fields.Many2one("bf.policy.org", ondelete="cascade")
    user_id = fields.Many2one("bf.policy.user", ondelete="cascade")

    @api.constrains("org_id", "user_id")
    def _check_owner(self):
        for rec in self:
            if bool(rec.org_id) == bool(rec.user_id):
                from odoo.exceptions import ValidationError
                raise ValidationError(
                    "A PWA must belong to exactly one of an org or a user.")


class BfPolicyApp(models.Model):
    """Catalogue d'applications Flatpak selectionnables dans la politique.

    Pourquoi un catalogue plutot qu'un champ texte : la politique est un ecran
    client. Choisir « Brave » dans une liste est verifiable ; taper
    « com.brave.Browser » a la main ne l'est pas, et une coquille ne se voit
    qu'au premier demarrage d'une machine, quand l'app n'apparait pas.

    Le catalogue couvre tout Flathub (~3 300 applications de bureau), mais la
    vue de selection filtre par defaut sur `recommended` — la courte liste
    approuvee par Blue Fox. Retirer le filtre donne acces au reste. Un ID absent
    du catalogue peut aussi etre cree a la volee : `flatpak_id` est le champ
    d'affichage, donc la creation rapide depuis le champ Many2many prend l'ID.
    """

    _name = "bf.policy.app"
    _description = "Blue Fox Policy — Flatpak Application (Flathub)"
    _order = "recommended desc, name, flatpak_id"
    _rec_name = "flatpak_id"

    flatpak_id = fields.Char(
        required=True, index=True, string="Flatpak ID",
        help="Identifiant Flathub, ex. com.brave.Browser.")
    name = fields.Char(help="Nom lisible, renseigne par la synchronisation Flathub.")
    summary = fields.Char()
    categories = fields.Char(
        help="Categories AppStream, separees par des virgules (Network, Office…).")
    recommended = fields.Boolean(
        index=True, default=False, string="Recommandee BF",
        help="Fait partie de la courte liste approuvee. C'est le filtre par "
             "defaut des vues de selection ; il peut etre retire pour choisir "
             "n'importe quelle application de Flathub.")
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("flatpak_id_uniq", "unique(flatpak_id)",
         "Cette application est deja au catalogue."),
    ]

    @api.depends("name", "flatpak_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = (
                f"{rec.name} ({rec.flatpak_id})" if rec.name else (rec.flatpak_id or ""))

    @api.constrains("flatpak_id")
    def _check_flatpak_id(self):
        # Un ID Flatpak est un nom inverse de domaine. On refuse tot ce qui n'en
        # a pas la forme : sinon l'erreur se manifeste sur la machine, au
        # premier demarrage, sous forme d'app silencieusement absente.
        #
        # ⚠️ Un label PEUT commencer par « _ » : Flatpak l'exige quand le segment
        # de domaine commence par un chiffre, ce qu'un nom de type D-Bus
        # interdit. 14 identifiants reels de Flathub en dependent
        # (ca._0ldsk00l.Nestopia, com.github._4lex4.ScanTailor-Advanced…). Motif
        # valide contre les 3 269 identifiants du catalogue : 0 rejet. Ne pas le
        # resserrer sans rejouer cette verification — c'est la 3e version, les
        # deux premieres ayant ete inventees plutot que derivees des donnees.
        import re as _re
        for rec in self:
            fid = (rec.flatpak_id or "").strip()
            if not _re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)+", fid):
                from odoo.exceptions import ValidationError
                raise ValidationError(
                    f"« {fid} » n'a pas la forme d'un ID Flatpak "
                    "(nom de domaine inverse, ex. com.brave.Browser).")

    # --- Synchronisation Flathub ------------------------------------------
    # Volontairement MANUELLE (bouton), pas un cron. Un cron hebdomadaire qui
    # echoue en silence laisserait un catalogue perime sans que personne ne le
    # sache ; ici, la personne qui rafraichit voit le resultat.
    FLATHUB_APPSTREAM_URL = "https://dl.flathub.org/repo/appstream/x86_64/appstream.xml.gz"

    @api.model
    def _fetch_flathub_appstream(self) -> bytes:
        """Le telechargement, isole pour que la lecture soit testable sans reseau."""
        import urllib.request

        req = urllib.request.Request(
            self.FLATHUB_APPSTREAM_URL, headers={"User-Agent": "bf_policy/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    @api.model
    def _parse_appstream(self, raw_gz: bytes) -> list[dict]:
        """Lit le catalogue AppStream gzippe et rend une entree par application.

        Un seul telechargement (~10 Mo) couvre ~3 300 applications, au lieu d'un
        appel par application sur /api/v2/appstream/<id>.
        """
        import gzip
        import io
        import xml.etree.ElementTree as ET

        root = ET.parse(io.BytesIO(gzip.decompress(raw_gz))).getroot()
        entries, seen = [], set()
        for comp in root.findall("component"):
            if comp.get("type") not in ("desktop", "desktop-application"):
                continue
            # ⚠️ NE PAS retirer un suffixe « .desktop » : sur Flathub, l'id du
            # composant EST l'id Flatpak, et certaines applications le portent
            # legitimement — app.organicmaps.desktop en est une. Le retirer
            # fabriquait un id inexistant, que la contrainte de forme rejetait
            # ensuite.
            fid = (comp.findtext("id") or "").strip()
            if not fid or fid in seen:
                continue
            seen.add(fid)
            entries.append({
                "flatpak_id": fid,
                "name": (comp.findtext("name") or "").strip() or False,
                "summary": (comp.findtext("summary") or "").strip() or False,
                "categories": ",".join(
                    x.text for x in comp.findall("categories/category") if x.text) or False,
            })
        return entries

    @api.model
    def _upsert_catalogue(self, entries: list[dict]) -> tuple[int, int]:
        """Cree ou met a jour les entrees. Ne touche NI `recommended` NI `active` :
        ce sont des choix de Blue Fox, pas des donnees de Flathub — une
        synchronisation ne doit pas effacer la courte liste approuvee."""
        created = updated = 0
        for entry in entries:
            vals = {k: v for k, v in entry.items() if k != "flatpak_id"}
            existing = self.search([("flatpak_id", "=", entry["flatpak_id"])], limit=1)
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(entry)
                created += 1
        return created, updated

    @api.private
    @api.model
    def action_sync_flathub(self):
        """Rafraichit le catalogue depuis Flathub (bouton, pas de cron)."""
        entries = self._parse_appstream(self._fetch_flathub_appstream())
        created, updated = self._upsert_catalogue(entries)
        _logger.info(
            "bf.policy.app: synchronisation Flathub — %s creees, %s mises a jour, "
            "%s applications vues", created, updated, len(entries))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": "Catalogue Flathub synchronise",
                "message": f"{created} ajoutee(s), {updated} mise(s) a jour "
                           f"({len(entries)} applications vues).",
                "sticky": False,
            },
        }


class BfPolicyMachine(models.Model):
    """Un poste installe, enrole a l'installation pour pouvoir se
    re-synchroniser tout seul par la suite.

    POURQUOI CE MODELE EXISTE
    Jusqu'ici, une machine recevait sa politique UNE fois : pendant le %pre de
    l'installation, avec le porteur OIDC de la personne qui installait. Ce
    jeton n'etait pas conserve — a raison. Consequence : modifier la politique
    dans Odoo ne changeait rien sur les postes deja installes ; il fallait
    reinstaller. L'enrolement donne a la machine une identite a elle, distincte
    de celle de son operateur.

    CE QUE LE JETON PERMET, ET RIEN DE PLUS
    Lire la politique fusionnee de SON usager, sur SON organisation. Il n'ouvre
    aucune session Odoo, ne porte aucun droit d'ecriture, et n'est utilisable
    que sur /api/v1/policy/machine. C'est la difference avec un jeton de
    rafraichissement Authentik, qui aurait mis un credential d'USAGER sur
    chaque disque.

    CE QU'ON STOCKE
    Le sha256 du secret, jamais le secret. Personne — pas meme un
    administrateur Odoo — ne peut relire le jeton d'une machine deja enrolee :
    il n'est rendu qu'une fois, dans la reponse a l'enrolement. Un secret de
    256 bits n'a pas besoin de sel ni d'iterations : il n'y a pas de dictionnaire
    a lui opposer.

    REVOCATION
    Archiver la fiche (bouton Revoquer). La machine tombe alors en 401 a la
    synchronisation suivante et garde sa derniere politique connue — un poste
    revoque ne se retrouve pas sans configuration.
    """

    _name = "bf.policy.machine"
    _description = "Blue Fox Policy — Machine enrolee"
    _order = "last_seen desc, id desc"
    _rec_name = "hostname"

    hostname = fields.Char(
        required=True, index=True,
        help="Nom d'hote applique par la politique au moment de l'installation.")
    machine_uuid = fields.Char(
        "UUID machine", required=True, index=True, copy=False, readonly=True,
        help="Identifiant tire au sort par la machine a l'installation. Une "
             "reinstallation en tire un nouveau : les deux fiches coexistent, "
             "et c'est `Vue le` qui dit laquelle est encore vivante.")
    org_id = fields.Many2one(
        "bf.policy.org", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", required=True, ondelete="cascade", index=True,
        help="Personne dont la politique fusionnee est servie a ce poste.")
    # groups= : le hash n'a rien a faire dans une exportation ou un rapport.
    token_hash = fields.Char(
        "Empreinte du jeton", required=True, index=True, copy=False,
        readonly=True, groups="base.group_system")
    enrolled_on = fields.Datetime(
        "Enrole le", default=fields.Datetime.now, readonly=True)
    last_seen = fields.Datetime("Vue le", readonly=True)
    last_seen_ip = fields.Char("Derniere IP", readonly=True)
    os_version = fields.Char("Version de l'image", readonly=True)
    sync_count = fields.Integer("Synchronisations", default=0, readonly=True)
    active = fields.Boolean(default=True)

    # --- Sequestre de la phrase de passe du disque -------------------------
    # groups= : meme un administrateur Odoo ne lit pas cette colonne sans le
    # droit dedie. C'est la seule facon d'avoir un sequestre ET une separation
    # des roles ; sinon « qui peut administrer Odoo » devient « qui peut ouvrir
    # tous les disques du parc », ce que personne n'a jamais decide.
    disk_passphrase_enc = fields.Char(
        "Phrase de passe (chiffree)", copy=False, readonly=True,
        groups="bf_policy.group_disk_escrow_read")
    # PAS de groups= ici, a dessein : savoir QU'UNE cle existe n'est pas la
    # lire, et l'exploitation courante a besoin de le voir pour reperer les
    # postes non couverts.
    disk_escrowed_on = fields.Datetime("Sequestre le", readonly=True, copy=False)
    disk_escrowed = fields.Boolean(
        "Phrase sequestree", compute="_compute_disk_escrowed", store=False,
        help="Une phrase de passe de disque est en depot pour cette machine.")
    disk_reveal_count = fields.Integer(
        "Revelations", default=0, readonly=True, copy=False)
    disk_last_revealed_on = fields.Datetime(
        "Derniere revelation", readonly=True, copy=False)
    disk_last_revealed_by = fields.Many2one(
        "res.users", string="Revelee par", readonly=True, copy=False)

    # depend de disk_escrowed_on et NON du champ chiffre : un compute qui lit
    # un champ sous groups= leve AccessError pour tout le monde sauf les
    # porteurs du droit, y compris dans une simple liste.
    @api.depends("disk_escrowed_on")
    def _compute_disk_escrowed(self):
        for rec in self:
            rec.disk_escrowed = bool(rec.disk_escrowed_on)

    _sql_constraints = [
        ("machine_uuid_uniq", "unique(machine_uuid)",
         "Cette machine est deja enrolee."),
    ]

    # ------------------------------------------------------------------ jeton
    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256((token or "").encode()).hexdigest()

    @api.model
    def _enrol(self, org, user, machine_uuid, hostname, os_version=""):
        """Enrole une machine et rend ``(machine, token)``.

        Le jeton en clair n'est rendu qu'ici. Un ré-enrolement avec le meme
        UUID (meme machine qui rejoue son installation) fait tourner le jeton
        sur la fiche existante plutot que d'en empiler une deuxieme.

        ⚠️ Un UUID dont la fiche a ete ARCHIVEE est refuse (``None, None``) :
        sans ca, revoquer une machine ne servirait a rien, puisqu'il lui
        suffirait de rejouer son enrolement pour se redonner un jeton.

        ⚠️ Un UUID deja enrole pour un AUTRE usager ou une AUTRE organisation
        leve EnrolConflict (18.0.2.11.0). L'UUID n'est pas un secret : il se
        lit sur la fiche et dans les journaux, donc le connaitre ne permet ni
        de faire tourner le jeton d'un poste d'autrui ni de se le faire
        reassigner. Seuls le meme usager et la meme organisation rejouent un
        enrolement.
        """
        uuid = (machine_uuid or "").strip()
        if not uuid:
            return None, None
        # active_test=False : une fiche archivee est invisible d'une recherche
        # ordinaire, ce qui ferait passer une machine revoquee pour inconnue.
        existing = self.with_context(active_test=False).search(
            [("machine_uuid", "=", uuid)], limit=1)
        if existing and not existing.active:
            _logger.warning(
                "[bf_policy] enrolement refuse : machine %s revoquee", uuid)
            return None, None
        if existing and (existing.user_id != user or existing.org_id != org):
            _logger.warning(
                "[bf_policy] enrolement refuse : machine %s deja enrolee pour "
                "un autre usager ou une autre organisation", uuid)
            raise EnrolConflict(uuid)

        token = secrets.token_urlsafe(32)
        vals = {
            "hostname": (hostname or "").strip() or "blue-fox-os",
            "org_id": org.id,
            "user_id": user.id,
            "token_hash": self._hash_token(token),
            "os_version": (os_version or "").strip(),
            "enrolled_on": fields.Datetime.now(),
        }
        if existing:
            existing.write(vals)
            machine = existing
        else:
            machine = self.create(dict(vals, machine_uuid=uuid))
        _logger.info("[bf_policy] machine enrolee : %s (%s) pour %s",
                     machine.hostname, uuid, user.login)
        return machine, token

    @api.model
    def _authenticate(self, token: str):
        """Retrouve la machine active portant ce jeton (recordset vide sinon)."""
        if not token:
            return self.browse()
        return self.search(
            [("token_hash", "=", self._hash_token(token))], limit=1)

    def _touch(self, client_ip=""):
        """Marque une synchronisation reussie. Best-effort : une machine ne doit
        pas se voir refuser sa politique parce que la statistique a echoue."""
        self.ensure_one()
        try:
            self.sudo().write({
                "last_seen": fields.Datetime.now(),
                "last_seen_ip": (client_ip or "")[:64],
                "sync_count": self.sync_count + 1,
            })
        except Exception:  # noqa: BLE001
            _logger.exception("[bf_policy] maj de last_seen impossible")

    # ------------------------------------------------------------------ vues
    def action_revoke(self):
        """Bouton « Revoquer » : la machine perd le droit de re-tirer sa
        politique des la prochaine minuterie, et ne peut pas se re-enroler."""
        for rec in self:
            rec.active = False
            _logger.warning("[bf_policy] machine revoquee : %s (%s)",
                            rec.hostname, rec.machine_uuid)
        return True

    # -------------------------------------------------------------- sequestre
    @api.model
    def _valid_passphrase(self, passphrase):
        """Une phrase de passe plausible pour LUKS, et rien d'autre.

        On refuse tout ce qui n'est pas de l'ASCII imprimable : cryptsetup lit
        des octets bruts, et une phrase contenant un saut de ligne ou un
        caractere non ASCII serait retapee differemment le jour ou elle sert —
        c'est-a-dire au pire moment.
        """
        if not isinstance(passphrase, str):
            return False
        if not 8 <= len(passphrase) <= 256:
            return False
        return all(0x20 <= ord(c) <= 0x7E for c in passphrase)

    def _escrow_disk_passphrase(self, passphrase):
        """Range la phrase de passe de ce poste. Rend ``(ok, raison)``.

        Appele en sudo depuis l'enrolement. Ne leve jamais : l'installateur doit
        pouvoir degrader vers la saisie manuelle, et un sequestre rate ne doit
        pas faire echouer un enrolement par ailleurs valide.

        ⚠️ Une phrase VIDE ne remet pas le champ a zero. Une re-synchronisation
        ou un ré-enrolement sans phrase ne doit pas effacer un depot existant :
        oublier une cle par omission serait un defaut silencieux, et le seul
        chemin qui efface est le bouton dedie.

        ⚠️ Un depot EXISTANT n'est jamais remplace (18.0.2.11.0), meme par le
        proprietaire du poste. On rend ``(False, raison)`` : l'installateur,
        qui ne se sert de la phrase tiree que si le depot est confirme,
        retombe alors sur la saisie manuelle. Une reinstallation normale tire
        un nouvel UUID, donc une nouvelle fiche, et n'est pas concernee ; seul
        un enrolement rejoue sous le meme UUID l'est. Remplacer volontairement
        un depot passe par « Retirer le sequestre », qui exige le droit dedie
        et laisse une trace.
        """
        self.ensure_one()
        if not passphrase:
            return False, "aucune phrase fournie"
        if self.sudo().disk_passphrase_enc:
            _logger.warning(
                "[bf_policy] depot refuse pour %s : une phrase est deja en "
                "sequestre", self.machine_uuid)
            return False, "une phrase est deja en depot pour ce poste"
        if not self._valid_passphrase(passphrase):
            return False, "phrase de passe refusee (longueur ou caracteres)"
        try:
            ciphertext = escrow.encrypt(passphrase)
        except escrow.EscrowUnavailable as exc:
            _logger.warning(
                "[bf_policy] sequestre impossible pour %s : %s",
                self.machine_uuid, exc)
            return False, str(exc)
        except Exception:  # noqa: BLE001
            _logger.exception("[bf_policy] chiffrement du sequestre en echec")
            return False, "echec du chiffrement"
        self.write({
            "disk_passphrase_enc": ciphertext,
            "disk_escrowed_on": fields.Datetime.now(),
        })
        _logger.info("[bf_policy] phrase de passe sequestree pour %s (%s)",
                     self.hostname, self.machine_uuid)
        return True, ""

    def _read_disk_passphrase(self):
        """Dechiffre, sans journaliser ni compter. Reserve a l'action ci-dessous."""
        self.ensure_one()
        return escrow.decrypt(self.sudo().disk_passphrase_enc or "")

    def action_reveal_passphrase(self):
        """Bouton « Reveler » : rend la phrase, et laisse une trace durable.

        POURQUOI UN BOUTON ET PAS UN CHAMP AFFICHE
        Journaliser la LECTURE d'un champ oblige a surcharger `_read_format`, le
        seul point ou toutes les lectures passent — et une surcharge qui ECRIT
        pendant une lecture se declenche aussi sur les listes, ou elle finit en
        AccessError. Un bouton explicite donne le meme audit sans toucher au
        chemin de lecture, et il dit a la personne qu'elle est en train de faire
        quelque chose de consequent.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_policy.group_disk_escrow_read"):
            raise AccessError(_(
                "Il faut le droit « Peut reveler les phrases de passe de "
                "disque » pour lire un sequestre."))
        if not self.disk_escrowed_on:
            raise UserError(_(
                "Aucune phrase de passe n'est en depot pour ce poste. Il a ete "
                "installe avant l'activation du sequestre, ou le depot a "
                "echoue et la phrase a ete tapee a la main."))
        try:
            passphrase = self._read_disk_passphrase()
        except escrow.EscrowUnavailable as exc:
            raise UserError(_(
                "Le sequestre n'est pas configure sur ce serveur : %s", exc))
        except Exception as exc:  # noqa: BLE001 — cle changee, donnee abimee
            raise UserError(_(
                "Impossible de dechiffrer ce depot (%s). La cle de sequestre "
                "a-t-elle change depuis l'installation de ce poste ?", exc))

        # Trace durable AVANT de rendre la valeur : si l'ecriture echoue, la
        # phrase ne sort pas. Un audit qu'on peut contourner en faisant planter
        # l'ecriture n'est pas un audit.
        self.sudo().write({
            "disk_reveal_count": self.disk_reveal_count + 1,
            "disk_last_revealed_on": fields.Datetime.now(),
            "disk_last_revealed_by": self.env.user.id,
        })
        _logger.warning(
            "[bf_policy] phrase de passe revelee : machine=%s (%s) par %s",
            self.hostname, self.machine_uuid, self.env.user.login)

        # ⚠️ On ne passe PAS la phrase au wizard : elle serait alors ecrite en
        # clair dans une colonne PostgreSQL. Voir BfPolicyMachineReveal.
        # `passphrase` a servi ici a prouver que le dechiffrement aboutit avant
        # de compter la revelation ; c'est tout ce qu'on lui demande.
        del passphrase
        # sudo + contexte : le groupe dedie n'a pas le droit de creer ces
        # ecrans lui-meme ; seul ce bouton en cree, donc toute lecture est
        # comptee.
        # create_uid reste l'usager courant : sudo() ne change pas env.uid.
        wizard = self.env["bf.policy.machine.reveal"].sudo().with_context(
            **{_REVEAL_CTX: True}).create({"machine_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "name": _("Phrase de passe du disque"),
            "res_model": "bf.policy.machine.reveal",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_forget_passphrase(self):
        """Retire le depot. Le seul chemin qui efface une phrase sequestree.

        Sert quand un poste est detruit ou son disque reinitialise : garder la
        cle d'un disque qui n'existe plus n'a que des inconvenients.
        """
        self.ensure_one()
        if not self.env.user.has_group("bf_policy.group_disk_escrow_read"):
            raise AccessError(_(
                "Il faut le droit « Peut reveler les phrases de passe de "
                "disque » pour retirer un sequestre."))
        self.sudo().write({
            "disk_passphrase_enc": False,
            "disk_escrowed_on": False,
        })
        _logger.warning(
            "[bf_policy] sequestre retire : machine=%s (%s) par %s",
            self.hostname, self.machine_uuid, self.env.user.login)
        return True


# Cle de contexte posee par action_reveal_passphrase, et par lui seul. Le
# contexte se transmet par RPC ; c'est pourquoi create() exige AUSSI le mode
# superutilisateur, qu'aucun appel distant ne peut obtenir.
_REVEAL_CTX = "bf_policy_reveal_from_button"
# Duree pendant laquelle un ecran de revelation rend la phrase a la personne
# qui l'a ouvert. Au-dela, il faut reappuyer sur le bouton, donc etre recompte.
REVEAL_TTL_SECONDS = 300


class BfPolicyMachineReveal(models.TransientModel):
    """Ecran jetable qui affiche une phrase de passe sequestree.

    ⚠️ POURQUOI `passphrase` EST CALCULE ET NON STOCKE
    « Transitoire » ne veut pas dire « pas en base ». Un TransientModel a une
    VRAIE table PostgreSQL, et un `fields.Char` ordinaire y est une VRAIE
    colonne : la phrase de passe de chiffrement integral d'un poste y serait
    ecrite en clair. Le menage automatique ne rattrape pas ca : il est pilote
    par le cron `base.autovacuum_job`, qui passe une fois par jour, avec un age
    minimum d'une heure. Une revelation faite le soir survit donc jusqu'au
    lendemain — et la sauvegarde nocturne (`pg_dump` complet, pousse hors site)
    tombe
    dans cette fenetre. La phrase serait sortie du serveur, en clair, dans
    exactement le fichier que `models/escrow.py` s'emploie a rendre inutile.

    Calcule et non stocke : la table ne porte qu'un `machine_id`, et le clair
    ne vit que le temps de la reponse JSON-RPC qui peint l'ecran.

    ⚠️ SEUL LE BOUTON CREE CES ECRANS (18.0.2.11.0)
    Le droit d'acces du groupe dedie est en lecture seule, create() exige le
    superutilisateur et le contexte du bouton, et la phrase n'est rendue qu'a
    la personne qui a ouvert l'ecran (create_uid), pendant REVEAL_TTL_SECONDS.
    Toute lecture passe donc par le compteur et par « revelee par ».
    """

    _name = "bf.policy.machine.reveal"
    _description = "Blue Fox Policy — Reveler une phrase de passe"

    machine_id = fields.Many2one("bf.policy.machine", readonly=True)
    passphrase = fields.Char("Phrase de passe", readonly=True,
                             compute="_compute_passphrase", store=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not (self.env.su and self.env.context.get(_REVEAL_CTX)):
            raise AccessError(_(
                "Une phrase de passe de disque ne se revele que par le bouton "
                "« Reveler la phrase de passe » de la fiche du poste."))
        return super().create(vals_list)

    @api.depends("machine_id")
    @api.depends_context("uid")
    def _compute_passphrase(self):
        """Dechiffre a l'affichage, et re-controle le droit a chaque lecture.

        Le controle est refait ici plutot que d'etre suppose acquis au bouton :
        l'id d'un wizard transitoire est devinable, et rien n'empeche quelqu'un
        d'autre de lire l'enregistrement. Sans ce garde-fou, retirer le droit a
        quelqu'un ne lui retirerait pas les ecrans deja ouverts.
        """
        allowed = self.env.user.has_group("bf_policy.group_disk_escrow_read")
        limite = fields.Datetime.now() - timedelta(seconds=REVEAL_TTL_SECONDS)
        for wizard in self:
            ouvert = wizard.sudo()
            if (not allowed or not ouvert.machine_id
                    or ouvert.create_uid != self.env.user
                    or not ouvert.create_date or ouvert.create_date < limite):
                wizard.passphrase = False
                continue
            try:
                wizard.passphrase = ouvert.machine_id._read_disk_passphrase()
            except Exception:  # noqa: BLE001 — cle absente, changee, abimee
                # Le bouton a deja explique l'echec avec sa cause exacte. Ici,
                # on ne peut que rendre un champ vide : lever pendant un calcul
                # se declencherait aussi a l'ouverture de la liste.
                wizard.passphrase = False


# Adresse de mise a jour de la boutique Chrome : c'est elle que Brave interroge
# pour une extension de la boutique imposee par politique.
CHROME_STORE_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
# Route servie par ce module pour les extensions Symbifox, hors boutique.
SYMBIFOX_UPDATE_PATH = "/bf_policy/extensions/update.xml"


class BfPolicyExtension(models.Model):
    """Catalogue d'extensions de navigateur imposables par la politique.

    Deux provenances :
    - « store » : la boutique Chrome (Bitwarden, uBlock…). Brave la tire de
      Google, comme une installation a la main ;
    - « symbifox » : nos extensions, hors boutique. Ce module sert lui-meme leur
      paquet signe (CRX3) et le fichier de mise a jour, sur le domaine de
      l'organisation. L'identifiant vient de la cle qui signe : il est stable.

    ⚠️ Hors boutique, Chromium n'accepte une extension imposee que sur Linux
    (et ChromeOS) — donc sur Blue Fox OS. Un poste Windows ou macOS non gere la
    refuserait : ce catalogue ne sert que les machines Blue Fox OS.
    """

    _name = "bf.policy.extension"
    _description = "Blue Fox Policy — Browser Extension"
    _order = "recommended desc, name, extension_id"

    name = fields.Char(required=True)
    extension_id = fields.Char(
        required=True, index=True, string="Extension ID",
        help="Identifiant Chromium : 32 lettres de a a p "
             "(la fin de l'adresse de la fiche dans la boutique Chrome).")
    source = fields.Selection(
        [("store", "Chrome Web Store"), ("symbifox", "Symbifox (served by this instance)")],
        required=True, default="store")
    managed_instance = fields.Boolean(
        string="Pass the instance address",
        help="Pose l'adresse de l'Odoo de l'organisation dans le stockage gere "
             "de l'extension (cle « instance »). L'extension doit la declarer "
             "dans son schema ; Symbifox Signets le fait. Elle ne fait que "
             "pre-remplir ses reglages : l'acces reste accorde par la personne.")
    recommended = fields.Boolean(index=True, default=False, string="Recommandee BF")
    note = fields.Char()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("extension_id_uniq", "unique(extension_id)",
         "Cette extension est deja au catalogue."),
    ]

    @api.depends("name", "extension_id")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = rec.name or rec.extension_id or ""

    @api.constrains("extension_id")
    def _check_extension_id(self):
        # Une coquille ne se verrait qu'au poste, en extension silencieusement
        # absente : on refuse ici tout ce qui n'a pas la forme d'un id Chromium.
        import re as _re
        for rec in self:
            if not _re.fullmatch(r"[a-p]{32}", rec.extension_id or ""):
                from odoo.exceptions import ValidationError
                raise ValidationError(_(
                    "« %s » n'est pas un identifiant d'extension Chromium "
                    "(32 lettres de a a p).", rec.extension_id))

    def _update_url(self, domain: str) -> str:
        self.ensure_one()
        if self.source == "symbifox":
            return f"https://{domain}{SYMBIFOX_UPDATE_PATH}" if domain else ""
        return CHROME_STORE_UPDATE_URL

    def _policy_entry(self, domain: str) -> dict:
        self.ensure_one()
        entry = {
            "id": self.extension_id,
            "name": self.name,
            "update_url": self._update_url(domain),
        }
        if self.managed_instance and domain:
            entry["managed"] = {"instance": f"https://{domain}"}
        return entry
