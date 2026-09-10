"""« Hors heures » se lisait en UTC.

`bf.email.date` est un datetime NAÏF en UTC, et `_compute_signals` en lisait le
`.hour` comme s'il s'agissait d'une heure de bureau. À Montréal, 08 h-18 h UTC
valent 04 h-14 h locales : **tout courriel reçu après 14 h était marqué « hors
heures »**. Mesuré sur une boîte réelle avant correction : trois entrants
sur quatre.

Ce que ces tests éprouvent, et dans cet ordre :

1. Le cas qui échouait — 18 h UTC, soit 14 h à Montréal, en pleine journée.
2. Les deux bornes de la journée locale, du bon côté de la frontière.
3. Le fuseau lu est celui du PROPRIÉTAIRE de la ligne, pas celui du serveur ni
   celui de la personne qui lit : c'est sa journée de travail qu'on qualifie.
4. Un fuseau absent ou illisible ne fait pas lever le calcul.

⚠️ Le test 3 est le seul qui distingue vraiment le correctif d'un correctif
approximatif : lire `self.env.user.tz` marcherait tant que le lecteur est le
propriétaire, ce qui est le cas ordinaire — et tomberait sur un admin courriel
qui ouvre la boîte d'un collègue à l'autre bout du monde.
"""
from odoo.tests import tagged

from .common import MobileApiCase


@tagged("post_install", "-at_install")
class TestSignauxFuseau(MobileApiCase):

    def _at(self, utc, offset="700", owner=None):
        # ⚠️ Une ligne dont le propriétaire n'est pas le lecteur se crée en
        # sudo : les règles d'enregistrement de `bf.email` refusent de poser la
        # boîte de quelqu'un d'autre, et c'est très bien. Ce qu'on éprouve ici
        # est le calcul, pas l'accès — celui-là a ses propres tests.
        Email = self.env["bf.email"]
        Email = (Email.sudo() if owner and owner != self.owner
                 else Email.with_user(self.owner))
        rec = Email.create({
            "subject": "Repère %s" % utc,
            "email_from": "client@acme.test",
            "email_to": "owner@test.invalid",
            "direction": "in",
            "status": "new",
            "source": "imap",
            "account_id": self.account.id,
            "user_id": (owner or self.owner).id,
            "imap_folder": "INBOX",
            "imap_uid": offset,
            "message_id_header": "<fuseau-%s@test.invalid>" % offset,
            "date": utc,
        })
        return rec

    def test_quatorze_heures_locales_ne_sont_pas_hors_heures(self):
        """Le cas qui échouait : 18 h UTC = 14 h à Montréal, un jeudi."""
        rec = self._at("2026-08-20 18:00:00", "701")
        self.assertFalse(
            rec.is_late_night,
            "14 h un jeudi est en pleine journée de travail")

    def test_dix_neuf_heures_locales_sont_hors_heures(self):
        """23 h UTC = 19 h à Montréal, après la fermeture."""
        rec = self._at("2026-08-20 23:00:00", "702")
        self.assertTrue(rec.is_late_night)

    def test_les_deux_bornes_de_la_journee_locale(self):
        """8 h pile dedans, 17 h 59 dedans, 7 h 59 dehors, 18 h pile dehors."""
        self.assertFalse(self._at("2026-08-20 12:00:00", "703").is_late_night)
        self.assertFalse(self._at("2026-08-20 21:59:00", "704").is_late_night)
        self.assertTrue(self._at("2026-08-20 11:59:00", "705").is_late_night)
        self.assertTrue(self._at("2026-08-20 22:00:00", "706").is_late_night)

    def test_la_fin_de_semaine_se_lit_aussi_en_local(self):
        """Samedi 01 h UTC est encore vendredi 21 h à Montréal.

        Hors heures dans les deux lectures — mais pour la bonne raison : c'est
        21 h, pas « c'est samedi ». Le contrôle qui tranche est le suivant.
        """
        self.assertTrue(self._at("2026-08-22 01:00:00", "707").is_late_night)
        # Samedi 16 h UTC = samedi midi à Montréal : en plein jour, mais fin de
        # semaine. C'est le jour qui doit décider.
        self.assertTrue(self._at("2026-08-22 16:00:00", "708").is_late_night)
        # Lundi 16 h UTC = lundi midi : ni l'un ni l'autre.
        self.assertFalse(self._at("2026-08-24 16:00:00", "709").is_late_night)

    def test_le_fuseau_est_celui_du_proprietaire(self):
        """Et non celui du lecteur.

        L'inconnu est réglé sur Auckland : 22 h UTC y est le lendemain 10 h, en
        pleine journée, alors que c'est 18 h à Montréal — hors heures. Les deux
        réponses diffèrent, donc le test ne peut pas passer par accident.
        """
        self.stranger.tz = "Pacific/Auckland"
        mien = self._at("2026-08-20 22:00:00", "710")
        self.assertTrue(mien.is_late_night, "18 h à Montréal, un jeudi")
        sien = self._at("2026-08-20 22:00:00", "711", owner=self.stranger)
        self.assertFalse(
            sien.is_late_night,
            "vendredi 10 h à Auckland : la journée de travail du propriétaire")

    def test_fuseau_absent_retombe_sur_montreal(self):
        self.stranger.tz = False
        rec = self._at("2026-08-20 18:00:00", "712", owner=self.stranger)
        self.assertFalse(rec.is_late_night)

    def test_fuseau_illisible_ne_fait_pas_lever_le_calcul(self):
        """Un `tz` qui n'existe pas ne doit pas casser la création d'un courriel.

        ⚠️ Posé en SQL brut, et il n'y a pas d'autre moyen : `res.partner.tz`
        est un Selection, donc un `write` par l'ORM le refuse. Une valeur
        illisible ne peut venir que d'un import direct ou d'une restauration —
        exactement les chemins qui ne passent pas par la validation.
        """
        self.env.cr.execute(
            "UPDATE res_partner SET tz = %s WHERE id = %s",
            ("Mars/Olympus_Mons", self.stranger.partner_id.id))
        self.env.invalidate_all()
        rec = self._at("2026-08-20 18:00:00", "713", owner=self.stranger)
        self.assertFalse(rec.is_late_night, "repli sur Montréal")

    def test_re_router_la_ligne_recalcule_le_drapeau(self):
        """`user_id` fait partie des dépendances du calcul.

        Il y manquait : la moitié de ces signaux se lisent du point de vue du
        propriétaire, et une ligne re-routée gardait les réponses de l'ancien.
        22 h UTC est hors heures à Montréal et en pleine journée à Auckland.
        """
        self.stranger.tz = "Pacific/Auckland"
        rec = self._at("2026-08-20 22:00:00", "715")
        self.assertTrue(rec.is_late_night)
        rec.sudo().write({"user_id": self.stranger.id})
        rec.invalidate_recordset()
        # ⚠️ Relu en sudo : la ligne n'est plus la nôtre, et les règles
        # d'enregistrement la refusent — ce qui est exactement le comportement
        # voulu. Ce test-ci porte sur le calcul, pas sur l'accès.
        self.assertFalse(
            rec.sudo().is_late_night,
            "le calcul doit suivre le nouveau propriétaire")
