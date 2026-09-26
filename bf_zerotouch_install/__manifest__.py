{
    "name": "Symbifox — Blue Fox OS Zero-Touch Install",
    "version": "18.0.4.0.2",
    "category": "Tools",
    "summary": "Serve /blue-fox-install.ks for Blue Fox OS zero-touch (rendered from bf.policy.org)",
    "description": """
        Public HTTP endpoint at /blue-fox-install.ks that returns a kickstart
        rendered from the canonical artifacts in data/:
            data/blue-fox-install.ks.template  (template)
            data/bfos_provision.py             (%pre device-flow, embedded)
            data/bfos_apply.py                 (%post apply, embedded)

        Consumed by the Anaconda installer when a user picks "Install Blue Fox
        OS (zero-touch)" in the GRUB menu and types this domain. The v2
        kickstart contains:
          - ostreecontainer pointing at the org's Blue Fox OS OCI image
          - %pre OIDC device-flow (bf_policy): authenticate the
            operator on a 2nd device, pull the org policy JSON, stage it
          - Locale, keyboard, timezone, network, encrypted btrfs autopart
          - %post that applies the policy install block + writes
            /var/lib/bluefox-welcome/tenant.json for the firstboot welcome wizard

        The device flow is fail-safe: it never aborts the install and falls back
        to org defaults (and the static install seeds) if it can't reach the org.

        Single source of truth: per-tenant config (OCI image, locale,
        keymap, timezone, OIDC endpoints) now lives in bf.policy.org. This module
        no longer owns a tenant model — it just resolves the Policy org by request
        Host and renders the kickstart from org._kickstart_vars(). An org with no
        OCI image configured has zero-touch disabled (404). Depends on bf_policy;
        the legacy bf.zerotouch.tenant table is migrated into Policy and dropped.

        No secrets in the response — image ref, service URLs and OIDC endpoints
        are public; the device flow needs operator consent on a 2nd device.
        Authentication-free by design (Anaconda dracut fetches before any login
        could happen anyway).

        This module's data/ artifacts mirror the canonical copies kept in the
        Blue Fox OS image repository (install/*), whose CI gates any drift.
    """,
    "author": "Les services de consultation Blue Fox, Inc.",
    "website": "https://symbifox.com",
    "license": "Other proprietary",  # Business Source License 1.1, see LICENSE
    "depends": ["base", "web", "bf_policy"],
    "data": [],
    "auto_install": False,
    "installable": True,
    "application": False,
}
