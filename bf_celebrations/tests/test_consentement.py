# -*- coding: utf-8 -*-
"""Ce que le module promet sur le consentement, éprouvé plutôt qu'affirmé."""

from datetime import date

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_celebrations")
class TestConsentement(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.company
        cls.usager_a = cls.env["res.users"].create({
            "name": "Solveig Marchetti", "login": "cel_a@example.test",
            "email": "cel_a@example.test",
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id])],
        })
        cls.usager_b = cls.env["res.users"].create({
            "name": "Anouk Delcourt", "login": "cel_b@example.test",
            "email": "cel_b@example.test",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_celebrations.group_organizer").id,
            ])],
        })
        cls.employe_a = cls.env["hr.employee"].create({
            "name": "Solveig Marchetti", "user_id": cls.usager_a.id,
            "work_email": "cel_a@example.test",
            # ⚠️ Une VRAIE date de naissance au dossier : c'est justement ce
            # que le module ne doit jamais lire.
            "birthday": date(1988, 3, 14),
        })
        cls.employe_b = cls.env["hr.employee"].create({
            "name": "Anouk Delcourt", "user_id": cls.usager_b.id,
            "work_email": "cel_b@example.test",
        })

    def _profil(self, employe):
        return self.env["bf.celebration.profile"].sudo().search(
            [("employee_id", "=", employe.id)], limit=1) or \
            self.env["bf.celebration.profile"].sudo().create(
                {"employee_id": employe.id})

    # ------------------------------------------------------------------

    def test_profil_invisible_aux_autres(self):
        """Le collègue le mieux outillé ne lit pas mon consentement.

        L'organisateur a tous les droits du module. La règle est GLOBALE,
        donc elle le borne lui aussi : c'est le point du dessin.
        """
        profil = self._profil(self.employe_a)
        profil.write({
            "consent": "full", "celebration_day": 4,
            "celebration_month": "7"})

        lu_par_soi = self.env["bf.celebration.profile"].with_user(
            self.usager_a).search([])
        self.assertEqual(lu_par_soi, profil,
                         "La personne doit voir son propre profil.")

        lu_par_autre = self.env["bf.celebration.profile"].with_user(
            self.usager_b).search([("employee_id", "=", self.employe_a.id)])
        self.assertFalse(
            lu_par_autre,
            "Un organisateur ne doit lire aucun profil qui n'est pas le sien.")

        with self.assertRaises(AccessError):
            profil.with_user(self.usager_b).read(["consent"])

    def test_la_date_de_naissance_n_est_jamais_lue(self):
        """Un dossier RH rempli ne fabrique aucune occasion à lui seul."""
        self._profil(self.employe_a)  # reste « en attente »
        self.env["bf.celebration.occasion"].sudo().search([]).unlink()
        self.env["bf.celebration.occasion"]._cron_generer()
        occasions = self.env["bf.celebration.occasion"].sudo().search(
            [("employee_id", "=", self.employe_a.id)])
        self.assertFalse(
            occasions,
            "Une date de naissance au dossier ne vaut pas un consentement.")

    def test_oui_sans_date_est_refuse(self):
        profil = self._profil(self.employe_a)
        with self.assertRaises(ValidationError):
            profil.write({"consent": "full"})

    def test_29_fevrier_se_reporte_au_28(self):
        profil = self._profil(self.employe_a)
        profil.write({
            "consent": "quiet", "celebration_day": 29,
            "celebration_month": "2"})
        # 2027 est une année commune : le 29 février n'existe pas.
        prochaine = profil._prochaine_occurrence(date(2027, 1, 10))
        self.assertEqual(prochaine, date(2027, 2, 28))
        # 2028 est bissextile : la vraie date revient.
        prochaine = profil._prochaine_occurrence(date(2028, 1, 10))
        self.assertEqual(prochaine, date(2028, 2, 29))

    def test_le_retrait_efface_ce_qui_n_a_pas_eu_lieu(self):
        profil = self._profil(self.employe_a)
        profil.write({
            "consent": "full", "celebration_day": 4,
            "celebration_month": "7"})
        self.env["bf.celebration.occasion"]._cron_generer()
        Occasion = self.env["bf.celebration.occasion"].sudo()
        avant = Occasion.search_count([
            ("employee_id", "=", self.employe_a.id),
            ("state", "=", "upcoming")])

        profil.write({"consent": "none"})

        apres = Occasion.search_count([
            ("employee_id", "=", self.employe_a.id),
            ("state", "=", "upcoming")])
        self.assertEqual(
            apres, 0,
            "Un retrait doit valoir tout de suite, pas au cron du lendemain. "
            "Il y avait %s occasion(s) avant." % avant)

    def test_le_changement_de_consentement_laisse_une_trace(self):
        """Le registre doit pouvoir dire QUAND la personne a changé d'avis.

        🔴 Deux pièges se cumulent ici, et chacun rend « aucune trace » sans
        lever la moindre erreur :

        1. `tracking=True` sur un modèle qui n'hérite PAS de `mail.thread` ne
           trace rien. Odoo se contente d'un avertissement au chargement
           (« unknown parameter 'tracking' »), noyé dans le journal d'une
           montée. D'où l'héritage posé sur le modèle.
        2. ⚠️ Odoo **ÉCARTE** le suivi d'un enregistrement créé puis modifié
           dans la MÊME transaction : `_track_discard` pose `None` comme
           valeurs initiales, et `_track_finalize` sort en silence sur
           `if vals`. Un test qui crée puis écrit sans rien vider observera
           donc toujours zéro trace, et conclura à tort que le suivi est
           cassé. En production le problème n'existe pas : la création et le
           changement d'avis tombent dans deux requêtes distinctes.

        Vider le précommit après la création consomme cet écart et remet le
        test dans les conditions réelles.
        """
        profil = self._profil(self.employe_a)
        self.env.cr.precommit.run()

        profil.write({
            "consent": "full", "celebration_day": 4,
            "celebration_month": "7"})
        self.env.cr.precommit.run()
        profil.invalidate_recordset()

        traces = profil.message_ids.tracking_value_ids
        self.assertTrue(
            traces,
            "Un changement de consentement doit laisser une trace datée.")
        self.assertIn("consent", traces.mapped("field_id.name"))

        # Et cette trace se lit par la personne elle-même.
        self.assertTrue(profil.with_user(self.usager_a).message_ids)

    def test_le_miroir_d_agenda_est_eteint_par_defaut(self):
        """🔴 Un booléen `config_parameter` SUPPRIME sa clé quand on décoche.

        L'état sûr doit donc être celui de l'absence : ici, ne rien écrire
        dans un agenda qui se synchronise vers des téléphones.
        """
        param = self.env["ir.config_parameter"].sudo()
        param.set_param("bf_celebrations.calendar_mirror", "")
        self.assertFalse(
            self.env["bf.celebration.occasion"]._miroir_agenda_actif())
        param.set_param("bf_celebrations.calendar_mirror", "True")
        self.assertTrue(
            self.env["bf.celebration.occasion"]._miroir_agenda_actif())

    def test_organisateur_par_defaut_ne_rend_pas_l_usager_zero(self):
        """`int(get_param(...) or 0)` vaut 0 quand la clé est absente.

        `browse(0)` rendrait un enregistrement qui se lit comme un usager
        valide jusqu'à la première écriture.
        """
        self.env["ir.config_parameter"].sudo().set_param(
            "bf_celebrations.default_organizer_id", "")
        occasion = self.env["bf.celebration.occasion"].sudo().create({
            "occasion_type": "welcome",
            "date": date(2027, 5, 4),
            "employee_id": self.employe_a.id,
            "company_id": self.societe.id,
        })
        # Sans gestionnaire et sans réglage : un recordset VIDE, pas browse(0).
        self.employe_a.parent_id = False
        organisateur = occasion._organisateur_par_defaut()
        self.assertFalse(organisateur)
        self.assertNotIn(0, organisateur.ids)
