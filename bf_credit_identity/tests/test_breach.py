"""Après une fuite : les deux dossiers tout de suite, puis à un mois et à trois mois.

Cadence arbitrée le 2026-10-03 ; construite en 18.0.1.1.0 après la relecture des
sections 4 et 8 du guide (2026-10-11).
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import CreditCase


@tagged("post_install", "-at_install", "bf_credit_identity")
class TestApresUneFuite(CreditCase):

    @staticmethod
    def apres_une_fuite(env, **valeurs):
        """Lance l'assistant dans le rôle de ``env`` ; rend les rappels « après une fuite »."""
        env["bf.credit.breach"].create(valeurs).action_create()
        return env["bf.credit.reminder"].search([("after_breach", "=", True)])

    def _courriels_vers(self, partenaire):
        return self.env["mail.notification"].sudo().search_count([
            ("res_partner_id", "=", partenaire.id), ("notification_type", "=", "email")])

    @freeze_time("2031-01-10")
    def test_la_serie(self):
        rappels = self.apres_une_fuite(self.env_anne)
        attendu = {
            ("report_equifax", date(2031, 1, 10)), ("report_transunion", date(2031, 1, 10)),
            ("report_equifax", date(2031, 2, 10)), ("report_transunion", date(2031, 2, 10)),
            ("report_equifax", date(2031, 4, 10)), ("report_transunion", date(2031, 4, 10)),
        }
        self.assertEqual({(r.kind, r.next_date) for r in rappels}, attendu)
        for r in rappels:
            with self.subTest(rappel=r.name):
                self.assertEqual(r.user_id, self.anne)
                self.assertFalse(r.recurring, "un contrôle après une fuite est ponctuel")
                self.assertEqual(r.bureau, "equifax" if r.kind == "report_equifax" else "transunion")
                self.assertTrue(r.link_url)
        self.assertEqual(
            sorted(rappels.mapped("name"))[:3],
            ["Equifax credit report (after a breach, one month later)",
             "Equifax credit report (after a breach, right away)",
             "Equifax credit report (after a breach, three months later)"])
        self.assertEqual(set(rappels.mapped("breach_date")), {date(2031, 1, 10)})

    @freeze_time("2031-01-10")
    def test_tout_de_suite_a_son_activite(self):
        # Le volet « aucun courriel » ne départage rien ici : Odoo n'avise jamais
        # une activité qu'on s'assigne soi-même. La garde (mail_activity_quick_update)
        # est mesurée par test_isolation.test_la_tache_planifiee_n_envoie_rien.
        avant_mail = self.env["mail.mail"].sudo().search_count([])
        avant_notif = self._courriels_vers(self.anne.partner_id)
        rappels = self.apres_une_fuite(self.env_anne)
        avec = rappels.filtered(lambda r: r.sudo().activity_ids)
        self.assertEqual(avec.mapped("next_date"), [date(2031, 1, 10)] * 2,
                         "seuls les deux dossiers « tout de suite » ont leur activité")
        self.assertEqual(avec.sudo().activity_ids.user_id, self.anne)
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), avant_mail)
        self.assertEqual(self._courriels_vers(self.anne.partner_id), avant_notif)

    @freeze_time("2031-01-10")
    def test_le_mois_suivant_vient_au_preavis(self):
        rappels = self.apres_une_fuite(self.env_anne)
        un_mois = rappels.filtered(lambda r: r.next_date == date(2031, 2, 10))
        self.assertFalse(un_mois.sudo().activity_ids)
        with freeze_time("2031-02-03"):
            self.env["bf.credit.reminder"]._cron_raise_activities()
        self.assertEqual(len(un_mois.sudo().activity_ids), 2)

    @freeze_time("2031-01-10")
    def test_fait_il_s_archive(self):
        rappel = self.apres_une_fuite(self.env_anne).filtered(
            lambda r: r.kind == "report_equifax" and r.next_date == date(2031, 1, 10))
        rappel.action_mark_done()
        self.assertFalse(rappel.active)
        self.assertEqual(rappel.last_done_date, date(2031, 1, 10))
        self.assertFalse(rappel.sudo().activity_ids)
        self.assertEqual(len(self.env_anne["bf.credit.reminder"].search([("after_breach", "=", True)])), 5)

    @freeze_time("2031-01-10")
    def test_relancer_la_meme_date_ne_cree_pas_de_doublon(self):
        premiers = self.apres_une_fuite(self.env_anne)
        self.assertEqual(self.apres_une_fuite(self.env_anne), premiers)
        # Une autre date, une deuxième fuite : sa propre série.
        self.assertEqual(len(self.apres_une_fuite(self.env_anne, start_date="2031-05-20")), 12)

    @freeze_time("2031-01-10")
    def test_relancer_apres_un_rappel_fait_ou_deplace(self):
        """Un dossier « tout de suite » fait (archivé) et un autre repoussé de deux
        jours : relancer pour la même date ne refait ni l'un ni l'autre."""
        rappels = self.apres_une_fuite(self.env_anne)
        tout_de_suite = rappels.filtered(lambda r: r.next_date == date(2031, 1, 10))
        equifax = tout_de_suite.filtered(lambda r: r.kind == "report_equifax")
        transunion = tout_de_suite - equifax
        equifax.action_mark_done()
        transunion.next_date = date(2031, 1, 12)
        apres = self.apres_une_fuite(self.env_anne)
        self.assertEqual(len(apres), 5)
        self.assertEqual(self.env_anne["bf.credit.reminder"].with_context(active_test=False).search_count(
            [("after_breach", "=", True)]), 6)
        self.assertFalse(equifax.active)
        # Toute la série faite, donc archivée : relancer pour la même date ne la
        # refait pas.
        apres.action_mark_done()
        self.assertFalse(self.apres_une_fuite(self.env_anne))

    @freeze_time("2031-01-10")
    def test_l_assistant_se_supprime(self):
        """Une ligne de l'assistant dit qu'il y a eu une fuite : elle ne reste pas."""
        assistant = self.env_anne["bf.credit.breach"].create({})
        assistant.action_create()
        self.assertFalse(assistant.sudo().exists())
        # Même quand la série existe déjà ; la personne est avisée que rien n'est ajouté.
        assistant = self.env_anne["bf.credit.breach"].create({})
        action = assistant.action_create()
        self.assertFalse(assistant.sudo().exists())
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["next"]["res_model"], "bf.credit.reminder")

    @freeze_time("2031-01-10")
    def test_un_rappel_duplique_reste_apres_une_fuite(self):
        """Dupliqué, un rappel de la série garde sa marque : sinon, ponctuel et sans
        marque, il passerait pour le dossier Equifax du calendrier de départ."""
        trois_mois = self.apres_une_fuite(self.env_anne).filtered(
            lambda r: r.kind == "report_equifax" and r.next_date == date(2031, 4, 10))
        copie = trois_mois.copy()
        self.assertTrue(copie.after_breach)
        self.assertEqual(copie.breach_date, date(2031, 1, 10))
        depart = self.mise_en_route(self.env_anne).filtered(lambda r: not r.after_breach)
        self.assertTrue(depart.filtered(lambda r: r.kind == "report_equifax" and r.recurring))

    @freeze_time("2031-01-10")
    def test_le_calendrier_de_depart_ne_compte_pas_la_serie(self):
        """La série d'abord, le calendrier ensuite : il crée quand même ses quatre
        rappels récurrents. Sans ça, une personne touchée par une fuite avant sa mise
        en route n'aurait jamais ses dossiers annuels."""
        self.apres_une_fuite(self.env_anne)
        depart = self.mise_en_route(self.env_anne).filtered(lambda r: not r.after_breach)
        self.assertEqual(len(depart), 4)
        self.assertTrue(all(depart.mapped("recurring")))
        # Et l'inverse : le calendrier de Bruno n'empêche pas sa série, même quand
        # son dossier Equifax annuel tombe le jour même du « tout de suite ».
        self.mise_en_route(self.env_bruno, start_date="2031-01-10")
        self.assertEqual(len(self.apres_une_fuite(self.env_bruno)), 6)

    @freeze_time("2031-01-10")
    def test_la_serie_reste_privee(self):
        self.apres_une_fuite(self.env_anne)
        self.assertFalse(self.env_bruno["bf.credit.reminder"].search([]))
        self.assertFalse(self.env_admin["bf.credit.reminder"].search([]))
        # Les rappels d'Anne ne comptent pas comme ceux de Bruno.
        self.assertEqual(len(self.apres_une_fuite(self.env_bruno)), 6)

    def test_l_assistant_apres_une_fuite_est_prive(self):
        # Un TransientModel n'est pas isolé d'office : sans la règle, toute personne
        # interne lirait qui a subi une fuite, et quand.
        assistant = self.env["bf.credit.breach"].with_user(self.anne).create(
            {"start_date": "2031-03-01"})
        self.assertFalse(
            self.env["bf.credit.breach"].with_user(self.bruno).search([("id", "=", assistant.id)]))
        with self.assertRaises(AccessError):
            assistant.with_user(self.bruno).read(["start_date"])
        self.assertEqual(
            assistant.with_user(self.anne).read(["start_date"])[0]["start_date"], date(2031, 3, 1))

    @freeze_time("2031-01-10")
    def test_les_noms_en_francais(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_credit_identity"], ["fr_CA"], overwrite=True)
        self.anne.lang = "fr_CA"
        rappels = self.apres_une_fuite(self.env(user=self.anne, context={"lang": "fr_CA"}))
        self.assertIn("Dossier de crédit Equifax (après une fuite, tout de suite)",
                      rappels.mapped("name"))
        self.assertIn("Dossier de crédit TransUnion (après une fuite, à trois mois)",
                      rappels.mapped("name"))
        self.assertTrue(all("/fr/" in r.link_url for r in rappels))

    def test_le_guide_annonce_la_serie(self):
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["bf_credit_identity"], ["fr_CA"], overwrite=True)
        for lang, attendu in (("en_US", "<strong>After a breach</strong> plans checks of your two "
                                        "credit reports"),
                              ("fr_CA", "<strong>Après une fuite</strong> prévoit la consultation de "
                                        "vos deux dossiers")):
            with self.subTest(lang=lang):
                guide = str(self.env(user=self.anne, context={"lang": lang})[
                    "bf.credit.guide"].get_guide_html())
                self.assertIn(attendu, guide)
