# -*- coding: utf-8 -*-
"""Les courriels des tableaux de vœux partent dans la mise en page commune.

Ils partaient dans la mise en page légère d'Odoo, en dur, à six endroits. Ils
prennent désormais la première de `_MISES_EN_PAGE` qui existe : la mise en page
commune de la maison (`bf_onboarding_base`, remplacée par celle de
`bluefox_branding` quand il est installé), sinon la légère d'Odoo. Le module ne
dépend pas du socle : sans lui, ces essais vérifient le repli.
"""

import re
from pathlib import Path
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from .test_merci import _tableau_livre

COMMUNE = "bf_onboarding_base.bf_mail_layout"
LEGERE_XMLID = "mail.mail_notification_light"
CARTE = "box-shadow:0 4px 24px"
LEGERE = "utm_medium=email"


@tagged("post_install", "-at_install", "bf_celebrations")
class TestMiseEnPage(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Même raison que `TestMerci` : la livraison rend un PDF.
        cls.env["ir.config_parameter"].sudo().set_param(
            "report.url", "http://127.0.0.1:9")
        cls.socle = bool(cls.env.ref(COMMUNE, raise_if_not_found=False))

    def _courriels(self, board):
        return self.env["mail.mail"].sudo().search(
            [("model", "=", "bf.celebration.board"), ("res_id", "=", board.id)])

    def test_la_livraison_et_le_merci_dans_la_mise_en_page_commune(self):
        if not self.socle:
            self.skipTest("bf_onboarding_base n'est pas installé : rien de commun à vérifier")
        board = _tableau_livre(self.env)
        livraison = self._courriels(board)
        self.assertTrue(livraison)
        self.assertTrue(board._remercier("<p>Merci à vous.</p>"))
        merci = self._courriels(board) - livraison
        self.assertTrue(merci)
        for courriel in livraison | merci:
            with self.subTest(sujet=courriel.subject):
                self.assertEqual(courriel.body_html.count(CARTE), 1, "une carte, la commune")
                self.assertNotIn(LEGERE, courriel.body_html)

    def test_l_invitation_au_consentement_aussi(self):
        """`_cron_inviter` (fiche de profil) passait la légère d'Odoo en dur."""
        utilisateur = self.env["res.users"].create({
            "name": "Profil Essai", "login": "cel_profil_essai@example.test",
            "email": "cel_profil_essai@example.test",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])]})
        self.env["hr.employee"].create({
            "name": "Profil Essai", "user_id": utilisateur.id,
            "work_email": "cel_profil_essai@example.test"})
        Gabarit = type(self.env["mail.template"])
        vues = []
        origine = Gabarit.send_mail

        def espion(gabarit, res_id, *args, **kwargs):
            vues.append(kwargs.get("email_layout_xmlid"))
            return origine(gabarit, res_id, *args, **kwargs)

        with patch.object(Gabarit, "send_mail", espion):
            self.env["bf.celebration.profile"]._cron_inviter()
        self.assertTrue(vues, "aucune invitation partie")
        attendue = COMMUNE if self.socle else LEGERE_XMLID
        self.assertEqual(set(vues), {attendue})

    def test_aucun_envoi_ne_code_la_mise_en_page_en_dur(self):
        """Chaque `email_layout_xmlid=` du module passe par `_mise_en_page()`.

        Deux envois l'avaient oublié (profil, occasion) : on le dit au source.
        """
        racine = Path(__file__).resolve().parent.parent
        fautes = []
        for fichier in list((racine / "models").glob("*.py")) + list((racine / "controllers").glob("*.py")):
            for n, ligne in enumerate(fichier.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"email_layout_xmlid\s*=", ligne) and "_mise_en_page()" not in ligne:
                    fautes.append("%s:%d" % (fichier.name, n))
        self.assertEqual(fautes, [])

    def test_le_repli_est_la_mise_en_page_legere_d_odoo(self):
        Board = self.env["bf.celebration.board"]
        if self.socle:
            self.assertEqual(Board._mise_en_page(), COMMUNE)
            self.env.cr.execute(
                "DELETE FROM ir_model_data WHERE module = 'bf_onboarding_base'"
                " AND name = 'bf_mail_layout'")
            self.env.registry.clear_cache()
        self.assertEqual(Board._mise_en_page(), LEGERE_XMLID)
