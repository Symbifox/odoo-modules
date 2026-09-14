"""La page d'organisation : l'accueil d'une entreprise cliente.

Ce fichier verrouille les quatre décisions qui la distinguent d'une page
personnelle, et qui sont toutes des décisions de PRUDENCE plutôt que de
présentation :

1. Son adresse ne se devine pas. Un slug déduit du nom de l'organisation
   (`/l/entreprise-cliente-inc`) est lisible par quiconque connaît la liste des
   clients ; ici l'adresse est tirée au sort, et c'est elle qui tient la porte.
2. Elle porte une échéance, et on AVERTIT avant qu'elle tombe. L'échéance d'une
   page ponctuelle est un succès ; celle d'une page d'accueil client au milieu
   d'un mandat est un 404 que personne chez nous n'apprend.
3. Les liens de portail ne s'affichent QUE si quelqu'un de l'organisation a un
   compte. Un bouton « Votre portail » qui n'ouvre qu'un écran de connexion est
   le pire lien d'un courriel d'accueil.
4. Elle se rattache à une SOCIÉTÉ. Rattachée à une personne, elle résoudrait
   des sources qui ne lui sont pas destinées.
"""

import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

_OPAQUE_RE = re.compile(r"^[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}$")


@tagged("bf_linkpage", "post_install", "-at_install")
class TestOrgPage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Page = cls.env["bf.linkpage"]
        cls.org = cls.env["res.partner"].create({
            "name": "Organisation Témoin Inc.",
            "is_company": True,
            "email": "info@organisation-temoin.invalid",
        })
        cls.contact = cls.env["res.partner"].create({
            "name": "Personne Témoin",
            "parent_id": cls.org.id,
            "email": "personne@organisation-temoin.invalid",
        })
        cls.conseiller = cls.env["res.users"].create({
            "name": "Conseillère Témoin",
            "login": "conseillere.temoin@test.invalid",
            "email": "conseillere.temoin@test.invalid",
        })

    def _page(self, **vals):
        base = {
            "name": self.org.name,
            "kind": "org",
            "partner_id": self.org.id,
            "state": "published",
        }
        base.update(vals)
        return self.Page.create(base)

    def _link(self, page, code, **vals):
        base = {"page_id": page.id, "name": "Lien", "source_code": code}
        base.update(vals)
        return self.env["bf.linkpage.link"].create(base)

    # ── l'adresse ────────────────────────────────────────────────────────────

    def test_slug_tire_au_sort_et_ne_dit_rien_du_client(self):
        page = self._page()
        self.assertTrue(
            _OPAQUE_RE.match(page.slug),
            "le slug d'une page d'organisation doit être opaque, pas %r" % page.slug,
        )
        self.assertNotIn("organisation", page.slug)
        self.assertNotIn("temoin", page.slug)

    def test_deux_pages_ne_tirent_pas_le_meme_slug(self):
        autre = self.env["res.partner"].create({
            "name": "Autre Organisation Inc.", "is_company": True,
        })
        premiere = self._page()
        seconde = self._page(name=autre.name, partner_id=autre.id)
        self.assertNotEqual(premiere.slug, seconde.slug)

    def test_un_slug_explicite_est_respecte(self):
        """La page reste modifiable : le tirage est un DÉFAUT, pas une prison."""
        page = self._page(slug="accueil-choisi-a-la-main")
        self.assertEqual(page.slug, "accueil-choisi-a-la-main")

    # ── l'organisation ───────────────────────────────────────────────────────

    def test_une_personne_ne_porte_pas_de_page_d_organisation(self):
        with self.assertRaises(ValidationError):
            self._page(partner_id=self.contact.id)

    def test_l_organisation_est_obligatoire(self):
        with self.assertRaises(ValidationError):
            self._page(partner_id=False)

    # ── l'échéance ───────────────────────────────────────────────────────────

    def test_echeance_armee_a_la_creation(self):
        page = self._page()
        self.assertTrue(page.date_expiry)
        reste = (page.date_expiry - fields.Datetime.now()).days
        self.assertGreater(reste, 300)

    def test_reconduire_repousse_l_echeance(self):
        page = self._page(date_expiry=fields.Datetime.now() + timedelta(days=3))
        page.action_renew()
        self.assertGreater((page.date_expiry - fields.Datetime.now()).days, 300)

    def test_l_avertissement_ne_part_qu_une_fois_par_echeance(self):
        page = self._page(date_expiry=fields.Datetime.now() + timedelta(days=5))
        self.assertEqual(self.Page._cron_warn_expiring(), 1)
        self.assertEqual(page.expiry_warned_on, page.date_expiry.date())
        # Repasser ne redit pas la même chose.
        self.assertEqual(self.Page._cron_warn_expiring(), 0)
        # Repousser l'échéance réarme l'avertissement sans qu'on efface rien.
        page.action_renew()
        page.date_expiry = fields.Datetime.now() + timedelta(days=4)
        self.assertEqual(self.Page._cron_warn_expiring(), 1)

    def test_l_avertissement_pose_une_activite_au_conseiller(self):
        page = self._page(
            date_expiry=fields.Datetime.now() + timedelta(days=5),
            advisor_user_id=self.conseiller.id,
        )
        self.Page._cron_warn_expiring()
        activite = self.env["mail.activity"].search([
            ("res_model", "=", "bf.linkpage"), ("res_id", "=", page.id),
        ])
        self.assertEqual(len(activite), 1)
        self.assertEqual(activite.user_id, self.conseiller)

    def test_le_modele_porte_le_mixin_des_activites(self):
        """Sans lui, UNE activité suffit à casser le tableau des activités.

        `mail.thread` laisse créer l'activité sans broncher ; c'est
        `ir.model.is_mail_activity` qui décide si la référence posée par
        `mail_activity_board` est valide. Le contrôle est ici parce que
        l'avertissement d'échéance pose justement une activité.
        """
        modele = self.env["ir.model"].search([("model", "=", "bf.linkpage")])
        self.assertTrue(modele.is_mail_activity)

    def test_une_page_expiree_ne_se_sert_plus(self):
        page = self._page(date_expiry=fields.Datetime.now() - timedelta(days=1))
        self.assertFalse(page.is_live)
        self.assertFalse(self.Page._resolve_slug(page.slug))

    # ── le portail : une porte sans clé ne s'affiche pas ──────────────────────

    def test_le_portail_ne_s_affiche_pas_sans_compte(self):
        page = self._page()
        lien = self._link(page, "org_portal")
        self.assertFalse(lien.resolved_url)

    def test_le_portail_s_affiche_des_qu_une_personne_a_un_compte(self):
        page = self._page()
        lien = self._link(page, "org_portal")
        self._donner_un_compte_portail()
        lien.invalidate_recordset(["resolved_url"])
        self.assertTrue(lien.resolved_url.endswith("/my/home"))

    def test_les_factures_exigent_un_compte_ET_une_facture(self):
        page = self._page()
        lien = self._link(page, "org_invoices")
        self._donner_un_compte_portail()
        lien.invalidate_recordset(["resolved_url"])
        self.assertFalse(
            lien.resolved_url,
            "sans facture publiée, le lien de factures n'a rien à ouvrir",
        )

    def _donner_un_compte_portail(self):
        groupe = self.env.ref("base.group_portal", raise_if_not_found=False)
        if not groupe:
            self.skipTest("le groupe portail n'est pas installé")
        self.env["res.users"].with_context(no_reset_password=True).create({
            "name": self.contact.name,
            "login": "portail.temoin@test.invalid",
            "partner_id": self.contact.id,
            "groups_id": [(6, 0, [groupe.id])],
        })

    # ── le conseiller ────────────────────────────────────────────────────────

    def test_le_courriel_du_conseiller_se_resout(self):
        page = self._page(advisor_user_id=self.conseiller.id)
        lien = self._link(page, "advisor_email")
        self.assertEqual(
            lien.resolved_url, "mailto:conseillere.temoin@test.invalid"
        )

    def test_sans_conseiller_le_lien_disparait(self):
        """Un conseiller vidé À LA MAIN reste vide : on ne le repropose pas."""
        page = self._page(advisor_user_id=False)
        self.assertFalse(page.advisor_user_id)
        lien = self._link(page, "advisor_email")
        self.assertFalse(lien.resolved_url)

    def test_une_page_sans_mention_de_conseiller_en_recoit_un(self):
        page = self._page()
        self.assertTrue(page.advisor_user_id)

    def test_le_conseiller_est_propose_depuis_le_vendeur(self):
        self.org.user_id = self.conseiller.id
        page = self._page()
        self.assertEqual(page.advisor_user_id, self.conseiller)

    def test_le_rendez_vous_ne_se_devine_pas(self):
        """Sans type nommé sur la page, le lien de rendez-vous disparaît.

        Le repli de `_resolve_appointment` (premier type public par séquence)
        a offert la rencontre exploratoire à un client existant, vu en jouant
        une page de démonstration sur une base réelle. Sur une page
        d'organisation, l'absence vaut mieux que la devinette.
        """
        page = self._page(advisor_user_id=self.conseiller.id, booking_slug=False)
        lien = self._link(page, "advisor_booking")
        self.assertFalse(lien.resolved_url)

    # ── le guide ─────────────────────────────────────────────────────────────

    def test_le_guide_ne_devine_aucune_adresse(self):
        page = self._page()
        lien = self._link(page, "guide")
        params = self.env["ir.config_parameter"].sudo()
        for cle in ("bf_linkpage.guide_url_fr", "bf_linkpage.guide_url_en",
                    "bf_guide.url_fr", "bf_guide.url_en"):
            params.set_param(cle, "")
        lien.invalidate_recordset(["resolved_url"])
        self.assertFalse(lien.resolved_url)

    def test_le_guide_suit_le_reglage(self):
        page = self._page()
        lien = self._link(page, "guide")
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_linkpage.guide_url_fr", "https://exemple.invalid/guide/"
        )
        lien.invalidate_recordset(["resolved_url"])
        self.assertEqual(lien.resolved_url, "https://exemple.invalid/guide/")

    # ── les gabarits ─────────────────────────────────────────────────────────

    def test_un_gabarit_de_personne_ne_se_pose_pas_sur_une_organisation(self):
        Template = self.env["bf.linkpage.template"]
        pour_personne = Template._for_user(self.env.user)
        pour_org = Template._for_org()
        self.assertTrue(pour_org, "le gabarit d'organisation doit être semé")
        self.assertEqual(pour_org.target, "org")
        if pour_personne:
            self.assertEqual(pour_personne.target, "person")
            self.assertNotEqual(pour_personne, pour_org)

    def test_un_defaut_par_cible_est_permis(self):
        """Deux gabarits par défaut sont légitimes s'ils visent des cibles différentes."""
        Template = self.env["bf.linkpage.template"]
        personne = Template.search(
            [("is_default", "=", True), ("target", "=", "person")], limit=1)
        org = Template.search(
            [("is_default", "=", True), ("target", "=", "org")], limit=1)
        self.assertTrue(personne and org)

    def test_deux_defauts_sur_la_meme_cible_sont_refuses(self):
        with self.assertRaises(ValidationError):
            self.env["bf.linkpage.template"].create({
                "name": "Deuxième défaut d'organisation",
                "target": "org",
                "is_default": True,
            })

    # ── ce que le module n'a pas le droit de devenir ─────────────────────────

    def test_aucun_champ_type_vers_un_module_optionnel(self):
        """Un champ relationnel vers un modèle optionnel = dépendance DURE.

        Elle ne se verrait pas ici (les fournisseurs sont installés sur cette
        base), mais elle ferait échouer l'installation chez le premier
        locataire qui ne les a pas. Le contrôle est donc structurel.
        """
        optionnels = {
            "resource.booking.type", "secure.transfer.brand",
            "account.move", "project.project", "hr.employee",
        }
        fautifs = []
        for modele in ("bf.linkpage", "bf.linkpage.link",
                       "bf.linkpage.template", "bf.linkpage.template.line"):
            for nom, champ in self.env[modele]._fields.items():
                if getattr(champ, "comodel_name", None) in optionnels:
                    fautifs.append("%s.%s" % (modele, nom))
        self.assertFalse(fautifs, "champs typés vers un module optionnel : %s" % fautifs)
