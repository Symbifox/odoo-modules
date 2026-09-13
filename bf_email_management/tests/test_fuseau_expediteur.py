"""« Hors heures » doit se lire dans le fuseau de CELUI QUI ÉCRIT.

La 11.30.0 avait corrigé l'heure UTC en heure du propriétaire. C'était
encore le mauvais fuseau : Kooti et al. 2015 mesure l'heure locale de
l'expéditeur, et un client de Montréal qui écrit à 10 h écrit en heures de
bureau, que je le lise de Montréal ou d'Auckland.

Mesuré sur une base réelle le 2026-09-13, avant correction : 10 570 lignes sur
16 178, soit 65 %, portaient le drapeau, parce que le fuseau Odoo du
propriétaire est `Pacific/Auckland`.

⚠️ Le contrôle qui tranche est `test_montreal_et_auckland_repondent_pareil` :
tant que le calcul lisait le fuseau du propriétaire, deux lecteurs donnaient
deux réponses au même courriel.
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestFuseauExpediteur(MobileApiCase):

    def _avec_date(self, utc, entete_date, uid, owner=None, sender=None):
        Email = self.env["bf.email"]
        Email = (Email.sudo() if owner and owner != self.owner
                 else Email.with_user(self.owner))
        return Email.create({
            "subject": "Repère %s" % uid,
            "email_from": sender or "client@acme.test",
            "email_to": "owner@test.invalid",
            "direction": "in",
            "status": "new",
            "source": "imap",
            "account_id": self.account.id,
            "user_id": (owner or self.owner).id,
            "imap_folder": "INBOX",
            "imap_uid": uid,
            "message_id_header": "<exp-%s@test.invalid>" % uid,
            "date": utc,
            "raw_headers": (
                "Return-Path: <client@acme.test>\n"
                "Date: %s\n"
                "Subject: Repère %s" % (entete_date, uid)
            ),
        })

    def test_dix_heures_a_montreal_ne_sont_pas_hors_heures(self):
        """14 h UTC, écrit en -0400 : 10 h chez l'expéditeur, un jeudi."""
        rec = self._avec_date("2026-08-20 14:00:00",
                              "Thu, 20 Aug 2026 10:00:00 -0400", "801")
        self.assertFalse(rec.is_late_night)

    def test_vingt_et_une_heures_chez_l_expediteur_sont_hors_heures(self):
        rec = self._avec_date("2026-08-21 01:00:00",
                              "Thu, 20 Aug 2026 21:00:00 -0400", "802")
        self.assertTrue(rec.is_late_night)

    def test_montreal_et_auckland_repondent_pareil(self):
        """Le même courriel, lu par deux propriétaires de fuseaux opposés.

        C'est le contrôle qui distingue le correctif de son prédécesseur : en
        lisant le fuseau du propriétaire, l'un disait « en journée » et l'autre
        « hors heures » pour un seul et même message.
        """
        self.stranger.tz = "Pacific/Auckland"
        entete = "Thu, 20 Aug 2026 10:00:00 -0400"
        mien = self._avec_date("2026-08-20 14:00:00", entete, "803")
        sien = self._avec_date("2026-08-20 14:00:00", entete, "804",
                               owner=self.stranger)
        self.assertFalse(mien.is_late_night)
        self.assertFalse(sien.sudo().is_late_night)

    def test_une_machine_qui_date_en_utc_ne_recoit_aucun_signal(self):
        """`+0000` est ce qu'écrit un serveur, pas une heure de bureau."""
        rec = self._avec_date("2026-08-20 03:00:00",
                              "Thu, 20 Aug 2026 03:00:00 +0000", "805",
                              sender="alerts@supervision.test")
        self.assertFalse(
            rec.is_late_night,
            "une machine n'a pas d'heure de bureau à qualifier")

    def test_moins_zero_zero_zero_zero_vaut_heure_inconnue(self):
        """La RFC 5322 dit que `-0000` signifie « heure locale inconnue »."""
        rec = self._avec_date("2026-08-20 03:00:00",
                              "Thu, 20 Aug 2026 03:00:00 -0000", "806")
        self.assertFalse(rec.is_late_night)

    def test_sans_en_tete_on_retombe_sur_le_fuseau_du_proprietaire(self):
        """Une ligne née du chatter n'a pas de `raw_headers`.

        Le repli est l'ancien comportement, et c'est voulu : mieux vaut le
        fuseau du lecteur que rien du tout.
        """
        rec = self.env["bf.email"].with_user(self.owner).create({
            "subject": "Sans en-tête",
            "email_from": "client@acme.test",
            "email_to": "owner@test.invalid",
            "direction": "in", "status": "new", "source": "chatter",
            "user_id": self.owner.id,
            "message_id_header": "<sans-entete@test.invalid>",
            "date": "2026-08-20 23:00:00",
        })
        self.assertTrue(rec.is_late_night, "19 h à Montréal, hors heures")

    def test_un_en_tete_illisible_ne_fait_pas_lever_le_calcul(self):
        rec = self._avec_date("2026-08-20 14:00:00", "pas une date", "807")
        self.assertFalse(
            rec.is_late_night,
            "10 h à Montréal par le repli sur le fuseau du propriétaire")

    def test_la_fin_de_semaine_se_lit_chez_l_expediteur(self):
        """Vendredi 22 h à Montréal est samedi 02 h UTC : les deux sont dehors,
        mais samedi midi chez l'expéditeur doit l'être aussi."""
        rec = self._avec_date("2026-08-22 16:00:00",
                              "Sat, 22 Aug 2026 12:00:00 -0400", "808")
        self.assertTrue(rec.is_late_night, "samedi midi reste la fin de semaine")
