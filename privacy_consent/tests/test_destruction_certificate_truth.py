"""Le certificat de destruction ne doit certifier QUE ce qui a été détruit.

C'est le défaut le plus grave qu'ait porté ce module, et il n'était dans aucune
note avant le 2026-08-02 : la voie « droit à l'effacement » **émettait un
certificat pour des documents qu'elle ne tentait même pas de détruire**.
``_execute_destruction`` ne routait vers la destruction documentaire que pour
``document`` et ``campaign``, alors que le gabarit du certificat imprimait le
tableau « Documents détruits » pour ``erasure_right``.

Quatre corollaires, tous couverts ici :

1. le routage manquant de ``erasure_right`` ;
2. **anonymiser une pièce jointe ne détruit rien** — le renseignement personnel
   EST le contenu du fichier, et l'archiver laisse les octets dans le filestore ;
3. le tableau du PDF était **inversé** : une destruction réussie archive la
   classification, donc le many2many d'origine ne rendait que les ÉCHECS ;
4. l'empreinte du certificat n'était pas **rejouable** : le contenu embarquait
   ``now()``, donc deux appels rendaient deux empreintes différentes.

⚠ Ce fichier est écrit pour rester identique entre ``cq_consent`` et
``privacy_consent`` : les groupes sont résolus sans préfixe de module en dur.
"""

import base64

from odoo import fields
from odoo.tests import TransactionCase, tagged

MODULES = ("cq_consent", "privacy_consent")


@tagged("privacy_consent", "privacy_destruction_truth")
class TestDestructionCertificateTruth(TransactionCase):

    @classmethod
    def _groupe(cls, code):
        for module in MODULES:
            groupe = cls.env.ref(f"{module}.{code}", raise_if_not_found=False)
            if groupe:
                return groupe
        raise AssertionError(f"Groupe {code} introuvable dans {MODULES}.")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Destruction = cls.env["privacy.destruction.request"]
        cls.Classification = cls.env["privacy.document.classification"]
        cls.Register = cls.env["privacy.destruction.register"]

        cls.manager = cls.env["res.users"].create({
            "name": "Gestionnaire destruction",
            "login": "test_destruction_manager_truth",
            "email": "gestion-destruction@example.invalid",
            "groups_id": [(4, cls._groupe("group_privacy_manager").id)],
        })
        cls.officer = cls.env["res.users"].create({
            "name": "Responsable destruction",
            "login": "test_destruction_officer_truth",
            "email": "resp-destruction@example.invalid",
            "groups_id": [(4, cls._groupe("group_privacy_officer").id)],
        })

        cls.partner = cls.env["res.partner"].create({
            "name": "Sujet effacement",
            "email": "effacement@example.invalid",
        })
        cls.calendar = cls.env["privacy.retention.calendar"].create({
            "name": "Conservation d'essai",
            "code": "TRUTH-WF",
            "document_type": "contract",
            "legal_basis": "Essai",
            "active_retention_years": 0,
            "destruction_method": "delete",
        })
        # ⚠ La méthode n'est PAS un champ de la demande : `action_execute` la
        # tire de la politique, puis du calendrier, et retombe sinon sur
        # « anonymize ». C'est ce dernier défaut qui rendait le droit à
        # l'effacement si dangereux sur une pièce jointe.
        cls.calendar_anonymise = cls.env["privacy.retention.calendar"].create({
            "name": "Conservation d'essai — anonymisation",
            "code": "TRUTH-ANON",
            "document_type": "contract",
            "legal_basis": "Essai",
            "active_retention_years": 0,
            "destruction_method": "anonymize",
        })

    # ------------------------------------------------------------------
    # Outillage
    # ------------------------------------------------------------------

    def _piece_jointe(self, nom="piece-sensible.txt"):
        """Une pièce jointe qui porte RÉELLEMENT une charge utile."""
        return self.env["ir.attachment"].create({
            "name": nom,
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "datas": base64.b64encode(b"RENSEIGNEMENT PERSONNEL EN CLAIR"),
        })

    def _classification(self, record):
        return self.Classification.create({
            "res_model": record._name,
            "res_id": record.id,
            "pi_category": "identification",
            "retention_calendar_id": self.calendar.id,
        })

    def _demande(self, request_type, classifications, **extra):
        vals = {
            "request_type": request_type,
            "classification_ids": [(6, 0, classifications.ids)],
            "retention_calendar_id": self.calendar.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        }
        if request_type == "erasure_right":
            vals["partner_id"] = self.partner.id
        vals.update(extra)
        return self.Destruction.create(vals)

    def _executer(self, demande):
        demande.with_user(self.manager).action_approve()
        demande.with_user(self.officer).action_execute()
        demande.invalidate_recordset()
        return demande

    # ------------------------------------------------------------------
    # Le défaut principal
    # ------------------------------------------------------------------

    def test_erasure_right_actually_destroys_documents(self):
        """RÉGRESSION — une demande d'effacement doit DÉTRUIRE, pas seulement
        certifier.

        ⚠ Avant correctif, ``erasure_right`` n'était pas dans la liste de
        routage : les pièces restaient intactes dans le filestore et le
        certificat imprimait quand même « Documents détruits ».
        """
        piece = self._piece_jointe()
        self.assertTrue(piece.datas, "Prérequis : la pièce doit porter des octets.")
        classification = self._classification(piece)
        piece_id = piece.id

        demande = self._executer(self._demande("erasure_right", classification))

        self.assertEqual(demande.state, "executed")
        # ⚠ Le contrôle a DURCI avec la 18.0.5.0.0. Il portait
        # sur `piece.datas`, parce que la méthode « Suppression » du socle se
        # contentait d'effacer la charge utile puis d'archiver la pièce. Elle
        # supprime désormais pour de bon : on contrôle donc l'ABSENCE de la
        # ligne, ce qui couvre a fortiori l'absence des octets.
        self.assertFalse(
            self.env["ir.attachment"].with_context(
                active_test=False
            ).browse(piece_id).exists(),
            "La pièce jointe survit à une demande d'effacement : le certificat "
            "certifierait une destruction qui n'a pas eu lieu.",
        )
        self.assertIn(classification, demande.destroyed_classification_ids)
        self.assertFalse(demande.skipped_classification_ids)

    def test_anonymise_erases_attachment_payload(self):
        """⚠ Une pièce jointe ne s'anonymise pas : le renseignement personnel EST
        le contenu du fichier. L'archiver laisserait les octets récupérables
        pendant que le certificat annonce « détruit ». C'est le surclaim le plus
        direct qu'ait porté le module, et la méthode par défaut du droit à
        l'effacement est justement ``anonymize``."""
        piece = self._piece_jointe()
        classification = self._classification(piece)
        demande = self._demande(
            "erasure_right", classification,
            retention_calendar_id=self.calendar_anonymise.id,
        )
        self._executer(demande)
        self.assertEqual(demande.destruction_method_used, "anonymize")

        piece.invalidate_recordset()
        self.assertFalse(piece.datas)
        self.assertIn("EFFACÉ", piece.description or "")

    # ------------------------------------------------------------------
    # Le tableau inversé
    # ------------------------------------------------------------------

    def test_destroyed_and_skipped_lists_do_not_overlap(self):
        """Le certificat lit deux listes figées. Un document ne peut pas être
        dans les deux, ni dans aucune."""
        piece = self._piece_jointe()
        classification = self._classification(piece)
        demande = self._executer(self._demande("document", classification))

        detruits = demande.destroyed_classification_ids
        sautes = demande.skipped_classification_ids
        self.assertFalse(detruits & sautes)
        self.assertEqual(len(detruits) + len(sautes), 1)
        self.assertEqual(demande.destroyed_count, len(detruits))
        self.assertEqual(demande.skipped_count, len(sautes))

    def test_skipped_document_is_not_certified_as_destroyed(self):
        """RÉGRESSION — un document SAUTÉ ne doit jamais figurer aux détruits.

        ⚠ Le chemin « sauté » ne se fabrique qu'en SQL : ``res_model`` est NOT
        NULL et une ``@api.constrains`` refuse un modèle inconnu à l'écriture
        ORM. ``flush_all()`` avant, ``invalidate_all()`` après — sans le flush,
        l'écriture ORM en attente écrase l'``UPDATE`` et le test passe au vert
        en n'ayant rien éprouvé.
        """
        piece = self._piece_jointe()
        classification = self._classification(piece)
        demande = self._demande("document", classification)

        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE privacy_document_classification SET res_model = %s WHERE id = %s",
            ("x.modele.absent", classification.id),
        )
        self.env.invalidate_all()

        self._executer(demande)

        self.assertIn(classification, demande.skipped_classification_ids)
        self.assertNotIn(classification, demande.destroyed_classification_ids)
        self.assertEqual(demande.destroyed_count, 0)
        self.assertEqual(demande.skipped_count, 1)
        self.assertIn("SAUTÉ", demande.execution_log or "")
        piece.invalidate_recordset()
        self.assertTrue(
            piece.datas,
            "Rien ne devait être détruit : la cible était introuvable.",
        )

    # ------------------------------------------------------------------
    # L'empreinte du certificat
    # ------------------------------------------------------------------

    def test_certificate_hash_is_replayable(self):
        """RÉGRESSION — l'empreinte doit être rejouable par un tiers.

        ⚠ Le contenu certifié embarquait ``now()`` : deux appels sur le MÊME
        enregistrement rendaient deux empreintes différentes. Le certificat
        promettait un contrôle d'intégrité que personne ne pouvait jouer.
        """
        piece = self._piece_jointe()
        demande = self._executer(
            self._demande("document", self._classification(piece))
        )

        self.assertTrue(demande.verification_hash)
        self.assertTrue(
            demande.certificate_content,
            "Sans contenu persisté, l'empreinte n'est vérifiable par personne.",
        )
        self.assertIs(demande.verify_certificate_integrity(), True)
        # Rejouer deux fois doit rendre le même verdict.
        self.assertIs(demande.verify_certificate_integrity(), True)

    def test_certificate_integrity_detects_tampering(self):
        piece = self._piece_jointe()
        demande = self._executer(
            self._demande("document", self._classification(piece))
        )
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE privacy_destruction_request SET certificate_content = %s WHERE id = %s",
            ("CONTENU FALSIFIÉ", demande.id),
        )
        self.env.invalidate_all()
        self.assertIs(demande.verify_certificate_integrity(), False)

    def test_legacy_certificate_returns_none_not_false(self):
        """Une pièce antérieure à la persistance du contenu n'a rien à rejouer.

        ⚠ Répondre ``False`` la ferait passer pour altérée. ``None`` dit la
        vérité : il n'y a rien à vérifier. C'est la mention portée par les deux
        certificats déjà émis en production.
        """
        piece = self._piece_jointe()
        demande = self._executer(
            self._demande("document", self._classification(piece))
        )
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE privacy_destruction_request SET certificate_content = NULL WHERE id = %s",
            (demande.id,),
        )
        self.env.invalidate_all()
        self.assertIsNone(demande.verify_certificate_integrity())

    # ------------------------------------------------------------------
    # Le registre
    # ------------------------------------------------------------------

    def test_register_keeps_categories_after_successful_destruction(self):
        """⚠ Une destruction réussie ARCHIVE la classification. Le registre qui
        lisait le many2many actif perdait donc ses catégories sur exactement les
        destructions qui avaient fonctionné."""
        piece = self._piece_jointe()
        classification = self._classification(piece)
        demande = self._executer(self._demande("document", classification))

        entrees = demande.register_entry_ids
        self.assertTrue(entrees, "Aucune entrée au registre après exécution.")
        categories = " ".join(
            (e.pi_categories or "") if "pi_categories" in e._fields else ""
            for e in entrees
        )
        if "pi_categories" in entrees._fields:
            self.assertTrue(
                categories.strip(),
                "Le registre a perdu ses catégories sur une destruction réussie.",
            )
