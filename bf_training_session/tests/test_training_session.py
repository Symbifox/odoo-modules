from datetime import datetime, timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "bf_training_session")
class TestTrainingSession(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.societe = cls.env.ref("base.main_company")
        cls.partenaire = cls.env["res.partner"].create({"name": "Apprenante d'essai"})
        cls.employe = cls.env["hr.employee"].create({
            "name": "Apprenante d'essai",
            "company_id": cls.societe.id,
            "work_contact_id": cls.partenaire.id,
            # ⚠️ Sans taux horaire, le socle marque TOUTE réalisation incomplète
            # (« le coût horaire », pour le relevé du 1 %). Un essai qui l'oublie
            # mesure ce manque-là et croit mesurer le sien.
            "hourly_cost": 30.0,
        })
        cls.categorie = cls.env["bf.training.category"].create({
            "name": "Essais", "code": "ESS"})
        cls.activite = cls.env["bf.training.activity"].create({
            "name": "Secourisme d'essai",
            "category_id": cls.categorie.id,
            "mode": "classroom",
            "duration_hours": 16.0,
        })
        cls.type_evenement = cls.env["event.type"].create({"name": "Formation d'essai"})

    def _seance(self, **kw):
        valeurs = {
            "name": "Séance d'essai",
            "date_begin": datetime(2026, 3, 10, 13, 0, 0),
            "date_end": datetime(2026, 3, 10, 21, 0, 0),
            "training_activity_id": self.activite.id,
        }
        valeurs.update(kw)
        return self.env["event.event"].create(valeurs)

    def _inscrire(self, seance, **kw):
        valeurs = {
            "event_id": seance.id,
            "partner_id": self.partenaire.id,
            "name": self.partenaire.name,
        }
        valeurs.update(kw)
        return self.env["event.registration"].create(valeurs)

    # ------------------------------------------------------------------
    # 🔴 La date portée au registre est celle de la SÉANCE
    # ------------------------------------------------------------------
    def test_la_realisation_porte_le_jour_de_la_seance_pas_du_pointage(self):
        """Une séance de mars pointée aujourd'hui s'écrit en MARS.

        C'est la garde contre `date_closed`, qui vaut `cr.now()` : l'instant du
        clic. Sans elle, le registre daterait la formation du jour où quelqu'un
        s'en est souvenu.
        """
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.state = "done"

        realisation = inscription.training_record_id
        self.assertTrue(realisation, "la présence doit écrire une réalisation")
        self.assertEqual(
            realisation.date_done, fields.Date.to_date("2026-03-10"),
            "la réalisation doit porter le jour de la séance")
        self.assertNotEqual(
            realisation.date_done, fields.Date.context_today(inscription),
            "elle ne doit surtout pas porter la date du pointage")

    # ------------------------------------------------------------------
    # 🔴 Une inscription annulée garde sa date de présence : ne jamais la lire
    # ------------------------------------------------------------------
    def test_une_inscription_annulee_n_ecrit_rien_malgre_sa_date_de_presence(self):
        """Annuler après avoir pointé laisse `date_closed` en place.

        Le calcul d'Odoo est gardé par `if not registration.date_closed` : une
        fois la valeur posée, rien ne l'efface. Une garde qui lirait ce champ
        compterait cette personne comme présente.
        """
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.state = "done"

        # 🔴 Le flush n'est PAS cosmétique, et c'est le coeur du piège.
        # `date_closed` est un calcul STOCKÉ : marquer présent ne fait que le
        # marquer à recalculer. Si l'annulation suit dans la même transaction
        # sans lecture entre les deux, le calcul ne tourne qu'UNE fois, avec
        # l'état déjà à « annulé », et la date n'est jamais posée.
        #
        # En production les deux gestes sont séparés par une transaction —
        # quelqu'un pointe la présence, puis quelqu'un annule plus tard — donc
        # la date EST en base et survit à l'annulation. Le flush reproduit cette
        # réalité. Sans lui, l'essai mesurait un artefact de transaction et
        # concluait le contraire de ce qui se passe chez le client.
        inscription.flush_recordset()
        self.assertTrue(
            inscription.date_closed,
            "prémisse : la présence pointée pose bien la date")

        # ⚠️ On DÉTACHE le lien, on ne supprime PAS la réalisation. Supprimer
        # marchait tant que `bf_training_qc` n'était pas là ; avec lui, la
        # conservation de six ans refuse la suppression — et elle a raison.
        # L'essai ne passait qu'en isolement, ce que seule la passe des neuf
        # modules ensemble a montré.
        inscription.training_record_id = False
        avant = self.env["bf.training.record"].search_count(
            [("event_id", "=", seance.id)])

        inscription.state = "cancel"
        inscription._ecrire_au_registre()

        self.assertTrue(
            inscription.date_closed,
            "prémisse du test : Odoo laisse la date de présence sur une annulée")
        self.assertFalse(
            inscription._vaut_presence(),
            "une inscription annulée ne vaut pas présence")
        self.assertFalse(
            inscription.training_record_id,
            "rien ne doit être rattaché pour une annulée")
        self.assertEqual(
            self.env["bf.training.record"].search_count(
                [("event_id", "=", seance.id)]), avant,
            "et aucune ligne de plus ne doit être écrite")

    # ------------------------------------------------------------------
    # La signature
    # ------------------------------------------------------------------
    def test_une_presence_non_signee_est_ecrite_mais_incomplete(self):
        """Perdre la présence pour une signature manquante serait pire."""
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.state = "done"

        realisation = inscription.training_record_id
        self.assertTrue(realisation, "la présence est écrite malgré tout")
        self.assertTrue(realisation.signature_manquante)
        self.assertFalse(realisation.is_complete)
        self.assertIn("signature", realisation.missing_info)

    def test_la_signature_s_ajoute_aux_manques_du_socle_sans_les_ecraser(self):
        """Les deux manques doivent apparaître ENSEMBLE.

        🔴 C'est la garde que le lot 2 avait failli livrer sans essai : si la
        surcharge écrit `missing_info` au lieu de repartir de celui du socle,
        « le coût horaire » disparaît dès qu'il manque aussi la signature, et la
        personne corrige un manque à la fois sans jamais voir la liste. Un essai
        qui ne teste QUE la signature laisse passer cette mutation.
        """
        self.employe.hourly_cost = 0.0
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.state = "done"

        realisation = inscription.training_record_id
        self.assertFalse(realisation.is_complete)
        self.assertIn("coût horaire", realisation.missing_info,
                      "le manque du socle doit survivre à la surcharge")
        self.assertIn("signature", realisation.missing_info,
                      "le manque du pont doit s'y ajouter")

    def test_la_signature_rend_la_realisation_complete(self):
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.state = "done"
        realisation = inscription.training_record_id

        inscription.training_signature = b"c2lnbmF0dXJl"

        realisation.invalidate_recordset()
        self.assertFalse(realisation.signature_manquante)
        self.assertTrue(realisation.is_complete, realisation.missing_info)

    def test_la_signature_est_horodatee_a_l_apposition(self):
        seance = self._seance()
        inscription = self._inscrire(seance)
        self.assertFalse(inscription.training_signature_date)

        inscription.training_signature = b"c2lnbmF0dXJl"

        self.assertTrue(
            inscription.training_signature_date,
            "apposer la signature doit l'horodater")

    def test_signer_avant_de_pointer_ecrit_quand_meme(self):
        """L'ordre des deux gestes ne doit rien changer.

        On signe la feuille puis on pointe, ou on pointe puis on fait signer.
        Un seul point d'entrée en manquerait la moitié.
        """
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.training_signature = b"c2lnbmF0dXJl"
        self.assertFalse(inscription.training_record_id, "rien tant qu'absent")

        inscription.state = "done"

        self.assertTrue(
            inscription.training_record_id,
            "pointer après avoir signé doit écrire au registre")

    def test_une_seance_sans_signature_exigee_est_complete_sans_signature(self):
        seance = self._seance(training_requires_signature=False)
        inscription = self._inscrire(seance)
        inscription.state = "done"

        realisation = inscription.training_record_id
        self.assertFalse(realisation.signature_manquante)
        self.assertTrue(realisation.is_complete, realisation.missing_info)

    # ------------------------------------------------------------------
    # Les heures
    # ------------------------------------------------------------------
    def test_les_heures_viennent_de_l_activite_pas_de_l_horloge(self):
        """La séance court 8 h d'horloge, l'activité en prévoit 16.

        La durée d'un événement compte les pauses et le repas. Celle de
        l'activité a été établie par quelqu'un : elle passe devant.
        """
        seance = self._seance()
        self.assertEqual(seance.training_hours, 16.0)

    def test_sans_duree_prevue_les_heures_viennent_de_la_seance(self):
        self.activite.duration_hours = 0.0
        seance = self._seance()
        self.assertEqual(seance.training_hours, 8.0)

    def test_une_seance_sans_heures_ecrit_une_realisation_incomplete(self):
        """Un cours sans durée ne vaut pas zéro heure, il vaut « on ne sait pas »."""
        self.activite.duration_hours = 0.0
        seance = self._seance()
        seance.training_hours = 0.0
        inscription = self._inscrire(seance)
        inscription.state = "done"

        realisation = inscription.training_record_id
        self.assertEqual(realisation.hours, 0.0)
        self.assertFalse(realisation.is_complete)
        self.assertIn("heures", realisation.missing_info)

    # ------------------------------------------------------------------
    # Le pont lui-même
    # ------------------------------------------------------------------
    def test_une_seance_sans_activite_n_ecrit_rien(self):
        """Un événement reste un événement tant qu'on ne l'a pas adossé."""
        seance = self._seance(training_activity_id=False)
        inscription = self._inscrire(seance)
        inscription.state = "done"
        self.assertFalse(inscription.training_record_id)

    def test_l_ecriture_est_idempotente(self):
        seance = self._seance()
        inscription = self._inscrire(seance)
        inscription.state = "done"
        premiere = inscription.training_record_id

        inscription._ecrire_au_registre()
        inscription.state = "open"
        inscription.state = "done"

        self.assertEqual(
            inscription.training_record_id, premiere,
            "repointer ne doit pas créer une seconde ligne")
        self.assertEqual(
            self.env["bf.training.record"].search_count(
                [("event_id", "=", seance.id)]), 1)

    def test_une_presence_sans_fiche_d_employe_n_ecrit_rien(self):
        seance = self._seance()
        etranger = self.env["res.partner"].create({"name": "Personne hors effectif"})
        inscription = self._inscrire(seance, partner_id=etranger.id, name=etranger.name)
        inscription.state = "done"
        self.assertFalse(inscription.training_record_id)

    def test_l_activite_compte_ses_seances(self):
        self._seance()
        self._seance(name="Deuxième séance")
        self.activite.invalidate_recordset()
        self.assertEqual(self.activite.event_count, 2)
