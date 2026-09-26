{
    "name": "Symbifox — Blue Fox OS Policy",
    "version": "18.0.2.11.0",
    "category": "Tools",
    "summary": "Group-policy plane for Blue Fox OS endpoints — single source of "
               "truth for zero-touch install + config + policies",
    "description": """
Symbifox — Blue Fox OS Policy (the Blue Fox OS GPO)
===================================================
Org-level defaults + per-user overrides that provision and govern Blue Fox OS
endpoints. Served as a merged, schema-validated JSON at /api/v1/policy/me to a
user authenticated with an Authentik OIDC bearer token.

Covers:
  - Authorization: who may provision a machine (menu-driven, not hardcoded).
  - Zero-touch install image: OCI image ref + locale/keymap/timezone + the OIDC
    device-flow endpoints baked into the anonymous /blue-fox-install.ks kickstart
    (rendered by bf_zerotouch_install from bf.policy.org — see _kickstart_vars).
  - Install block: locale, keymap, timezone, hostname, root, sssd/LDAP login.
  - Policies: offline login (sssd cache) + expiry, MFA required, auto-lock.
  - Session: accent, wallpaper, Nextcloud mounts, pinned PWAs, clock format
    (12 h / 24 h) and a second clock when the person's timezone differs from
    the org's.
  - Browser: extensions forced into Brave (ExtensionInstallForcelist), picked
    from a catalogue per org and per person. Symbifox's own extensions are
    served by this module as signed CRX3 packages with a public update manifest
    at /bf_policy/extensions/update.xml; store extensions (Bitwarden…) come
    from the Chrome Web Store.
  - User: the person's photo (employee record, else user avatar), so the
    machine's login screen shows their face.

The Anaconda %pre script consumes the install/policies blocks; the firstboot
welcome agent consumes the session block. Schema: static/schema/policy.v2.json.

Single source of truth: the per-tenant zero-touch config that used
to live in a separate bf.zerotouch.tenant model is folded into bf.policy.org, so
one Policy row fully describes a tenant — from the install image down to the
session PWAs. bf_zerotouch_install now only renders the kickstart from this row.

Multi-tenant: /api/v1/policy/me is routed to a bf.policy.org by the
request Host (the domain typed at the GRUB zero-touch entry). With a single org
row, that row is served regardless of host; with several, an unmatched host is
refused (404) rather than leaking another tenant's policy.

Re-synchronisation: a machine now gets an identity of its
own. During the install, while the operator's bearer is still in hand, the %pre
calls POST /api/v1/policy/enroll and stores the returned per-machine secret at
/etc/bluefox/machine.json (0600, root). A daily timer then re-fetches the policy
from GET /api/v1/policy/machine with that secret and rewrites the machine's
provisioning.json — so a policy edited in Odoo reaches machines that are already
installed, instead of only new ones. Odoo keeps the sha256 of the secret, never
the secret; revoking a machine (Policy > Machines) cuts it off at the next sync
and blocks any re-enrolment under the same identity.

Disk passphrase escrow: with "Escrow disk passphrase" on,
the installer draws the LUKS passphrase itself and deposits it during the same
authenticated window as the enrolment, instead of a human typing one nobody
records. It is stored encrypted with a key read from the environment or
odoo.conf — never from the database — so a stolen dump opens no disk. Reading it
back needs a dedicated group that system administration does NOT imply, and
every reveal is counted, timestamped and attributed on the machine record. Fails
closed: no key configured means the deposit is refused and the installer falls
back to a typed passphrase, because a machine installed with a generated
passphrase nobody holds is worse than either.

Access hardening (18.0.2.11.0): portal, archived and out-of-company users are
refused in every provisioning mode, and the check is replayed at each machine
sync; new orgs default to "group" (nobody until groups are chosen), existing
orgs keep their mode. The bearer maps to a user through ONE configurable claim
matched exactly against the login (several matches = refusal). A machine
enrolled for another user or org cannot be re-enrolled (409), and an escrowed
passphrase is never overwritten. A disk passphrase can only be read through the
counted Reveal button, by the person who pressed it, for five minutes. Service
methods are not callable over RPC. The LDAP bind password is only served to
orgs in sssd login mode.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["base", "web"],
    "data": [
        # Groups BEFORE the ACLs: ir.model.access.csv references one.
        "security/bf_policy_groups.xml",
        "security/ir.model.access.csv",
        "security/bf_policy_rules.xml",
        "data/bf_policy_extension_data.xml",
        "views/bf_policy_views.xml",
        "views/bf_policy_menus.xml",
    ],
    "auto_install": False,
    "installable": True,
    "application": True,
}
