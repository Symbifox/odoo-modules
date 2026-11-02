import unittest
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

REGISTER_CREATE = (
    "odoo.addons.bf_security_awareness_privacy.models.bf_security_profile"
    ".BfSecurityProfile._privacy_register_destruction"
)


@tagged("post_install", "-at_install", "bf_security_awareness_privacy")
class TestDeclaration(TransactionCase):
    """Ce que le pont déclare, indépendamment de `hr`."""

    def test_purpose_and_retention_are_declared(self):
        """La finalité et la règle sont au registre des traitements."""
        purpose = self.env.ref(
            "bf_security_awareness_privacy.purpose_security_awareness")
        # Pas du consentement : c'est la mesure de sécurité de l'art. 10.
        self.assertFalse(purpose.requires_consent)
        calendar = self.env.ref(
            "bf_security_awareness_privacy.retention_security_awareness")
        self.assertEqual(calendar.applies_to_model, "bf.security.profile")
        self.assertEqual(calendar.final_disposition, "destroy")
        # « 0 année » est délibéré : la conservation court jusqu'au départ.
        self.assertEqual(calendar.total_retention_days, 0)

    def test_models_are_classifiable(self):
        """Le pont ouvre ses modèles sans écraser ceux du socle."""
        allowed = self.env[
            "privacy.document.classification"]._privacy_classifiable_models()
        for model in ("bf.security.profile", "bf.phishing.result",
                      "bf.training.assignment", "bf.reported.phish"):
            self.assertIn(model, allowed)
        self.assertIn("res.partner", allowed)


@tagged("post_install", "-at_install", "bf_security_awareness_privacy")
class TestDepartureRetention(TransactionCase):
    """La conservation du profil de risque est ancrée sur le départ."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Dépendance douce : sans `hr`, il n'y a pas de date de départ à lire,
        # et le pont se contente de déclarer. On saute plutôt que d'échouer.
        if "hr.employee" not in cls.env:
            raise unittest.SkipTest(
                "hr n'est pas installé : aucune date de départ à résoudre.")
        cls.Profile = cls.env["bf.security.profile"]
        cls.Register = cls.env["privacy.destruction.register"]
        cls.calendar = cls.env.ref(
            "bf_security_awareness_privacy.retention_security_awareness")

        cls.template = cls.env["bf.phishing.template"].create({
            "name": "Leurre de test",
            "subject": "Votre compte doit être vérifié",
        })
        cls.campaign = cls.env["bf.phishing.campaign"].create({
            "name": "Campagne de test",
            "template_id": cls.template.id,
        })
        cls.course = cls.env["slide.channel"].create({"name": "Cours de test"})

    # ------------------------------------------------------------------ #
    # Fabriques
    # ------------------------------------------------------------------ #
    def _partner(self, name):
        return self.env["res.partner"].create({
            "name": name, "email": "%s@example.com" % name.lower()})

    def _employee(self, partner, departure_date=None):
        employee = self.env["hr.employee"].create({
            "name": partner.name, "work_contact_id": partner.id})
        if departure_date:
            employee.departure_date = departure_date
            employee.active = False
        return employee

    def _profile_with_data(self, partner):
        """Un profil doté d'un résultat, d'une formation et d'une trace."""
        result = self.env["bf.phishing.result"].create({
            "campaign_id": self.campaign.id,
            "partner_id": partner.id,
            "email": partner.email,
            "state": "clicked",
        })
        assignment = self.env["bf.training.assignment"].create({
            "partner_id": partner.id, "channel_id": self.course.id})
        trace = self.env["mailing.trace"].create({
            "model": "bf.phishing.result", "res_id": result.id,
            "email": partner.email})
        profile = self.Profile.search([("partner_id", "=", partner.id)])
        assignment.profile_id = profile
        return profile, result, assignment, trace

    def _days_ago(self, days):
        return fields.Date.context_today(self.Profile) - timedelta(days=days)

    # ------------------------------------------------------------------ #
    # Résolution du départ
    # ------------------------------------------------------------------ #
    def test_employed_person_is_never_due(self):
        """Quelqu'un en poste garde son profil, quelle que soit son ancienneté."""
        partner = self._partner("Enposte")
        self._employee(partner)
        profile, *_ = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "employed")
        self.assertFalse(profile.privacy_retention_due_date)

        self.Profile._cron_privacy_purge_departed()
        self.assertTrue(profile.exists())

    def test_no_employee_record_leaves_the_profile_alone(self):
        """Sans fiche employé, aucun ancrage : rien n'est détruit, et ça se voit.

        C'est le trou honnête du montage. Détruire « par défaut » sur une date
        devinée serait pire que ne rien détruire.
        """
        partner = self._partner("Inconnu")
        profile, *_ = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "unknown")
        self.assertFalse(profile.privacy_departure_date)

        self.Profile._cron_privacy_purge_departed()
        self.assertTrue(profile.exists())

    def test_departure_within_grace_is_pending(self):
        """Un départ tout frais n'est pas encore échu."""
        partner = self._partner("Fraisparti")
        self._employee(partner, departure_date=self._days_ago(5))
        profile, *_ = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "pending")
        self.assertEqual(
            profile.privacy_retention_due_date, self._days_ago(5) + timedelta(days=30))

        self.Profile._cron_privacy_purge_departed()
        self.assertTrue(profile.exists())

    def test_rehired_person_counts_as_employed(self):
        """Une fiche active sans date de départ l'emporte sur une fiche partie."""
        partner = self._partner("Reembauche")
        self._employee(partner, departure_date=self._days_ago(400))
        self._employee(partner)
        profile, *_ = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "employed")

    def test_status_is_readable_by_an_operator_without_privacy_rights(self):
        """Un opérateur de la sensibilisation voit l'échéance sans droits RH ni vie privée.

        Le champ est affiché dans SA liste : s'il lève, la liste ne s'ouvre
        plus. Le piège attrapé ici est `privacy.retention.calendar`, dont les
        ACL sont réservées aux groupes vie privée — sans le `sudo()` du pont,
        la lecture rendait une AccessError.

        ⚠️ Ce que ce test ne prouve PAS : que le `sudo()` sur `hr.employee`
        est nécessaire à la lecture de `departure_date`. Mesuré plutôt que
        supposé, le garde-fou `groups=` de ce champ ne mord qu'à la
        frontière `read()` ; l'accès par attribut rend la valeur à tout
        utilisateur interne.
        """
        partner = self._partner("Partisansdroits")
        self._employee(partner, departure_date=self._days_ago(90))
        profile, *_ = self._profile_with_data(partner)

        operator = self.env["res.users"].create({
            "name": "Opérateur sensibilisation",
            "login": "op_secaware_privacy",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_security_awareness.group_bf_secaware_manager").id,
            ])],
        })
        self.assertNotIn(
            self.env.ref("hr.group_hr_user"), operator.groups_id)

        seen = profile.with_user(operator)
        # 🔴 Depuis la 18.0.1.3.0, la date de départ et son échéance sont
        # réservées au responsable de la vie privée : republier à
        # l'opérateur une valeur que `hr` réserve aux RH élargissait une
        # visibilité sans le dire.
        with self.assertRaises(AccessError):
            seen.read(["privacy_departure_date"])
        with self.assertRaises(AccessError):
            seen.read(["privacy_retention_due_date"])
        # Ce qui lui reste ne livre aucune date.
        self.assertEqual(seen.privacy_retention_status, "due")
        # La liste passe par `read()` : c'est la frontière qui compte pour
        # l'affichage, et elle doit passer elle aussi.
        self.assertEqual(
            seen.read(["privacy_retention_status"])[0]["privacy_retention_status"],
            "due")

    def test_archived_employee_is_still_found(self):
        """🔴 Le garde-fou qui décide de tout : la fiche d'un partant est archivée.

        Sans `active_test=False`, la recherche ne voit jamais personne de
        parti — la passe rendrait « aucun départ connu » pour tout le monde et
        ne détruirait rien, en silence.
        """
        partner = self._partner("Archive")
        employee = self._employee(partner, departure_date=self._days_ago(90))
        self.assertFalse(employee.active, "la fiche d'un partant devrait être archivée")
        self.assertFalse(
            self.env["hr.employee"].search([("work_contact_id", "=", partner.id)]),
            "une recherche ordinaire voit encore la fiche : le test ne prouve rien")

        profile, *_ = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "due")

    # ------------------------------------------------------------------ #
    # Destruction
    # ------------------------------------------------------------------ #
    def test_departed_profile_is_destroyed_and_registered(self):
        """Départ échu : tout part, et une seule entrée immuable le consigne."""
        partner = self._partner("Parti")
        self._employee(partner, departure_date=self._days_ago(90))
        profile, result, assignment, trace = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "due")

        before = self.Register.search([])
        purged = self.Profile._cron_privacy_purge_departed()

        self.assertEqual(purged, 1)
        self.assertFalse(profile.exists())
        self.assertFalse(result.exists())
        self.assertFalse(assignment.exists())
        # Rien ne rattachait la trace au résultat par clé étrangère : sans
        # suppression explicite, elle survivrait avec l'adresse ciblée.
        self.assertFalse(trace.exists())

        entries = self.Register.search([]) - before
        self.assertEqual(len(entries), 1)
        entry = entries
        self.assertEqual(entry.res_model, "bf.security.profile")
        self.assertEqual(entry.res_name, partner.display_name)
        self.assertEqual(entry.subject_count, 1)
        # `delete`, pas `secure_wipe` : on supprime des lignes, on n'efface
        # pas des blocs.
        self.assertEqual(entry.destruction_method, "delete")
        self.assertEqual(entry.retention_calendar_id, self.calendar)
        self.assertTrue(entry.verification_hash)
        self.assertIn(self.campaign.name, entry.document_description)
        self.assertIn(self.course.name, entry.document_description)
        # L'entrée doit aussi dire ce qui SURVIT, sinon elle se lit comme si
        # plus rien ne restait sur la personne.
        self.assertIn("bf.reported.phish", entry.document_description)

    def test_register_failure_rolls_the_destruction_back(self):
        """Si l'inscription casse, rien n'est détruit non plus.

        L'inverse du pont du transfert sécurisé, où la purge existait déjà :
        ici c'est nous qui détruisons, donc détruire sans trace serait le pire
        des deux résultats.
        """
        partner = self._partner("Registrecasse")
        self._employee(partner, departure_date=self._days_ago(90))
        profile, result, assignment, trace = self._profile_with_data(partner)

        def boom(this, *args, **kwargs):
            raise ValueError("registre indisponible")

        with patch(REGISTER_CREATE, boom):
            purged = self.Profile._cron_privacy_purge_departed()

        self.assertEqual(purged, 0)
        self.assertTrue(profile.exists())
        self.assertTrue(result.exists())
        self.assertTrue(assignment.exists())
        self.assertTrue(trace.exists())

    def test_non_destroy_disposition_stops_the_pass(self):
        """La règle gouverne : sort final autre que « Destruction », rien ne bouge."""
        partner = self._partner("Sortfinal")
        self._employee(partner, departure_date=self._days_ago(90))
        profile, *_ = self._profile_with_data(partner)

        self.calendar.final_disposition = "permanent_archive"
        self.assertEqual(self.Profile._cron_privacy_purge_departed(), 0)
        self.assertTrue(profile.exists())

    def test_archived_rule_stops_the_pass(self):
        """Une règle archivée arrête la passe : pas de règle, pas de purge."""
        partner = self._partner("Reglearchivee")
        self._employee(partner, departure_date=self._days_ago(90))
        profile, *_ = self._profile_with_data(partner)

        self.calendar.active = False
        self.assertEqual(self.Profile._cron_privacy_purge_departed(), 0)
        self.assertTrue(profile.exists())

    def test_grace_parameter_moves_the_due_date(self):
        """Le délai d'application est un paramètre, pas une constante."""
        partner = self._partner("Delai")
        self._employee(partner, departure_date=self._days_ago(10))
        profile, *_ = self._profile_with_data(partner)
        self.assertEqual(profile.privacy_retention_status, "pending")

        self.env["ir.config_parameter"].sudo().set_param(
            "bf_security_awareness_privacy.purge_grace_days", "0")
        profile.invalidate_recordset()
        self.assertEqual(profile.privacy_retention_status, "due")
