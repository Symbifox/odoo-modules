from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestInvoiceFollowup(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.relance_type = cls.env.ref("bf_invoice_followup.mail_activity_type_relance")
        cls.today = fields.Date.context_today(cls.env["account.move"])

    # -- helpers ------------------------------------------------------------

    def _facture(self, jours_de_retard=20, post=True):
        facture = self.init_invoice(
            "out_invoice", partner=self.partner_a, amounts=[1000.0],
            invoice_date=self.today - timedelta(days=jours_de_retard + 10), post=post)
        facture.invoice_date_due = self.today - timedelta(days=jours_de_retard)
        return facture

    def _relances(self, facture):
        return self.env["mail.activity"].search([
            ("res_model", "=", "account.move"),
            ("res_id", "=", facture.id),
            ("activity_type_id", "=", self.relance_type.id),
        ])

    # -- le champ de retard -------------------------------------------------

    def test_jours_de_retard_comptes_depuis_l_echeance(self):
        facture = self._facture(jours_de_retard=20)
        self.assertEqual(facture.bf_days_overdue, 20)

    def test_une_facture_fournisseur_ne_compte_pas(self):
        achat = self.init_invoice("in_invoice", partner=self.partner_a, amounts=[500.0],
                                  invoice_date=self.today - timedelta(days=40), post=True)
        achat.invoice_date_due = self.today - timedelta(days=30)
        self.assertEqual(achat.bf_days_overdue, 0)

    def test_une_facture_a_venir_ne_compte_pas(self):
        facture = self._facture(jours_de_retard=-5)
        self.assertEqual(facture.bf_days_overdue, 0)

    # -- le palier ----------------------------------------------------------

    def test_le_changement_de_palier_planifie_une_activite(self):
        facture = self._facture()
        self.assertFalse(self._relances(facture))
        facture.bf_followup_state = "rappel_1"
        relances = self._relances(facture)
        self.assertEqual(len(relances), 1)
        self.assertIn("1er rappel", relances.summary)
        self.assertEqual(facture.bf_followup_date, self.today)

    def test_revenir_au_meme_palier_ne_replanifie_rien(self):
        facture = self._facture()
        facture.bf_followup_state = "rappel_1"
        facture.bf_followup_state = "rappel_1"
        self.assertEqual(len(self._relances(facture)), 1)

    # -- le cron ------------------------------------------------------------

    def test_le_cron_cree_une_activite_et_ne_touche_pas_au_palier(self):
        facture = self._facture(jours_de_retard=20)
        self.env["account.move"]._cron_overdue_followup_activities()
        self.assertEqual(len(self._relances(facture)), 1)
        self.assertEqual(facture.bf_followup_state, "none",
                         "le cron ne doit jamais faire évoluer le palier")

    def test_le_cron_est_idempotent(self):
        facture = self._facture(jours_de_retard=20)
        self.env["account.move"]._cron_overdue_followup_activities()
        self.env["account.move"]._cron_overdue_followup_activities()
        self.assertEqual(len(self._relances(facture)), 1)

    def test_le_cron_laisse_une_facture_pas_encore_en_retard(self):
        facture = self._facture(jours_de_retard=2)
        self.env["account.move"]._cron_overdue_followup_activities()
        self.assertFalse(self._relances(facture),
                         "le seuil par défaut est de 7 jours après l'échéance")

    # -- le gabarit livré ---------------------------------------------------

    def test_le_module_livre_son_gabarit_de_relance(self):
        gabarit = self.env.ref("bf_invoice_followup.mail_template_relance_facture")
        self.assertEqual(gabarit.model_id.model, "account.move")
        self.assertTrue(gabarit.body_html)

    def test_le_gabarit_se_rend_sur_une_vraie_facture(self):
        facture = self._facture(jours_de_retard=20)
        gabarit = self.env.ref("bf_invoice_followup.mail_template_relance_facture")
        rendu = gabarit._render_field("body_html", facture.ids)[facture.id]
        self.assertIn(facture.name, rendu)
        sujet = gabarit._render_field("subject", facture.ids)[facture.id]
        self.assertIn("20", sujet, "le sujet nomme les jours de retard")

    def test_le_bouton_precharge_le_gabarit(self):
        facture = self._facture()
        action = facture.action_send_followup_relance()
        self.assertEqual(action["res_model"], "mail.compose.message")
        attendu = self.env["ir.config_parameter"].sudo().get_param(
            "bf_invoice_followup.relance_template_id")
        if attendu:
            self.assertEqual(action["context"].get("default_template_id"), int(attendu))

    # -- qui reçoit le récapitulatif ---------------------------------------

    def _usager(self, login, facturation, abonne=True):
        groupes = [self.env.ref("base.group_user").id]
        if facturation:
            groupes.append(self.env.ref("account.group_account_invoice").id)
        return self.env["res.users"].create({
            "name": login, "login": login, "email": "%s@example.test" % login,
            "groups_id": [(6, 0, groupes)],
            "bf_followup_digest": abonne,
        })

    def _envoyer_le_recap(self):
        """Joue le cron du récapitulatif en retenant les courriels."""
        Mail = type(self.env["mail.mail"])
        envoyes = []
        with patch.object(Mail, "send",
                          lambda self, *a, **k: envoyes.extend(self.mapped("email_to")) or True):
            self.env["account.move"]._cron_weekly_followup_digest()
        return envoyes

    def test_le_recap_part_a_une_personne_de_la_facturation(self):
        """Discrimination : la même passe doit servir l'une et refuser l'autre."""
        self._facture(jours_de_retard=20)
        compta = self._usager("recap.compta", facturation=True)
        envoyes = self._envoyer_le_recap()
        self.assertIn(compta.email, envoyes)

    def test_le_recap_ne_part_pas_a_qui_n_est_pas_de_la_facturation(self):
        """Le champ d'abonnement est auto-inscriptible : la garde est dans le domaine.

        Sans elle, n'importe quel usager interne s'ouvrait le grand livre client
        — numéros de facture, noms de clients, soldes dus — en cochant une case
        dans ses propres préférences.
        """
        self._facture(jours_de_retard=20)
        quelconque = self._usager("recap.quelconque", facturation=False)
        self.assertTrue(quelconque.bf_followup_digest,
                        "l'essai doit porter sur un abonné réel, sinon il ne prouve rien")
        envoyes = self._envoyer_le_recap()
        self.assertNotIn(quelconque.email, envoyes)

    def test_la_case_reste_inscriptible_par_la_personne(self):
        """La garde ne doit pas casser l'abonnement de qui a le droit."""
        compta = self._usager("recap.compta2", facturation=True, abonne=False)
        compta.with_user(compta).write({"bf_followup_digest": True})
        self.assertTrue(compta.bf_followup_digest)

    # -- la clôture au paiement --------------------------------------------

    def test_le_filet_du_cron_ferme_une_relance_oubliee(self):
        """Une facture réglée hors lettrage garderait sa relance sans le filet."""
        facture = self._facture(jours_de_retard=20)
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=facture.ids
        ).create({})._create_payments()
        # On repose une relance À LA MAIN : l'état d'une base où le défaut a
        # tourné pendant des mois.
        facture._followup_schedule_activity(summary="Relance oubliée")
        self.assertEqual(len(self._relances(facture)), 1)
        self.env["account.move"]._cron_overdue_followup_activities()
        self.assertFalse(self._relances(facture))

    def test_le_filet_ne_touche_pas_une_facture_impayee(self):
        """Discrimination : le balayage ne doit fermer QUE les factures réglées."""
        facture = self._facture(jours_de_retard=20)
        facture.bf_followup_state = "rappel_1"
        self.assertEqual(len(self._relances(facture)), 1)
        ferme = self.env["account.move"]._bf_close_paid_followups()
        self.assertEqual(ferme, 0)
        self.assertEqual(len(self._relances(facture)), 1,
                         "une facture impayée garde sa relance")


    def test_le_balayage_ferme_la_relance_d_une_facture_payee(self):
        """La clôture passe par le balayage, pas par le lettrage.

        ⚠️ Mesuré le 2026-11-02 : au moment du lettrage, `payment_state` porte
        encore la valeur d'avant, et la surcharge de `account.partial.reconcile`
        est appelée avec une liste VIDE. Le seul chemin qui ferme vraiment est
        le balayage des deux tâches planifiées.
        """
        facture = self._facture(jours_de_retard=20)
        facture.bf_followup_state = "rappel_1"
        self.assertEqual(len(self._relances(facture)), 1)
        self.env["account.payment.register"].with_context(
            active_model="account.move", active_ids=facture.ids
        ).create({})._create_payments()
        # Un paiement non lettré à un relevé laisse la facture « in_payment »,
        # que le module compte comme payée (PAID_STATES).
        self.assertIn(facture.payment_state, ("paid", "in_payment"))
        ferme = self.env["account.move"]._bf_close_paid_followups()
        self.assertGreaterEqual(ferme, 1)
        self.assertFalse(self._relances(facture),
                         "une facture payée ne doit plus porter de relance ouverte")
