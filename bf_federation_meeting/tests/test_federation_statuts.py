"""Les statuts qui traversent, et ceux qui ne doivent surtout pas traverser.

Deux familles d'essais, et elles ne disent pas la même chose :

* ce qui DOIT arriver chez le pair : terminé, annulé, et l'envoi de l'émetteur ;
* ce qui ne doit JAMAIS arriver : l'état d'envoi du receveur lui-même, qui est la
  seule chose qui sépare un compte rendu reçu d'un compte rendu publié au portail
  de ses clients.

Des miroirs d'ordre du jour et des comptes rendus ont été corrigés à la main chez
un pair, faute que ces chemins existent. Chaque essai ici nomme ce qui a été fait
à la main à ce moment-là.
"""

import json

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.bf_federation.tests.test_federation import TestFederation


@tagged("post_install", "-at_install", "federation", "federation_statuts")
class TestFederationStatuts(TestFederation):

    def setUp(self):
        super().setUp()
        # Construire une charge de compte rendu lit des libellés d'éléments de matrice, et
        # la reprise exige le rôle. On le pose une fois pour toutes.
        gestionnaire = self.env.ref("bf_meeting.group_meeting_manager")
        self.admin.groups_id = [(4, gestionnaire.id)]
        # 🔴 Le receveur doit avoir le droit d'écrire, sinon les essais de garde sont verts
        # pour la MAUVAISE raison : `AccessError` hérite de `UserError`, donc un refus de
        # droits passe pour un refus de la garde. Deux mutations ont survécu à cause de ça,
        # et seule la mutation l'a montré. Avec le rôle, seule la garde peut
        # encore refuser.
        self.receveur.groups_id = [(4, gestionnaire.id)]

    def _refus_de_la_garde(self, miroir, vals, quoi):
        """Refuser, oui, mais par la garde du miroir et pas par les droits."""
        with self.assertRaises(UserError, msg="%s doit être refusé sur un miroir" % quoi) as pris:
            miroir.with_user(self.receveur).write(vals)
        self.assertNotIsInstance(
            pris.exception, AccessError,
            "%s : refusé par les DROITS, donc la garde n'a jamais parlé" % quoi)
        self.assertIn("reçu de", str(pris.exception),
                      "%s : le refus doit être celui de la garde, qui nomme le pair" % quoi)

    # --- Outillage --------------------------------------------------------------------
    def _agenda(self, partager=True):
        agenda = self.env["meeting.agenda"].create({
            "name": "Statutaire de septembre",
            "project_id": self.project.id,
            "date": "2026-09-20 14:00:00",
            "duration_planned": 60,
            "objectives": "Faire le point.",
        })
        if partager:
            agenda.write({"federation_peer_id": self.peer_b.id})
            self._flush()
        return agenda

    def _compte_rendu(self, partager=True, **extra):
        valeurs = {
            "name": "Statutaire du 20 septembre",
            "project_id": self.project.id,
            "date": "2026-09-20 14:00:00",
            "duration_minutes": 55,
            "location": "Visio",
            "summary": "Trois chantiers, deux décisions.",
        }
        valeurs.update(extra)
        record = self.env["meeting.record"].create(valeurs)
        self.env["meeting.decision"].create({
            "meeting_id": record.id, "sequence": 1,
            "name": "On repousse le chantier C à octobre.",
        })
        if partager:
            record.write({"federation_peer_id": self.peer_b.id})
            self._flush()
        return record

    def _miroir(self, record, modele):
        link = self.env["federation.link"].with_context(active_test=False).search(
            [("peer_id", "=", self.peer_a.id), ("remote_ref", "=", str(record.id)),
             ("res_model", "=", modele)], limit=1)
        self.assertTrue(link, "aucun miroir pour %s" % record.display_name)
        return link._record().with_context(active_test=False)

    # --- L'ordre du jour ---------------------------------------------------------------
    def test_s01_l_etat_arrive_avec_la_premiere_carte(self):
        """Le miroir ne naît plus en « Brouillon » quand l'émetteur a déjà confirmé."""
        agenda = self._agenda(partager=False)
        agenda.action_confirm()
        agenda.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        self.assertEqual(self._miroir(agenda, "meeting.agenda").state, "confirmed")

    def test_s02_terminer_arrive_chez_le_pair(self):
        agenda = self._agenda()
        self.assertEqual(self._miroir(agenda, "meeting.agenda").state, "draft")
        agenda.action_done()
        self._flush()
        self.assertEqual(self._miroir(agenda, "meeting.agenda").state, "done")

    def test_s03_annuler_arrive_chez_le_pair(self):
        """🔴 Le cas qui n'avait aucun contournement manuel.

        `action_cancel` n'archive pas : sans ce chemin, le pair garde pour toujours une
        rencontre « Brouillon » qui n'aura jamais lieu, et personne ne va l'éteindre chez lui.
        """
        agenda = self._agenda()
        agenda.action_cancel()
        self._flush()
        miroir = self._miroir(agenda, "meeting.agenda")
        self.assertEqual(miroir.state, "cancelled")
        self.assertTrue(miroir.active, "annulé n'est pas archivé : l'émetteur garde le sien")

    def test_s04_l_envoi_de_l_emetteur_arrive_a_part(self):
        """L'envoi du pair se lit chez le receveur, sans que le receveur prétende avoir envoyé."""
        agenda = self._agenda()
        agenda.write({"email_sent_date": "2026-09-19 13:30:00"})
        self._flush()
        miroir = self._miroir(agenda, "meeting.agenda")
        self.assertEqual(agenda.send_state, "sent")
        self.assertEqual(miroir.federation_peer_send_state, "sent")
        self.assertEqual(fields.Datetime.to_string(miroir.federation_peer_sent_date),
                         "2026-09-19 13:30:00")
        self.assertEqual(miroir.send_state, "not_sent",
                         "le receveur n'a envoyé de courriel à personne, et son champ le dit")
        self.assertFalse(miroir.email_sent_date)
        self.assertFalse(miroir.sent_manually,
                         "« Envoyé à la main » veut dire « parti par un autre canal qu'Odoo » : "
                         "c'est faux chez le receveur")

    def test_s05_un_envoi_declenche_bien_une_carte(self):
        """⚠️ `send_state` est calculé : il n'est jamais dans le `vals`. Ce sont ses
        ingrédients qui doivent être surveillés, sinon l'envoi ne part pas du tout."""
        agenda = self._agenda()
        depart = self.Outbox.search_count([("kind", "=", "agenda.card")])
        agenda.write({"sent_date": "2026-09-19 12:00:00"})
        self.assertEqual(self.Outbox.search_count([("kind", "=", "agenda.card")]), depart + 1)
        self._flush()
        self.assertEqual(self._miroir(agenda, "meeting.agenda").federation_peer_send_state,
                         "prepared")

    def test_s06_le_miroir_ne_change_pas_son_propre_etat(self):
        agenda = self._agenda()
        miroir = self._miroir(agenda, "meeting.agenda")
        self._refus_de_la_garde(miroir, {"state": "done"}, "state")

    def test_s07_le_miroir_ne_se_pose_pas_un_envoi_a_la_main(self):
        """🔴 La garde nommait `send_state`, un champ CALCULÉ, et laissait passer ses
        ingrédients : c'est par là qu'une passe manuelle est entrée."""
        agenda = self._agenda()
        miroir = self._miroir(agenda, "meeting.agenda")
        for champ, valeur in (("email_sent_date", "2026-09-19 13:30:00"),
                              ("sent_manually", True),
                              ("sent_date", "2026-09-19 13:30:00")):
            self._refus_de_la_garde(miroir, {champ: valeur}, champ)

    # --- Le compte rendu ---------------------------------------------------------------
    def test_s08_le_genre_est_annonce(self):
        self.peer_a.action_ping()
        self.peer_a.invalidate_recordset()
        annonces = (self.peer_a.accepted_kinds or "").split(",")
        self.assertIn("record.share", annonces)
        self.assertIn("record.card", annonces)

    def test_s09_le_compte_rendu_arrive_avec_son_contenu(self):
        record = self._compte_rendu()
        miroir = self._miroir(record, "meeting.record")
        self.assertEqual(miroir.name, "Statutaire du 20 septembre")
        self.assertEqual(miroir.duration_minutes, 55)
        self.assertEqual(miroir.location, "Visio")
        self.assertIn("Trois chantiers", miroir.summary)
        self.assertEqual(miroir.decision_ids.mapped("name"),
                         ["On repousse le chantier C à octobre."])
        self.assertTrue(miroir.federation_is_mirror)
        self.assertTrue(miroir.exchange_source_ref,
                        "la provenance est posée : c'est elle qui rattache les copies d'avant")

    def test_s10_le_miroir_reste_en_brouillon_meme_quand_l_emetteur_a_envoye(self):
        """🔴 Le refus qui tient le portail du receveur fermé, par construction.

        `bf_meeting_portal` n'ouvre un compte rendu que si les TROIS sont vraies :
        `report_state == 'sent'`, une `report_sent_date`, et le visiteur aux destinataires.
        Un miroir n'en a aucune, et ce n'est pas un hasard.
        """
        record = self._compte_rendu(partager=False)
        record.write({"report_state": "sent", "report_sent_date": "2026-09-20 18:00:00",
                      "report_recipient_ids": [(4, self.admin.partner_id.id)]})
        record.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        miroir = self._miroir(record, "meeting.record")
        self.assertEqual(miroir.report_state, "draft")
        self.assertFalse(miroir.report_sent_date)
        self.assertFalse(miroir.report_recipient_ids)
        self.assertEqual(miroir.federation_peer_report_state, "sent")
        self.assertEqual(fields.Datetime.to_string(miroir.federation_peer_sent_date),
                         "2026-09-20 18:00:00")

    def test_s11_l_etat_du_pair_suit_les_changements(self):
        record = self._compte_rendu()
        miroir = self._miroir(record, "meeting.record")
        self.assertEqual(miroir.federation_peer_report_state, "draft")
        record.write({"report_state": "reviewed"})
        self._flush()
        self.assertEqual(self._miroir(record, "meeting.record").federation_peer_report_state,
                         "reviewed")

    def test_s12_le_contenu_se_rafraichit(self):
        record = self._compte_rendu()
        record.write({"summary": "Quatre chantiers, et une décision de moins."})
        self._flush()
        miroir = self._miroir(record, "meeting.record")
        self.assertIn("Quatre chantiers", miroir.summary)

    def test_s13_le_miroir_du_compte_rendu_se_lit(self):
        record = self._compte_rendu()
        miroir = self._miroir(record, "meeting.record")
        self._refus_de_la_garde(miroir, {"summary": "Ma version des faits."}, "summary")
        self._refus_de_la_garde(miroir, {"report_state": "sent"}, "report_state")

    def test_s14_le_verbatim_ne_traverse_pas(self):
        record = self._compte_rendu(partager=False, verbatim="Ce que chacun a dit, mot pour mot.")
        carte = record._federation_card()
        self.assertNotIn("verbatim", json.dumps(carte),
                         "le verbatim n'a rien à faire sur le réseau")

    # --- L'adoption d'une copie déjà là ------------------------------------------------
    def test_s16_une_copie_deja_importee_est_adoptee_pas_dupliquee(self):
        """🔴 Sans ce chemin, un rattrapage de N copies en aurait fait 2 N."""
        record = self._compte_rendu(partager=False)
        # Là où l'import de l'échange range ce qu'il reçoit : mesuré chez un pair, les
        # copies sont dans le projet miroir de l'émetteur, toutes.
        copie = self.env["meeting.record"].create({
            "name": "Copie arrivée par courriel",
            "date": "2026-09-20 14:00:00",
            "project_id": self.peer_a._ensure_mirror_project().id,
            "exchange_source_ref": "%s:%s" % (self.env.cr.dbname, record.id),
        })
        avant = self.env["meeting.record"].search_count([])
        record.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        self.assertEqual(self.env["meeting.record"].search_count([]), avant,
                         "aucun compte rendu de plus : la copie existante a été adoptée")
        miroir = self._miroir(record, "meeting.record")
        self.assertEqual(miroir, copie,
                         "c'est bien la copie déjà posée qui est devenue le miroir")
        self.assertEqual(copie.name, "Statutaire du 20 septembre",
                         "la copie adoptée est remise au niveau de son original")

    def test_s17_l_adoption_referme_l_etat_d_envoi(self):
        """🔴 Les copies avaient été basculées à « Envoyé » à la main. Devenir un miroir,
        c'est retomber à brouillon : c'est ce retour qui referme la porte du portail."""
        record = self._compte_rendu(partager=False)
        copie = self.env["meeting.record"].create({
            "name": "Copie arrivée par courriel",
            "date": "2026-09-20 14:00:00",
            "project_id": self.peer_a._ensure_mirror_project().id,
            "exchange_source_ref": "%s:%s" % (self.env.cr.dbname, record.id),
            "report_state": "sent",
            "report_sent_date": "2026-09-20 18:00:00",
            "report_recipient_ids": [(4, self.admin.partner_id.id)],
        })
        record.write({"federation_peer_id": self.peer_b.id})
        self._flush()
        copie.invalidate_recordset()
        self.assertEqual(copie.report_state, "draft")
        self.assertFalse(copie.report_sent_date)
        self.assertFalse(copie.report_recipient_ids)
        self.assertTrue(copie.federation_is_mirror)

    def test_s15_une_charge_cassee_est_refusee_proprement(self):
        """Une charge venue du réseau repasse par le validateur de l'échange, et un refus
        doit être un refus, pas une erreur 500."""
        self.assertIsNone(self.env["meeting.record"]._federation_parse({"format": "n'importe quoi"}))
        self.assertIsNone(self.env["meeting.record"]._federation_parse({}))
