# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
"""La section du digest, et surtout ce qu'elle refuse de taire.

⚠️ Le destinataire des essais est une personne RÉELLE du groupe d'hébergement,
jamais `self.env.user` : sur un banc, c'est le superutilisateur, qui n'a aucun
groupe, et la section se lit maintenant au nom du destinataire.
"""

import uuid

from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSectionDigest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["daily.digest.config"].search([], limit=1) \
            or cls.env["daily.digest.config"].create({"name": "Digest de banc"})
        cls.partner = cls.env["res.partner"].create({
            "name": "Client du digest", "is_company": True,
        })
        cls.endpoint = cls.env["hosting.endpoint"].create({
            "name": "digest-01", "partner_id": cls.partner.id,
        })
        cls.gestion = new_test_user(
            cls.env, login="digest-gestion",
            groups="base.group_user,hosting_management.group_hosting_manager")
        cls.simple = new_test_user(
            cls.env, login="digest-simple", groups="base.group_user")

    def _system(self, name, endpoint=None, **values):
        base = {"name": name, "endpoint_id": (endpoint or self.endpoint).id,
                "machine_id": uuid.uuid4().hex, "patch_managed": True}
        base.update(values)
        return self.env["bf.patch.system"].create(base)

    def _section(self, user=None):
        return self.config._render_hosting_patch_section(user or self.gestion)

    def test_muette_quand_tout_va_bien(self):
        """Une section qui répète chaque matin que tout va bien cesse d'être
        lue. Elle se tait quand il n'y a rien à dire."""
        systeme = self._system("digest-01-linux")
        systeme._apply_report({"pending_count": 0, "pending_known": True})
        self.assertEqual(systeme.patch_state, "ok")
        self.assertNotIn("digest-01-linux", self._section())

    def test_un_systeme_MUET_fait_toujours_paraitre_la_section(self):
        """La raison d'être de la section : se taire parce que les machines ne
        parlent plus reviendrait à dire « rien à signaler » quand la vraie
        phrase est « personne ne mesure »."""
        systeme = self._system("digest-01-linux")
        self.assertEqual(systeme.patch_state, "stale")
        section = self._section()
        self.assertIn("digest-01-linux", section)
        self.assertIn("ne rapporte", section)

    def test_le_chapeau_se_lit_au_singulier_comme_au_pluriel(self):
        """L'assemblage d'un pluriel donnait « 1 un système ne rapporte plus »."""
        muets = self.env["bf.patch.system"].search_count(
            [("patch_state", "=", "stale")])
        self._system("digest-muet-a")
        section = self._section()
        self.assertNotIn("1 un système", section)
        if muets == 0:
            self.assertIn("Attention : un système ne rapporte plus", section)
        self._system("digest-muet-b")
        self.assertIn("systèmes ne rapportent plus", self._section())

    def test_la_securite_et_le_compte_inconnu_paraissent(self):
        secu = self._system("digest-secu")
        secu._apply_report({"pending_count": 4, "pending_security_count": 2,
                            "pending_known": True})
        aveugle = self._system("digest-aveugle")
        aveugle._apply_report({"pending_count": 0, "pending_known": False})
        section = self._section()
        self.assertIn("digest-secu", section)
        self.assertIn("digest-aveugle", section)

    def test_la_section_se_desactive(self):
        self._system("digest-01-linux")
        self.config.include_hosting_patch = False
        self.assertEqual(self._section(), "")

    # ------------------------------------------------------------------
    # Ce que le destinataire a le droit de lire
    # ------------------------------------------------------------------
    def test_sans_acces_a_l_hebergement_pas_de_section(self):
        """Le digest part à chaque personne de la configuration, sans
        condition d'accès. Une lecture en `sudo` lui donnait tout le parc."""
        self._system("digest-01-linux")
        self.assertIn("digest-01-linux", self._section(self.gestion))
        self.assertEqual(self._section(self.simple), "")

    def test_la_section_ne_nomme_que_les_clients_du_destinataire(self):
        autre_client = self.env["res.partner"].create({
            "name": "Autre client du digest", "is_company": True,
        })
        autre_poste = self.env["hosting.endpoint"].create({
            "name": "digest-autre", "partner_id": autre_client.id,
        })
        self._system("digest-du-client", endpoint=self.endpoint)
        self._system("digest-pas-a-lui", endpoint=autre_poste)
        borne = new_test_user(
            self.env, login="digest-borne",
            groups="base.group_user,hosting_management.group_hosting_user")
        software = self.env["hosting.software"].search([], limit=1) \
            or self.env["hosting.software"].create({"name": "Logiciel témoin"})
        self.env["hosting.service"].create({
            "name": "Service du client du digest", "partner_id": self.partner.id,
            "software_id": software.id, "user_id": borne.id,
        })
        self.env.invalidate_all()

        section = self._section(borne)
        self.assertIn("digest-du-client", section)
        self.assertNotIn("digest-pas-a-lui", section)
        self.assertIn("digest-pas-a-lui", self._section(self.gestion))

    # ------------------------------------------------------------------
    # Le HTML
    # ------------------------------------------------------------------
    def test_le_nom_du_systeme_est_echappe(self):
        """Les noms viennent de l'agent, donc du réseau. Ils partent dans un
        courriel HTML."""
        self._system("<img src=x onerror=alert(1)>")
        section = self._section()
        self.assertNotIn("<img", section)
        self.assertIn("&lt;img", section)

    def test_au_dela_de_la_limite_les_noms_ne_sont_echappes_qu_une_fois(self):
        """Ajouter « et N de plus » à une chaîne déjà échappée la ré-échappait :
        `a&b` sortait en `a&amp;amp;b` dès le neuvième système."""
        for i in range(10):
            self._system(f"a&b-{i}")
        section = self._section()
        self.assertIn("a&amp;b", section)
        self.assertNotIn("&amp;amp;", section)
        self.assertIn("de plus", section)

    def test_la_section_s_insere_dans_une_ligne_de_table(self):
        """⚠️ Le marqueur est ENTRE deux <tr>. Une section nue y est sortie de
        la table par l'analyseur HTML5 et s'affiche au-dessus de la carte.

        L'essai précédent ne gardait rien : son assertion vivait sous un `if`,
        et cherchait `<tr><td` que les lignes de la section contiennent déjà."""
        self._system("digest-01-linux")
        self.config.include_weather = False
        html = self.config._generate_html(
            {"tasks": [], "activities": []}, self.gestion
        )
        self.assertIn("<!-- Divider -->", html,
                      "le gabarit du digest a perdu son marqueur d'insertion")
        # D'autres sections s'insèrent au même marqueur : on vise la NÔTRE,
        # par son titre, et on exige que sa ligne d'enveloppe soit ouverte
        # après la dernière ligne fermée qui la précède.
        titre = html.index("Mises à jour du parc")
        avant = html[:titre]
        ouverture = avant.rfind('<tr><td style="padding:0 24px 24px 24px;">')
        self.assertGreater(ouverture, avant.rfind("</tr>"),
                           "la section est sortie de sa ligne de table")
        self.assertLess(titre, html.index("<!-- Divider -->"))
