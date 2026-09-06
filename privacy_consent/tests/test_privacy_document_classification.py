from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("privacy_consent", "privacy_document_classification")
class TestPrivacyDocumentClassification(TransactionCase):
    """Test cases for document classification (v3.0.0)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({
            "name": "Classification Subject",
            "email": "subject@example.com",
        })
        cls.calendar = cls.env["privacy.retention.calendar"].create({
            "name": "Test contracts",
            "code": "TEST-CLASS",
            "document_type": "contract",
            "legal_basis": "Test",
            "active_retention_years": 2,
            "semi_active_retention_years": 0,
        })

    def test_create_classification(self):
        """Classification is created with required fields."""
        cls_record = self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "identification",
            "sensitivity_level": "confidential",
            "subject_partner_id": self.partner.id,
            "retention_calendar_id": self.calendar.id,
        })
        self.assertEqual(cls_record.res_name, self.partner.display_name)
        self.assertTrue(cls_record.classified_at)

    def test_retention_expiry_uses_document_date(self):
        """retention_expiry_date = document_date + total_retention_days (preferred)."""
        doc_date = fields.Date.from_string("2020-01-01")
        cls_record = self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "identification",
            "retention_calendar_id": self.calendar.id,
            "document_date": doc_date,
        })
        expected = doc_date + timedelta(days=2 * 365)
        self.assertEqual(cls_record.retention_expiry_date, expected)

    def test_retention_expiry_falls_back_to_classified_at(self):
        """Without document_date, expiry falls back to classified_at."""
        classified_at = fields.Datetime.from_string("2026-01-01 10:00:00")
        cls_record = self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "identification",
            "retention_calendar_id": self.calendar.id,
            "classified_at": classified_at,
        })
        expected = classified_at.date() + timedelta(days=2 * 365)
        self.assertEqual(cls_record.retention_expiry_date, expected)

    def test_retention_expiry_recomputes_on_calendar_change(self):
        """Changing the calendar rule recomputes expiry."""
        doc_date = fields.Date.from_string("2024-06-15")
        cls_record = self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "identification",
            "retention_calendar_id": self.calendar.id,
            "document_date": doc_date,
        })
        short_rule = self.env["privacy.retention.calendar"].create({
            "name": "Short",
            "code": "SHORT-TEST",
            "document_type": "other",
            "legal_basis": "Test",
            "active_retention_years": 0,
            "semi_active_retention_years": 0,
        })
        cls_record.retention_calendar_id = short_rule
        self.assertEqual(cls_record.retention_expiry_date, doc_date)

    def test_ondelete_restrict_on_referenced_calendar(self):
        """Deleting a calendar rule referenced by a classification is blocked."""
        ref_rule = self.env["privacy.retention.calendar"].create({
            "name": "To delete",
            "code": "DEL-TEST",
            "document_type": "other",
            "legal_basis": "Test",
        })
        self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "other",
            "retention_calendar_id": ref_rule.id,
        })
        with self.assertRaises(Exception):
            ref_rule.unlink()

    def test_disallowed_model_raises(self):
        """Classifying a system model raises ValidationError."""
        with self.assertRaises(ValidationError):
            self.env["privacy.document.classification"].create({
                "res_model": "res.users",
                "res_id": self.env.user.id,
                "pi_category": "identification",
            })

    def test_unique_per_category(self):
        """Same (model, id, category, company) cannot be classified twice."""
        self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "financial",
        })
        with self.assertRaises(Exception):
            self.env["privacy.document.classification"].create({
                "res_model": "res.partner",
                "res_id": self.partner.id,
                "pi_category": "financial",
            })

    def test_action_view_source_record(self):
        """action_view_source_record returns an act_window to the classified record."""
        cls_record = self.env["privacy.document.classification"].create({
            "res_model": "res.partner",
            "res_id": self.partner.id,
            "pi_category": "identification",
        })
        action = cls_record.action_view_source_record()
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(action["res_id"], self.partner.id)

    # === Point d'extension ===

    def test_classifiable_models_defaults_to_constant(self):
        """Sans surcharge, le point d'extension rend la constante."""
        Classification = self.env["privacy.document.classification"]
        self.assertEqual(
            Classification._privacy_classifiable_models(),
            set(Classification.ALLOWED_MODELS),
        )

    def test_enterprise_only_models_are_not_classifiable(self):
        """Les modèles Odoo Enterprise absents d'ici ne sont plus offerts.

        `sign`, `documents` et `hr_payroll` ne sont installés sur aucun de nos
        locataires : les lister laissait croire qu'une demande d'effacement
        pouvait les atteindre. Notre module de signature est `bf_sign`, dont
        le modèle est `bf.sign.request` — ajouté par le pont, pas ici.
        """
        allowed = self.env["privacy.document.classification"]._privacy_classifiable_models()
        for model in ("sign.request", "sign.request.item",
                      "documents.document", "hr.payslip"):
            self.assertNotIn(model, allowed)

    def test_override_extends_allowed_models(self):
        """Une surcharge du point d'extension rend un modèle classifiable.

        C'est le contrat sur lequel s'appuient les ponts vie privée : la
        contrainte doit lire la méthode, pas la constante.
        """
        Classification = self.env["privacy.document.classification"]
        base = Classification._privacy_classifiable_models()
        self.assertNotIn("res.country", base)

        with patch.object(
            type(Classification),
            "_privacy_classifiable_models",
            lambda self: base | {"res.country"},
        ):
            record = Classification.create({
                "res_model": "res.country",
                "res_id": self.env.ref("base.ca").id,
                "pi_category": "other",
            })
            self.assertTrue(record.id)

    def test_constraint_still_refuses_outside_the_override(self):
        """La surcharge n'ouvre que ce qu'elle nomme, pas le reste."""
        Classification = self.env["privacy.document.classification"]
        base = Classification._privacy_classifiable_models()
        with patch.object(
            type(Classification),
            "_privacy_classifiable_models",
            lambda self: base | {"res.country"},
        ):
            with self.assertRaises(ValidationError):
                Classification.create({
                    "res_model": "ir.module.module",
                    "res_id": 1,
                    "pi_category": "other",
                })
