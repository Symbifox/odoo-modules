"""Les sources : ce qui fait qu'un lien suit la personne.

Les deux invariants verrouillés ici :

1. Le module reste installable et la page reste affichable SANS aucun module
   fournisseur. C'est ce qui justifie de n'avoir aucun import vers
   `bf_appointment` ni `bf_securetransfer`.
2. Une source qui ne résout pas fait DISPARAÎTRE le lien, elle ne rend pas une
   adresse approximative. Un lien mort atteint par un QR déjà imprimé coûte
   plus cher qu'un lien absent.
"""

from unittest.mock import patch

from odoo.addons.bf_linkpage.models.linkpage_source import BfLinkpageSource
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("bf_linkpage", "post_install", "-at_install")
class TestSources(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Source Testeur",
            "email": "source@test.invalid",
            "mobile": "+1 (514) 555-0142",
            "website": "https://exemple.invalid/",
        })
        cls.page = cls.env["bf.linkpage"].create({
            "name": "Source Testeur",
            "slug": "source-testeur",
            "kind": "owner",
            "partner_id": cls.partner.id,
            "state": "published",
        })

    def _link(self, **vals):
        base = {"page_id": self.page.id, "name": "Lien", "source_code": "manual"}
        base.update(vals)
        return self.env["bf.linkpage.link"].create(base)

    # ── les sources sans fournisseur ─────────────────────────────────────────

    def test_source_manuelle(self):
        link = self._link(url="https://example.invalid/x")
        self.assertEqual(link.resolved_url, "https://example.invalid/x")

    def test_source_courriel(self):
        link = self._link(source_code="partner_email")
        self.assertEqual(link.resolved_url, "mailto:source@test.invalid")

    def test_source_telephone_nettoie_la_ponctuation(self):
        """`tel:` n'accepte ni espace ni ponctuation de présentation."""
        link = self._link(source_code="partner_phone")
        self.assertEqual(link.resolved_url, "tel:+15145550142")

    def test_source_site_web(self):
        link = self._link(source_code="partner_website")
        self.assertEqual(link.resolved_url, "https://exemple.invalid/")

    def test_source_sans_donnee_ne_resout_pas(self):
        self.partner.website = False
        link = self._link(source_code="partner_website")
        self.assertFalse(link.resolved_url)

    # ── l'absence d'un fournisseur ───────────────────────────────────────────

    def test_source_dont_le_fournisseur_est_absent_ne_resout_pas(self):
        """Le test s'adapte à ce qui est installé, sans jamais rien conclure
        d'un parc qu'il n'a pas regardé : si le fournisseur est là, on vérifie
        l'inverse (la source est offerte)."""
        Source = self.env["bf.linkpage.source"]
        disponibles = Source._available_codes()
        if "resource.booking.type" in self.env:
            self.assertIn("appointment", disponibles)
        else:
            self.assertNotIn("appointment", disponibles)
            link = self._link(source_code="appointment")
            self.assertFalse(link.resolved_url)
            self.assertFalse(link.source_available)

    def test_le_catalogue_offre_tout_meme_sans_fournisseur(self):
        """Retirer une option de la sélection ferait disparaître la valeur des
        liens déjà enregistrés le jour d'une désinstallation, et Odoo
        afficherait une case vide sans dire pourquoi."""
        codes = [code for code, _label in self.env["bf.linkpage.source"]._selection()]
        self.assertIn("appointment", codes)
        self.assertIn("securetransfer", codes)

    def test_un_resolveur_qui_leve_ne_casse_pas_la_page(self):
        """Le point de reprise est posé avant l'appel, donc la transaction
        survit. Sans lui, une source cassée ferait échouer tout ce qui suit
        sur la page publique, pas seulement son propre lien."""
        def _boom(self, link):
            raise ValueError("source cassée")

        # Le correctif porte sur la classe DÉCLARÉE par le module, pas sur
        # `env[...].__class__` : cette dernière est la classe composite du
        # registre, où `_resolve_manual` n'est qu'hérité. Y écrire ajoute un
        # attribut que la restauration ne peut plus retirer, et Odoo refuse
        # ensuite la suite des tests pour cause de fuite d'attribut.
        with patch.object(BfLinkpageSource, "_resolve_manual", _boom):
            link = self._link(url="https://example.invalid/")
            self.assertFalse(link.resolved_url)
            # La transaction est toujours utilisable : c'est ce qui compte.
            self.assertGreaterEqual(self.env["res.partner"].search_count([]), 1)

    # ── l'affichage ──────────────────────────────────────────────────────────

    def test_un_lien_non_resolu_disparait_de_la_page(self):
        self.partner.website = False
        visible = self._link(name="Visible", url="https://example.invalid/")
        muet = self._link(name="Muet", source_code="partner_website")
        affiches = self.page._public_links()
        self.assertIn(visible, affiches)
        self.assertNotIn(muet, affiches)

    def test_compteur_de_liens_affiches_revele_une_source_muette(self):
        """L'écart entre « liens » et « liens affichés » est la seule façon de
        voir au back-office qu'une source ne résout plus."""
        self.partner.website = False
        self._link(name="Visible", url="https://example.invalid/")
        self._link(name="Muet", source_code="partner_website")
        self.page.invalidate_recordset()
        self.assertEqual(self.page.link_count, 2)
        self.assertEqual(self.page.visible_link_count, 1)

    # ── le refus des schémas dangereux ───────────────────────────────────────

    def test_javascript_refuse(self):
        """Un href fourni au back-office et rendu sur une page publique doit
        être borné aux schémas inoffensifs, sinon le module offre du script
        exécuté chez le visiteur."""
        with self.assertRaises(ValidationError):
            self._link(url="javascript:alert(1)")

    def test_data_uri_refuse(self):
        with self.assertRaises(ValidationError):
            self._link(url="data:text/html;base64,PHNjcmlwdD4=")

    def test_url_manquante_refusee(self):
        with self.assertRaises(ValidationError):
            self._link(url=False)


    def test_un_lien_inactif_reste_exclu_meme_sans_le_filtre_du_one2many(self):
        """Le one2many écarte déjà les inactifs, ce qui rendait le filtre
        explicite de `_public_links` invisible aux tests : le casser ne faisait
        rougir personne (mesuré par mutation le 2026-08-30). Ici on retire le
        filtre du one2many pour que le filtre explicite soit le SEUL rempart."""
        visible = self._link(name="Visible", url="https://example.invalid/")
        cache = self._link(name="Caché", url="https://cache.invalid/")
        cache.active = False
        sans_filtre = self.page.with_context(active_test=False)
        self.assertEqual(len(sans_filtre.link_ids), 2,
                         "le contexte doit bien exposer les deux liens")
        affiches = sans_filtre._public_links()
        self.assertIn(visible, affiches)
        self.assertNotIn(cache, affiches)

    def test_le_compteur_de_visites_est_incremente_en_base(self):
        """Un lire-modifier-écrire perd des visites en concurrence. On vérifie
        que le compteur monte bien de un par appel."""
        self.page.visit_count = 0
        self.env.flush_all()
        for attendu in (1, 2, 3):
            self.page._register_visit()
            self.assertEqual(self.page.visit_count, attendu)
        self.assertTrue(self.page.last_visit, "la dernière visite doit être datée")

    # ⚠ Ce que ce test NE prouve PAS : que l'incrément résiste à la
    # concurrence. Un lire-modifier-écrire passe ce test aussi bien que
    # l'incrément fait par la base — vérifié par mutation le 2026-08-30. La
    # perte de visites sous charge n'est pas démontrable dans une transaction
    # unique, et l'incrément SQL reste une précaution non couverte.


@tagged("bf_linkpage", "post_install", "-at_install")
class TestTemplates(TransactionCase):

    def test_reappliquer_un_gabarit_epargne_les_liens_ajoutes_a_la_main(self):
        """Sans la distinction, réappliquer un gabarit effacerait en silence le
        travail de la personne sur sa propre page."""
        template = self.env["bf.linkpage.template"].create({
            "name": "Équipe",
            "line_ids": [(0, 0, {
                "name": "Rendez-vous", "source_code": "manual",
                "url": "https://exemple.invalid/rdv",
            })],
        })
        partner = self.env["res.partner"].create({"name": "Gabarité"})
        page = self.env["bf.linkpage"].create({
            "name": "Gabarité", "kind": "owner",
            "partner_id": partner.id, "template_id": template.id,
        })
        self.assertEqual(len(page.link_ids), 1)
        perso = self.env["bf.linkpage.link"].create({
            "page_id": page.id, "name": "Mon blogue",
            "source_code": "manual", "url": "https://blogue.invalid/",
        })
        page.action_apply_template()
        page.invalidate_recordset()
        self.assertIn(perso, page.link_ids, "un lien ajouté à la main survit")
        self.assertEqual(len(page.link_ids), 2)

    def _liberer_le_defaut(self):
        """Retirer le drapeau du gabarit par défaut SEMÉ par le module.

        Depuis 18.0.1.1.0, le module installe « Carte de visite » avec
        `is_default`. Un test qui pose son propre défaut heurte donc la
        contrainte d'unicité et sort en erreur, alors que la contrainte fait
        exactement son travail. Le test doit partir d'un terrain qu'il maîtrise.
        """
        self.env["bf.linkpage.template"].search(
            [("is_default", "=", True)]
        ).is_default = False

    def test_un_seul_gabarit_par_defaut(self):
        self._liberer_le_defaut()
        self.env["bf.linkpage.template"].create({"name": "A", "is_default": True})
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage.template"].create({"name": "B", "is_default": True})

    def test_le_gabarit_de_groupe_passe_avant_le_defaut(self):
        groupe = self.env["res.groups"].create({"name": "Groupe Linkpage Test"})
        user = self.env["res.users"].create({
            "name": "Membre", "login": "membre.linkpage@test.invalid",
            "groups_id": [(4, groupe.id)],
        })
        self._liberer_le_defaut()
        self.env["bf.linkpage.template"].create({"name": "Défaut", "is_default": True})
        cible = self.env["bf.linkpage.template"].create({
            "name": "Ciblé", "group_ids": [(4, groupe.id)],
        })
        self.assertEqual(self.env["bf.linkpage.template"]._for_user(user), cible)
