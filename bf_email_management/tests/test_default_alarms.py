# -*- coding: utf-8 -*-
"""Rappels posés d'office sur les événements créés dans Odoo (11.11.0).

Le réglage ``bf_email_management.default_alarm_minutes`` acceptait un seul
délai; il en accepte désormais plusieurs, séparés par des virgules. Deux
lecteurs partagent ce réglage — celui-ci, et le filet de tirage de
``calendar_nextcloud_sync`` — donc la grammaire compte autant que la valeur.

⚠️ Un défaut de champ ne s'applique QUE si le champ est absent des valeurs de
création : c'est ce qui garde le .ics maître de ce qui vient de lui, et deux
tests d'ici en dépendent.
"""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDefaultAlarms(TransactionCase):

    def setUp(self):
        super().setUp()
        self.param = self.env["ir.config_parameter"].sudo()
        self.Event = self.env["calendar.event"]

    def _set(self, value):
        self.param.set_param("bf_email_management.default_alarm_minutes", value)

    def _minutes_on(self, event):
        return sorted(event.alarm_ids.mapped("duration_minutes"))

    def _event(self, **vals):
        base = {
            "name": "Rencontre rappels",
            "start": "2026-09-10 14:00:00",
            "stop": "2026-09-10 15:00:00",
        }
        base.update(vals)
        return self.Event.create(base)

    # -- La lecture du réglage ---------------------------------------------

    def test_une_liste_donne_plusieurs_delais(self):
        self._set("1,15")
        self.assertEqual(self.Event._bf_default_alarm_minutes(), [1, 15])

    def test_un_delai_seul_marche_toujours(self):
        self._set("15")
        self.assertEqual(self.Event._bf_default_alarm_minutes(), [15])

    def test_zero_desactive(self):
        self._set("0")
        self.assertEqual(self.Event._bf_default_alarm_minutes(), [])

    def test_les_doublons_et_l_ordre_sont_normalises(self):
        """« 15,1,15 » ne doit pas poser deux fois la même alarme."""
        self._set("15,1,15")
        self.assertEqual(self.Event._bf_default_alarm_minutes(), [1, 15])

    def test_un_morceau_illisible_n_emporte_pas_les_autres(self):
        """Le comportement qui distingue « tolérant » de « silencieux ».

        Un réglage à demi fautif doit poser les rappels valides plutôt que de
        n'en poser aucun : l'ancienne lecture faisait un ``int`` sur la chaîne
        entière et rendait 0, donc AUCUN rappel, sur une simple faute de
        frappe.
        """
        self._set("1, ,quinze,15")
        self.assertEqual(self.Event._bf_default_alarm_minutes(), [1, 15])

    def test_les_valeurs_negatives_sont_ecartees(self):
        self._set("-5,15")
        self.assertEqual(self.Event._bf_default_alarm_minutes(), [15])

    # -- Ce que ça pose sur une rencontre ----------------------------------

    def test_une_rencontre_creee_dans_odoo_recoit_les_deux_rappels(self):
        self._set("1,15")
        self.assertEqual(self._minutes_on(self._event()), [1, 15])

    def test_les_alarmes_existantes_sont_reutilisees_pas_dupliquees(self):
        """Sans rapprochement, chaque création fabriquerait un doublon de
        l'alarme d'usine et la liste déroulante des rappels deviendrait
        illisible."""
        self._set("1,15")
        Alarm = self.env["calendar.alarm"]
        domain = [
            ("alarm_type", "=", "notification"),
            ("duration_minutes", "in", [1, 15]),
        ]
        # ⚠️ Le comptage part APRÈS une première rencontre, pas avant : une
        # base neuve n'a que l'alarme d'usine de 15 minutes, donc la première
        # création fabrique légitimement celle d'une minute. Compter avant
        # mesurerait cette création-là et non la réutilisation.
        self._event()
        before = Alarm.search_count(domain)
        self._event()
        self._event()
        self.assertEqual(Alarm.search_count(domain), before,
                         "des alarmes en double ont été créées")
        self.assertEqual(before, 2,
                         "les deux délais du réglage n'ont pas été matérialisés")

    def test_un_evenement_qui_pose_ses_rappels_garde_les_siens(self):
        """L'invariant que le tirage Nextcloud dépend de.

        Le pull écrit toujours ``alarm_ids``, même vide : c'est ce qui rend le
        .ics maître de ce qui vient de lui. Si le défaut s'appliquait par-dessus
        une valeur explicite, un rappel supprimé dans Thunderbird reviendrait à
        chaque synchronisation.
        """
        self._set("1,15")
        event = self._event(alarm_ids=[Command.clear()])
        self.assertFalse(event.alarm_ids)

    def test_aucun_rappel_quand_le_reglage_est_a_zero(self):
        self._set("0")
        self.assertFalse(self._event().alarm_ids)
