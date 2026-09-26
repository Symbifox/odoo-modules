"""Core bf.policy.org behaviour that shipped in v1 without tests.

Covers authorization modes, the hostname pattern, and the merged
get_policy_json payload shape (org defaults + per-user overrides)."""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPolicyModel(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({
            "name": "Foxy Inc.", "website": "https://foxy.example",
        })
        cls.org = cls.env["bf.policy.org"].create({
            "company_id": cls.company.id, "domain": "foxy.example",
            "accent_color": "#112233", "wallpaper_url": "https://foxy.example/w.png",
        })
        cls.user = cls.env["res.users"].create({
            "name": "Riley", "login": "riley@foxy.example",
            "company_id": cls.company.id, "company_ids": [(4, cls.company.id)],
        })

    # --- authorization modes -------------------------------------------------
    def test_authorized_any(self):
        self.org.provision_mode = "any"
        self.assertTrue(self.org.is_user_authorized(self.user))

    def test_authorized_group(self):
        grp = self.env["res.groups"].create({"name": "BFOS Provisioners"})
        self.org.write({"provision_mode": "group",
                        "provision_group_ids": [(6, 0, [grp.id])]})
        self.assertFalse(self.org.is_user_authorized(self.user))
        self.user.groups_id = [(4, grp.id)]
        self.assertTrue(self.org.is_user_authorized(self.user))

    def test_authorized_allowlist(self):
        self.org.write({"provision_mode": "allowlist",
                        "provision_user_ids": [(6, 0, [])]})
        self.assertFalse(self.org.is_user_authorized(self.user))
        self.org.provision_user_ids = [(4, self.user.id)]
        self.assertTrue(self.org.is_user_authorized(self.user))

    # --- hostname pattern ----------------------------------------------------
    def test_hostname_local_part(self):
        # {username} is the login local part (e-mail stripped at '@').
        self.assertEqual(self.org._hostname_for(self.user), "bf-riley")

    # --- merged payload ------------------------------------------------------
    def test_policy_json_shape_and_merge(self):
        # Org-owned mount + an extra user-owned mount that should merge in.
        self.env["bf.policy.mount"].create({
            "name": "Org NC", "org_id": self.org.id,
            "remote_path": "/remote.php/dav/files/", "mount_point": "~/Nextcloud"})
        override = self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "accent_color": "#ABCDEF"})
        self.env["bf.policy.pwa"].create({
            "name": "Talk", "url": "https://talk.foxy.example",
            "pinned": True, "user_id": override.id})

        payload = self.org.get_policy_json(self.user)

        self.assertEqual(payload["schema"], "bf-policy/v2")
        # Explicit routing domain wins over the website-derived one.
        self.assertEqual(payload["org"]["domain"], "foxy.example")
        self.assertEqual(payload["user"]["login"], "riley@foxy.example")
        self.assertEqual(payload["install"]["hostname"], "bf-riley")
        # User override beats the org accent; wallpaper falls back to org.
        self.assertEqual(payload["session"]["accent_color"], "#ABCDEF")
        self.assertEqual(payload["session"]["wallpaper_url"],
                         "https://foxy.example/w.png")
        # Org mount + user PWA both present in the merged session.
        self.assertEqual([m["name"] for m in payload["session"]["mounts"]],
                         ["Org NC"])
        self.assertIn("Talk", [p["name"] for p in payload["session"]["pwas"]])

    # --- install block: locale / keyboard / timezone --------------------------
    def _install(self, user=None):
        return self.org.get_policy_json(user or self.user)["install"]

    def test_install_defaults_come_from_org(self):
        self.org.write({"locale": "fr_CA.UTF-8", "keymap": "ca",
                        "timezone": "America/Montreal", "x_layout": "ca",
                        "x_variant": "multix",
                        "x_options": "grp:alt_shift_toggle"})
        self.user.tz = False
        block = self._install()
        self.assertEqual(block["locale"], "fr_CA.UTF-8")
        self.assertEqual(block["keymap"], "ca")
        self.assertEqual(block["timezone"], "America/Montreal")
        self.assertEqual(block["x_layout"], "ca")
        self.assertEqual(block["x_variant"], "multix")
        self.assertEqual(block["x_options"], "grp:alt_shift_toggle")

    def test_timezone_follows_the_user_odoo_preference(self):
        """The tz the person already maintains in Odoo wins over the org."""
        self.org.timezone = "America/Montreal"
        self.user.tz = "Pacific/Auckland"
        self.assertEqual(self._install()["timezone"], "Pacific/Auckland")

    def test_explicit_override_beats_the_odoo_preference(self):
        """Pinning a shared machine must survive its operator's own tz."""
        self.org.timezone = "America/Montreal"
        self.user.tz = "Pacific/Auckland"
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "timezone": "UTC"})
        self.assertEqual(self._install()["timezone"], "UTC")

    def test_blank_user_override_falls_back(self):
        """An override row that exists but is empty must change nothing."""
        self.org.write({"keymap": "ca", "x_layout": "ca", "x_variant": "multix"})
        self.user.tz = "America/Toronto"
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "accent_color": "#ABCDEF"})
        block = self._install()
        self.assertEqual(block["keymap"], "ca")
        self.assertEqual(block["x_variant"], "multix")
        self.assertEqual(block["timezone"], "America/Toronto")

    def test_keyboard_override_per_user(self):
        self.org.write({"keymap": "ca", "x_layout": "ca", "x_variant": "multix"})
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "keymap": "us", "x_layout": "us", "x_variant": ""})
        block = self._install()
        self.assertEqual(block["keymap"], "us")
        self.assertEqual(block["x_layout"], "us")
        # A blank override cannot clear an org value — same rule as accent.
        self.assertEqual(block["x_variant"], "multix")

    def test_install_block_validates_against_the_schema(self):
        """The new keys must not break the published policy.v2 contract."""
        import json
        import pathlib
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema_path = (pathlib.Path(__file__).resolve().parent.parent
                       / "static/schema/policy.v2.json")
        jsonschema.validate(self.org.get_policy_json(self.user),
                            json.loads(schema_path.read_text()))

    # ------------------------------------------------------------------
    # Applications Flatpak (catalogue + selection org/usager)
    # ------------------------------------------------------------------
    def _apps(self):
        return self.org.get_policy_json(self.user)["apps"]

    def _app(self, fid, **kw):
        return self.env["bf.policy.app"].create({"flatpak_id": fid, **kw})

    def test_apps_block_is_empty_by_default(self):
        """Une politique sans selection ne change rien : la machine garde la base
        bakee dans l'image."""
        self.assertEqual(self._apps(), {"install": [], "remove": []})

    def test_org_apps_are_served(self):
        self.org.app_ids = [(4, self._app("org.libreoffice.LibreOffice").id)]
        self.assertEqual(self._apps()["install"], ["org.libreoffice.LibreOffice"])

    def test_user_apps_extend_org_apps(self):
        self.org.app_ids = [(4, self._app("org.libreoffice.LibreOffice").id)]
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "app_ids": [(4, self._app("com.spotify.Client").id)]})
        self.assertEqual(self._apps()["install"],
                         ["org.libreoffice.LibreOffice", "com.spotify.Client"])

    def test_removal_wins_over_install(self):
        """Regle de conflit explicite : sans elle, le resultat dependrait de
        l'ordre de lecture des deux fichiers par system-flatpak-setup."""
        app = self._app("org.gnome.Boxes")
        self.org.app_ids = [(4, app.id)]
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "app_remove_ids": [(4, app.id)]})
        block = self._apps()
        self.assertEqual(block["install"], [])
        self.assertEqual(block["remove"], ["org.gnome.Boxes"])

    def test_duplicates_are_collapsed(self):
        app = self._app("com.brave.Browser")
        self.org.app_ids = [(4, app.id)]
        self.env["bf.policy.user"].create({
            "user_id": self.user.id, "company_id": self.company.id,
            "app_ids": [(4, app.id)]})
        self.assertEqual(self._apps()["install"], ["com.brave.Browser"])

    def test_flatpak_id_shape_is_enforced(self):
        """Une coquille dans un ID ne se verrait qu'au premier demarrage d'une
        machine, sous forme d'application silencieusement absente."""
        from odoo.exceptions import ValidationError
        # « com.brave » (2 labels) est VALIDE : Flathub en contient, et le
        # controle ne sert qu'a attraper une coquille sans point.
        for bad in ("brave", "com brave Browser", "", ".com.brave", "com..brave"):
            with self.assertRaises(ValidationError, msg=f"accepte a tort : {bad!r}"):
                self._app(bad)

    def test_display_name_shows_name_and_id(self):
        app = self._app("com.brave.Browser", name="Brave")
        self.assertEqual(app.display_name, "Brave (com.brave.Browser)")
        # Sans nom (creation rapide en tapant l'ID), l'ID sert d'etiquette.
        self.assertEqual(self._app("com.spotify.Client").display_name,
                         "com.spotify.Client")

    def test_appstream_parsing(self):
        """Lecture du catalogue Flathub, sans reseau."""
        import gzip
        xml = b"""<?xml version="1.0"?>
        <components>
          <component type="desktop-application">
            <id>com.brave.Browser</id><name>Brave</name>
            <summary>Fast Internet</summary>
            <categories><category>Network</category><category>WebBrowser</category></categories>
          </component>
          <component type="desktop">
            <id>org.gnome.Boxes.desktop</id><name>Boxes</name>
          </component>
          <component type="runtime"><id>org.freedesktop.Platform</id></component>
          <component type="desktop-application"><id>com.brave.Browser</id></component>
        </components>"""
        entries = self.env["bf.policy.app"]._parse_appstream(gzip.compress(xml))
        ids = [e["flatpak_id"] for e in entries]
        # Le runtime est ignore et le doublon ecarte. Le suffixe .desktop est
        # CONSERVE : c'est l'id Flatpak reel de certaines applications.
        self.assertEqual(ids, ["com.brave.Browser", "org.gnome.Boxes.desktop"])
        self.assertEqual(entries[0]["categories"], "Network,WebBrowser")

    def test_sync_preserves_the_curated_flag(self):
        """Une synchronisation ne doit pas effacer la courte liste approuvee."""
        app = self._app("com.brave.Browser", recommended=True)
        self.env["bf.policy.app"]._upsert_catalogue(
            [{"flatpak_id": "com.brave.Browser", "name": "Brave",
              "summary": "Fast", "categories": "Network"}])
        self.assertTrue(app.recommended)
        self.assertEqual(app.name, "Brave")

    def test_real_flathub_ids_are_accepted(self):
        """Cas vecu : le suffixe .desktop fait partie de l'id de certaines
        applications (app.organicmaps.desktop). Le retirer fabriquait un id
        inexistant, rejete ensuite par la contrainte de forme."""
        for good in ("app.organicmaps.desktop", "com.brave.Browser",
                     "org.kde.kate", "io.github.some_app.Name",
                     # Label commencant par « _ » : convention Flatpak quand le
                     # segment de domaine commence par un chiffre. 14 id reels
                     # de Flathub en dependent.
                     "ca._0ldsk00l.Nestopia",
                     "com.github._4lex4.ScanTailor-Advanced"):
            self.assertTrue(self._app(good).id, f"refuse a tort : {good}")

    def test_parsing_keeps_the_desktop_suffix(self):
        import gzip
        xml = (b'<components><component type="desktop-application">'
               b'<id>app.organicmaps.desktop</id><name>Organic Maps</name>'
               b'</component></components>')
        entries = self.env["bf.policy.app"]._parse_appstream(gzip.compress(xml))
        self.assertEqual([e["flatpak_id"] for e in entries], ["app.organicmaps.desktop"])
