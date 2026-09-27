"""Trois pièces, trois statuts, et ce que la signature ne fait PAS.

Le pont est court ; l'essentiel de ce qu'il y a à éprouver est ce qu'il refuse
de laisser croire.

1. **Signer n'est pas remettre** (art. 1068.1). Les quinze jours se comptent de
   la demande à la remise, et la signature ne les arrête pas.
2. **Signer n'est pas transmettre** (art. 1102.1 et 1086.1). Les deux articles
   exigent la transmission, pas la signature.
3. **Le module ne devine pas qui signe pour le syndicat.** Un seul des trois
   documents a un signataire par défaut, parce que c'est le seul dont la
   personne est une donnée du dossier.
"""
from datetime import date

from odoo import fields
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPropertySign(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {
                "name": "Syndicat qui signe",
                "fraction_base": 1000,
                # Art. 1068.1 al. 3 : l'attestation n'existe qu'après la perte
                # de contrôle du promoteur, et le module la refuse sans cette
                # date plutôt que de la supposer.
                "promoter_handover_date": date(2015, 6, 1),
            }
        )
        cls.building = cls.env["bf.property.building"].create(
            {
                "name": "Immeuble qui signe",
                "organisation_id": cls.syndicat.id,
                "floors_above_ground": 6,
                "common_areas_in_building": True,
            }
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {
                "name": "201",
                "building_id": cls.building.id,
                "quote_part": 1000.0,
            }
        )
        cls.expert = cls.env["res.partner"].create(
            {
                "name": "Technologue Essai",
                "email": "technologue@example.invalid",
            }
        )

    def _attestation(self, **kw):
        vals = {
            "organisation_id": self.syndicat.id,
            "unit_id": self.unit.id,
            "requester_partner_id": self.expert.id,
            "request_date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        return self.env["bf.property.attestation"].create(vals)

    def _assembly(self, **kw):
        vals = {
            "name": "Assemblée annuelle",
            "organisation_id": self.syndicat.id,
            "date": fields.Datetime.now(),
        }
        vals.update(kw)
        return self.env["bf.property.assembly"].create(vals)

    def _council(self, **kw):
        vals = {
            "name": "Réunion du conseil",
            "organisation_id": self.syndicat.id,
            "date": fields.Datetime.now(),
        }
        vals.update(kw)
        return self.env["bf.property.council.meeting"].create(vals)

    def _log(self, **kw):
        vals = {
            "name": "Carnet à signer",
            "organisation_id": self.syndicat.id,
            "building_id": self.building.id,
            "established_date": fields.Date.context_today(self.syndicat),
            "author_partner_id": self.expert.id,
            "author_order": "technologist",
            "author_practice": True,
            "author_independent": True,
            "site_declaration": True,
            "site_declaration_date": fields.Date.context_today(self.syndicat),
        }
        vals.update(kw)
        log = self.env["bf.property.maintenance.log"].create(vals)
        self.env["bf.property.maintenance.item"].create(
            {
                "log_id": log.id,
                "name": "Toiture",
                "condition": "fair",
                "remaining_life_years": 8,
                "major_work": "Réfection de la membrane",
                "major_work_year": log.established_date.year + 8,
                "major_work_cost": 120000.0,
            }
        )
        return log

    def _signed_request(self, record, signed_on=None, signer_name="Tremblay"):
        """Une demande de signature signée, sans rendre de PDF.

        ⚠️ Le harnais ne passe pas par `create_from_record` : celui-là rend le
        document, donc fait tourner le moteur PDF, qui n'a rien à voir avec ce
        qui est éprouvé ici. Ce qui compte est le crochet et ce qu'il écrit.
        """
        return self.env["bf.sign.request"].create(
            {
                "res_model": record._name,
                "res_id": record.id,
                "company_id": self.env.company.id,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "name": signer_name,
                            "email": "technologue@example.invalid",
                            "signed_on": signed_on
                            or fields.Datetime.now(),
                        },
                    )
                ],
            }
        )

    # ── Qui signe, et qui n'est pas deviné ──

    def test_the_carnet_proposes_its_author_and_nobody_else(self):
        """r. 8.01, art. 1 : le règlement borne qui peut établir un carnet.

        C'est le seul des trois documents dont le signataire est une donnée du
        dossier : le module le propose, et il n'a pas à demander.
        """
        log = self._log()
        signers = log._sign_default_signers()
        self.assertEqual(len(signers), 1)
        self.assertEqual(signers[0]["partner_id"], self.expert.id)

    def test_an_author_without_an_email_is_not_invented(self):
        mute = self.env["res.partner"].create({"name": "Sans courriel"})
        log = self._log(author_partner_id=mute.id)
        self.assertEqual(log._sign_default_signers(), [])

    def test_the_syndicat_signatory_is_asked_never_guessed(self):
        """⚠️ La suite ne modélise pas la composition du conseil.

        La déclaration de copropriété la fixe et elle varie d'un immeuble à
        l'autre. Proposer quelqu'un ici donnerait l'air d'être le bon à une
        personne choisie par le logiciel.
        """
        for record in (self._attestation(), self._assembly(), self._council()):
            self.assertEqual(
                record._sign_default_signers(), [], record._name
            )

    # ── Ce que la signature écrit, et ce qu'elle n'écrit pas ──

    def test_the_signature_carries_the_declaration_of_article_6(self):
        """🔴 Une case disait « quelqu'un affirme que le professionnel a déclaré ».

        L'art. 6 veut que le professionnel déclare lui-même. La signature coche
        la case et pose la date, et la date vient de la signature, pas du jour.
        """
        log = self._log(site_declaration=False, site_declaration_date=False)
        self.assertFalse(log.site_declaration)
        signed_on = fields.Datetime.to_datetime("2026-06-11 14:00:00")
        log._sign_on_signed(self._signed_request(log, signed_on=signed_on))
        self.assertTrue(log.site_declaration)
        self.assertEqual(log.site_declaration_date, date(2026, 6, 11))
        self.assertIn(
            "art. 6", "".join(log.message_ids.mapped("body"))
        )

    def test_a_paper_declaration_keeps_its_own_date(self):
        """Un syndicat qui avait une déclaration papier ne la voit pas redatée."""
        paper = date(2026, 2, 3)
        log = self._log(site_declaration=True, site_declaration_date=paper)
        log._sign_on_signed(
            self._signed_request(
                log, signed_on=fields.Datetime.to_datetime("2026-06-11 14:00:00")
            )
        )
        self.assertEqual(log.site_declaration_date, paper)

    def test_signing_the_attestation_is_not_issuing_it(self):
        """🔴 Art. 1068.1 : quinze jours de la demande, arrêtés par la REMISE.

        Croire que signer suffit ferait rater un délai que le module suit par
        ailleurs avec soin.
        """
        attestation = self._attestation()
        before = attestation.issued_date
        attestation._sign_on_signed(self._signed_request(attestation))
        self.assertEqual(attestation.issued_date, before)
        self.assertIn(
            "Signer n'est pas",
            "".join(attestation.message_ids.mapped("body")),
        )

    def test_signing_the_minutes_is_not_transmitting_them(self):
        """🔴 Art. 1102.1 et 1086.1 : les deux exigent la TRANSMISSION.

        Ni l'un ni l'autre ne parle de signature. Un procès-verbal signé n'est
        pas transmis pour autant, et l'échéance de 30 jours continue de se
        compter sur la seule transmission.
        """
        assembly = self._assembly(minutes="<p>Adoption du budget.</p>")
        council = self._council(
            minutes="<p>Octroi du contrat de déneigement.</p>"
        )
        for record, article in ((assembly, "1102.1"), (council, "1086.1")):
            record._sign_on_signed(self._signed_request(record))
            self.assertFalse(record.minutes_sent_date, record._name)
            self.assertIn(
                article, "".join(record.message_ids.mapped("body"))
            )

    # ── Ce qui doit exister pour que le pont serve ──

    def test_every_wired_document_has_a_report_that_exists(self):
        """Un renvoi de rapport qui ne résout pas ne se voit qu'à l'usage.

        La liste vient du registre — les modèles de la suite qui héritent du
        mixin — et non d'une énumération tapée à la main : brancher une pièce de
        plus sans son rapport fait échouer ce test.
        """
        # `sign_request_count` ne vient que du mixin : sa présence désigne
        # exactement les modèles que ce pont a branchés.
        wired = sorted(
            name
            for name in self.env.registry.models
            if name.startswith("bf.property.")
            and "sign_request_count" in self.env[name]._fields
        )
        self.assertGreaterEqual(len(wired), 4, wired)
        for name in wired:
            model = self.env[name]
            ref = model._sign_report_ref()
            self.assertTrue(ref, name)
            self.assertTrue(
                self.env.ref(ref, raise_if_not_found=False),
                "%s renvoie à un rapport introuvable : %s" % (name, ref),
            )


@tagged("post_install", "-at_install")
class TestManagerCanUseTheBridge(TransactionCase):
    """🔴 Sans ce droit, le gestionnaire voyait le bouton sans
    pouvoir s'en servir. Il tient l'outil au niveau utilisateur, jamais admin."""

    def test_the_manager_holds_the_tool_as_a_user(self):
        manager = self.env["res.users"].create({
            "name": "Gestionnaire outillé", "login": "gest_outil_bf_property_sign",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_property_core.group_bf_property_manager").id])],
        })
        self.assertTrue(manager.has_group("bf_sign.group_sign_user"))
        self.assertFalse(manager.has_group("bf_sign.group_sign_manager"))
