"""« Anonymiser » doit anonymiser, et rien ne se certifie à vide.

Le module rendait l'étiquette « anonymisation » quoi qu'il
arrive ::

    if method == "anonymize":
        applied = "anonymisation"
        if "active" in record._fields:
            record.sudo().write({"active": False})
        if "notes" in record._fields:
            record.sudo().write({"notes": f"[ANONYMISÉ le {fields.Date.today()}]"})
        return applied

Relevé sur l'ensemble classifiable réel de chaque locataire : sur
les dizaines de modèles classifiables d'une base réelle, **une majorité ne
    subissait rien du tout**, d'autres n'étaient que
archivés (dont ``res.partner``, le cas le plus courant), 7 voyaient leur champ
``notes`` écrasé, et seule la pièce jointe était vraiment effacée. Aucun champ
identifiant n'était jamais réécrit, et le registre scellait une anonymisation.

Quatre volets, tous couverts ici :

1. le contrat : un modèle qui ne déclare pas ``_privacy_anonymize()`` fait
   refuser l'opération, et le document tombe dans « non détruits » ;
2. les anonymiseurs du socle : pièce jointe, fiche de contact, preuve de
   consentement, et le refus motivé du consentement lui-même ;
3. la portée au registre : une anonymisation s'inscrit en portée partielle en
   nommant les champs vidés, jamais en « enregistrement entier » ;
4. **rien détruit, rien certifié** : une demande sans effet ne rend ni
   certificat ni entrée de registre.

⚠ Ce fichier est écrit pour rester identique entre ``cq_consent`` et
``privacy_consent`` : les groupes sont résolus sans préfixe de module en dur, et
rien n'importe le module par son chemin.
"""
import copy
import json
import pickle
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

MODULES = ("cq_consent", "privacy_consent")


@tagged("privacy_consent", "privacy_destruction_truth", "t27007")
class TestAnonymisationContrat(TransactionCase):

    @classmethod
    def _groupe(cls, code):
        for module in MODULES:
            groupe = cls.env.ref(f"{module}.{code}", raise_if_not_found=False)
            if groupe:
                return groupe
        raise AssertionError(f"Groupe {code} introuvable dans {MODULES}.")

    @classmethod
    def _ops(cls):
        """Le module de la primitive, sans chemin en dur.

        ⚠ `cq_consent` ne le porte pas encore : la boucle essaie les
        deux noms et prend celui qui répond, comme `_groupe`.
        """
        for module in MODULES:
            try:
                return import_module(f"odoo.addons.{module}.models.privacy_destruction_ops")
            except ImportError:
                continue
        raise AssertionError(f"privacy_destruction_ops introuvable dans {MODULES}.")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Campaign = cls.env["privacy.destruction.campaign"]
        cls.Classification = cls.env["privacy.document.classification"]
        cls.Register = cls.env["privacy.destruction.register"]
        cls.Destruction = cls.env["privacy.destruction.request"]

        # ⚠ `base.group_partner_manager` est indispensable : l'exécuteur contrôle
        # le droit d'écriture sur la cible avant d'escalader en sudo.
        cls.officer = cls.env["res.users"].create({
            "name": "Responsable 27007",
            "login": "test_officer_27007",
            "email": "officer-27007@example.invalid",
            "groups_id": [
                (4, cls._groupe("group_privacy_officer").id),
                (4, cls.env.ref("base.group_partner_manager").id),
            ],
        })
        # Deux calendriers : l'un anonymise, l'autre supprime.
        cls.calendrier_anonymise = cls.env["privacy.retention.calendar"].create({
            "name": "Conservation 27007 (anonymisation)",
            "code": "T27007A",
            "document_type": "contract",
            "legal_basis": "Essai",
            "active_retention_years": 0,
            "semi_active_retention_years": 0,
            "destruction_method": "anonymize",
        })

    # ------------------------------------------------------------------
    # Outillage
    # ------------------------------------------------------------------

    def _contact(self, nom="Cible 27007"):
        """Une fiche NEUVE : elle porte `active` et aucune pièce comptable."""
        return self.env["res.partner"].create({
            "name": nom,
            "email": f"cible-{nom.lower().replace(' ', '-')}@example.invalid",
            "phone": "+1 555 0100",
            "street": "1 rue de l'Essai",
            "city": "Québec",
            "comment": "Note qui nomme la personne.",
        })

    def _classer(self, record, calendrier=None, sujet=None):
        return self.Classification.create({
            "res_model": record._name,
            "res_id": record.id,
            "pi_category": "identification",
            "retention_calendar_id": (calendrier or self.calendrier_anonymise).id,
            "subject_partner_id": sujet.id if sujet else False,
        })

    def _demande(self, classifications, methode="anonymize", type_demande="document"):
        demande = self.Destruction.create({
            "request_type": type_demande,
            "classification_ids": [(6, 0, classifications.ids)],
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id
            if methode == "anonymize" else False,
        })
        demande.with_user(self.officer).action_approve()
        return demande

    def _campagne(self, nom="Campagne 27007", calendrier=None):
        campagne = self.Campaign.create({
            "name": nom,
            "retention_calendar_id": (calendrier or self.calendrier_anonymise).id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campagne.action_scan()
        campagne.with_user(self.officer).action_approve()
        return campagne

    def _calendrier(self, code, methode):
        return self.env["privacy.retention.calendar"].create({
            "name": f"Conservation 27007 ({methode})",
            "code": code,
            "document_type": "contract",
            "legal_basis": "Essai",
            "active_retention_years": 0,
            "semi_active_retention_years": 0,
            "destruction_method": methode,
        })

    def _piece(self, nom):
        return self.env["ir.attachment"].create({
            "name": nom,
            "datas": "Qm9uam91cg==",
        })

    def _journal_et_compte(self):
        """Le journal de vente et un compte de produits utilisables, sinon (False, False).

        ⚠ Un compte DÉPRÉCIÉ fait lever `action_post` (« le compte est
        déprécié ») : le compte par défaut d'un journal réel l'est.
        """
        if "account.move" not in self.env:
            return False, False
        # ⚠ **Pas de journal à inaltérabilité** : `test_le_refus_comptable_compte_les_trois_canaux` écrit
        # `partner_id` sur une LIGNE d'une pièce comptabilisée, et un journal qui
        # active `restrict_mode_hash_table` refuse cette écriture, et l'essai
        # tombait alors en ERREUR, pas en échec, chez le premier locataire qui
        # scelle ses journaux de vente. Ce qui est mesuré ici, c'est le compteur
        # du refus comptable, pas la séquence inaltérable.
        domaine = [("type", "=", "sale"), ("company_id", "=", self.env.company.id)]
        if "restrict_mode_hash_table" in self.env["account.journal"]._fields:
            domaine.append(("restrict_mode_hash_table", "=", False))
        journal = self.env["account.journal"].search(domaine, limit=1)
        if not journal:
            return False, False
        compte = journal.default_account_id.filtered(lambda a: not a.deprecated)
        if not compte:
            compte = self.env["account.account"].search(
                [("account_type", "=", "income"), ("deprecated", "=", False)], limit=1)
        return journal, compte

    def _facture(self, journal, compte, partner, **extra):
        vals = {
            "move_type": "out_invoice",
            "partner_id": partner.id,
            "journal_id": journal.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [(0, 0, {
                "name": "Ligne d'essai 27007",
                "quantity": 1,
                "price_unit": 10.0,
                "account_id": compte.id,
                "tax_ids": [(5, 0, 0)],
            })],
        }
        vals.update(extra)
        return self.env["account.move"].create(vals)

    def _vals_registre(self, **extra):
        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": self.env.user.id,
            "approved_by_id": self.env.user.id,
            "document_description": "Entrée d'essai 27007.",
            "destruction_method": "anonymize",
            "legal_basis": "Art. 23 LPRPSP (essai)",
            "company_id": self.env.company.id,
        }
        vals.update(extra)
        return vals

    # ------------------------------------------------------------------
    # 1. Le contrat
    # ------------------------------------------------------------------

    def test_un_modele_muet_fait_refuser_lanonymisation(self):
        """Un modèle sans `_privacy_anonymize()` n'est pas anonymisé en silence.

        `privacy.purpose` sert de témoin : c'est un modèle du socle que personne
        n'a appris à anonymiser. Avant le correctif, l'appel se contentait de
        l'archiver et rendait pourtant l'étiquette « anonymisation », que
        l'appelant écrivait au journal sous la forme « DÉTRUIT (anonymisation) ».

        ⚠ La contrainte `_check_allowed_model` refuse de classer un modèle hors
        liste, et une `ValidationError` attrapée en Python n'abandonne pas la
        transaction : on classe donc une cible permise, puis on repointe la ligne
        en SQL, comme le fait le test du socle.
        """
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité muette 27007",
            "code": "T27007MUET",
        })
        contact = self._contact("Porteur de classification 27007")
        classification = self._classer(contact, sujet=contact)
        self.env.cr.execute(
            "UPDATE privacy_document_classification "
            "SET res_model = %s, res_id = %s WHERE id = %s",
            ("privacy.purpose", finalite.id, classification.id),
        )
        classification.invalidate_recordset(["res_model", "res_id"])

        demande = self._demande(classification)

        # ⚠ Le journal se lit HORS du bloc `assertRaises` : Odoo y entre un point
        # de sauvegarde (`BaseCase._assertRaises`) et le défait quand l'exception
        # attendue tombe, donc TOUT ce que le bloc écrit est annulé. On joue donc
        # le volet documentaire seul, qui consigne sans lever, puis on éprouve la
        # garde de certification à part.
        demande.with_user(self.officer)._execute_document_destruction("anonymize")
        self.assertIn("SAUTÉ", demande.execution_log or "")
        self.assertIn(
            "ne déclare pas comment s'anonymiser", demande.execution_log or "",
            "Le journal doit dire POURQUOI, et pas seulement que c'est sauté.",
        )
        self.assertFalse(demande.applied_effects)
        self.assertIn(classification, demande.skipped_classification_ids)
        self.assertNotIn(classification, demande.destroyed_classification_ids)
        self.assertTrue(
            self.env["privacy.purpose"].browse(finalite.id).exists(),
            "Le témoin ne doit pas avoir été touché.",
        )

        # La demande s'exécute quand même, et c'est voulu : depuis la garde du socle le
        # certificat porte une section « Documents NON détruits » qui nomme
        # l'échec. Ce qui compte ici, c'est que le document refusé n'entre JAMAIS
        # dans les détruits, donc ni au tableau du certificat ni au registre.
        demande.with_user(self.officer).action_execute()
        self.assertEqual(demande.state, "executed")
        self.assertEqual(demande.destroyed_count, 0)
        self.assertEqual(demande.skipped_count, 1)
        self.assertFalse(demande.applied_effects)

    # ------------------------------------------------------------------
    # 2. Les anonymiseurs du socle
    # ------------------------------------------------------------------

    def test_une_piece_jointe_perd_sa_charge_utile(self):
        piece = self.env["ir.attachment"].create({
            "name": "preuve-27007.txt",
            "datas": "Qm9uam91cg==",
        })
        classification = self._classer(piece)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()

        self.assertFalse(piece.datas, "La charge utile doit être vide.")
        self.assertIn("EFFACÉ", piece.description or "")
        self.assertEqual(demande.destroyed_count, 1)
        self.assertIn("datas", demande.execution_log)
        self.assertTrue(demande.applied_effects)

    def test_un_contact_perd_ce_qui_lidentifie(self):
        contact = self._contact("Contact anonymisable 27007")
        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()

        contact.invalidate_recordset()
        self.assertFalse(contact.email, "Le courriel doit être vidé.")
        self.assertFalse(contact.phone, "Le téléphone doit être vidé.")
        self.assertFalse(contact.street)
        self.assertFalse(contact.comment)
        self.assertIn("anonymis", (contact.name or "").lower())
        self.assertFalse(contact.active, "La fiche anonymisée est aussi archivée.")
        self.assertIn("email", demande.execution_log)

    def test_le_refus_comptable_vaut_sans_comptabilite_installee(self):
        """La règle du refus comptable s'éprouve SANS comptabilité.

        🔴 L'unique preuve du refus
        comptable portait trois `skipTest`, donc chez un locataire sans
        comptabilité (ou sans journal de vente utilisable) elle se taisait sans
        rien dire au bilan, et le refus n'était plus éprouvé du tout. Ici le
        compteur est bouchonné : ce qui est mesuré, c'est la décision prise
        quand il rend autre chose que zéro, et ça tourne partout.
        """
        contact = self._contact("Refus comptable bouchonné 27007")
        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        with patch.object(type(contact), "_privacy_accounting_holds", lambda self: 2):
            demande.with_user(self.officer)._execute_document_destruction("anonymize")

        contact.invalidate_recordset()
        self.assertEqual(contact.name, "Refus comptable bouchonné 27007")
        self.assertTrue(contact.email, "Rien ne doit avoir été vidé.")
        self.assertTrue(contact.active)
        self.assertIn("comptable", demande.execution_log or "")
        self.assertFalse(demande.applied_effects)
        self.assertIn(classification, demande.skipped_classification_ids)
        self.assertEqual(demande.destroyed_count, 0)

    def test_le_refus_comptable_compte_les_trois_canaux(self):
        """Une pièce comptabilisée qui NOMME la personne, par l'un des trois canaux.

        🔴 Le compteur ne lisait que
        `account.move.partner_id` : une fiche « Adresse de livraison », nommée
        par `partner_shipping_id` seulement, rendait 0 et s'anonymisait, et des
        pièces conservées six ans imprimaient « Personne anonymisée » en adresse
        de livraison. Une personne portée seulement par une LIGNE d'écriture
        passait aussi.
        """
        journal, compte = self._journal_et_compte()
        if not (journal and compte):
            self.skipTest(
                "Aucun journal de vente avec compte utilisable : les trois "
                "canaux se mesurent sur une vraie pièce comptabilisée. La "
                "DÉCISION du refus est éprouvée sans comptabilité par "
                "test_le_refus_comptable_vaut_sans_comptabilite_installee."
            )
        payeur = self._contact("Payeur 27007")

        livre = self._contact("Livré seulement 27007")
        facture = self._facture(journal, compte, payeur)
        facture.partner_shipping_id = livre.id
        facture.action_post()
        self.assertEqual(facture.state, "posted")
        self.assertEqual(facture.partner_shipping_id, livre)
        self.assertTrue(
            livre._privacy_accounting_holds(),
            "Une adresse de livraison est nommée par la pièce : elle retient.",
        )

        nomme_par_la_ligne = self._contact("Nommé par une ligne 27007")
        seconde = self._facture(journal, compte, payeur)
        seconde.action_post()
        # ⚠ La ligne se nomme APRÈS la comptabilisation : `partner_id` d'une
        # ligne est un champ calculé stocké sur le partenaire de la pièce, et
        # `action_post` le recalcule, donc une écriture faite avant est perdue.
        seconde.invoice_line_ids[0].partner_id = nomme_par_la_ligne.id
        self.assertEqual(seconde.state, "posted")
        self.assertEqual(seconde.invoice_line_ids[0].partner_id, nomme_par_la_ligne)
        self.assertTrue(
            nomme_par_la_ligne._privacy_accounting_holds(),
            "Une ligne d'écriture comptabilisée nomme la personne : elle retient.",
        )

        # Et le refus se consigne, bout en bout, pour l'adresse de livraison.
        classification = self._classer(livre, sujet=livre)
        demande = self._demande(classification)
        demande.with_user(self.officer)._execute_document_destruction("anonymize")
        livre.invalidate_recordset()
        self.assertEqual(livre.name, "Livré seulement 27007")
        self.assertIn("comptable", demande.execution_log or "")
        self.assertEqual(demande.destroyed_count, 0)

    def test_une_facture_annulee_ne_retient_pas_lanonymisation(self):
        """`state != "draft"` englobait `cancel` : un faux refus prive d'un droit.

        🔴 Une pièce
        ANNULÉE n'est pas une facture émise : elle ne porte plus rien au client,
        et refuser l'effacement pour elle empêche une personne d'exercer son
        droit. L'état retenu est `posted` SEULEMENT.
        """
        journal, compte = self._journal_et_compte()
        if not (journal and compte):
            self.skipTest("Aucun journal de vente avec compte utilisable.")
        contact = self._contact("Facture annulée 27007")
        facture = self._facture(journal, compte, contact)
        facture.action_post()
        facture.button_cancel()
        self.assertEqual(facture.state, "cancel")
        self.assertEqual(
            contact._privacy_accounting_holds(), 0,
            "Une pièce annulée ne retient pas l'anonymisation.",
        )

        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()
        contact.invalidate_recordset()
        self.assertFalse(contact.email, "Le droit doit avoir été honoré.")
        self.assertEqual(demande.destroyed_count, 1)

    def test_un_contact_avec_une_facture_refuse_lanonymisation(self):
        """La pièce comptable arrête le geste."""
        journal, compte = self._journal_et_compte()
        if not (journal and compte):
            self.skipTest(
                "Aucun journal de vente avec compte utilisable ; la décision "
                "du refus est éprouvée sans comptabilité par "
                "test_le_refus_comptable_vaut_sans_comptabilite_installee."
            )
        contact = self._contact("Contact facturé 27007")
        facture = self._facture(journal, compte, contact)
        facture.action_post()
        self.assertEqual(facture.state, "posted")

        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        # Le volet documentaire consigne le refus sans lever (voir la note sur
        # `assertRaises` dans l'essai du modèle muet).
        demande.with_user(self.officer)._execute_document_destruction("anonymize")

        contact.invalidate_recordset()
        self.assertTrue(contact.email, "Rien ne doit avoir été vidé.")
        self.assertTrue(contact.active)
        self.assertIn("comptable", demande.execution_log or "")
        self.assertFalse(demande.applied_effects)
        self.assertIn(classification, demande.skipped_classification_ids)
        self.assertEqual(demande.destroyed_count, 0)

    def test_un_contact_avec_un_compte_usager_refuse_lanonymisation(self):
        """Le compte usager arrête le geste.

        Sans ce refus, anonymiser la fiche d'un employé renommait son compte
        « Personne anonymisée » et archivait la fiche, sans retirer un seul
        accès : le compte gardait ses groupes, ses jetons et son historique.
        """
        contact = self._contact("Employé 27007")
        self.env["res.users"].create({
            "partner_id": contact.id,
            "login": "compte_usager_27007",
        })

        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        # Le volet documentaire consigne le refus sans lever (voir la note sur
        # `assertRaises` dans l'essai du modèle muet).
        demande.with_user(self.officer)._execute_document_destruction("anonymize")

        contact.invalidate_recordset()
        self.assertEqual(contact.name, "Employé 27007", "Rien ne doit avoir bougé.")
        self.assertTrue(contact.email)
        self.assertTrue(contact.active)
        self.assertIn("compte", (demande.execution_log or "").lower())
        self.assertFalse(demande.applied_effects)
        self.assertIn(classification, demande.skipped_classification_ids)
        self.assertEqual(demande.destroyed_count, 0)

    def test_un_compte_usager_archive_refuse_aussi(self):
        """Un compte archivé garde ses accès : il retient l'anonymisation.

        Une autre forme envisagée offrait « refuser seulement si le compte
        est actif » ; le refus simple a été retenu. Cet essai est ce qui
        distingue les deux règles, donc ce qui tue le mutant qui compterait
        seulement les comptes actifs.
        """
        contact = self._contact("Employé parti 27007")
        compte = self.env["res.users"].create({
            "partner_id": contact.id,
            "login": "compte_archive_27007",
        })
        compte.action_archive()
        self.assertFalse(compte.active)

        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        demande.with_user(self.officer)._execute_document_destruction("anonymize")

        contact.invalidate_recordset()
        self.assertEqual(contact.name, "Employé parti 27007")
        self.assertTrue(contact.email)
        self.assertIn("compte", (demande.execution_log or "").lower())
        self.assertFalse(demande.applied_effects)
        self.assertEqual(demande.destroyed_count, 0)

    def test_un_consentement_refuse_lanonymisation(self):
        """Son sujet est obligatoire : la personne reste désignée quoi qu'on vide."""
        contact = self._contact("Sujet consentement 27007")
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité 27007",
            "code": "T27007C",
        })
        consentement = self.env["privacy.consent"].create({
            "subject_partner_id": contact.id,
            "purpose_id": finalite.id,
            "notes": "Note d'origine qui doit survivre au refus.",
        })
        demande = self.Destruction.create({
            "request_type": "consent",
            "consent_id": consentement.id,
            "partner_id": contact.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id,
        })
        demande.with_user(self.officer).action_approve()
        # Le refus se consigne sans lever ; la levée vient de la garde de
        # certification, et un `assertRaises` annulerait le journal avec son
        # point de sauvegarde.
        demande.with_user(self.officer)._execute_destruction("anonymize")

        consentement.invalidate_recordset()
        self.assertTrue(
            consentement.active,
            "Le consentement ne doit pas être archivé par une anonymisation refusée.",
        )
        self.assertIn("origine", consentement.notes or "")
        self.assertIn("NON anonymisé", demande.execution_log or "")
        self.assertFalse(demande.applied_effects)

        with self.assertRaises(UserError):
            demande.with_user(self.officer).action_execute()

    def test_un_anonymiseur_sterile_est_refuse(self):
        """Un anonymiseur qui ne nomme aucun champ n'en est pas un.

        La garde est défensive : aucun anonymiseur du socle ne peut rendre une
        liste vide aujourd'hui. Elle vaut pour les ponts à venir, dont un
        anonymiseur pourrait ne rien trouver à vider et le dire quand même. Sans
        essai, la passe de mutations la déclarait survivante, à juste titre.
        """
        piece = self.env["ir.attachment"].create({
            "name": "preuve-sterile-27007.txt",
            "datas": "Qm9uam91cg==",
        })
        classification = self._classer(piece)
        demande = self._demande(classification)
        with patch.object(type(piece), "_privacy_anonymize", lambda self: []):
            demande.with_user(self.officer)._execute_document_destruction("anonymize")

        self.assertIn("n'a nommé aucun champ", demande.execution_log or "")
        self.assertIn(classification, demande.skipped_classification_ids)
        self.assertTrue(piece.datas, "Rien ne doit avoir été touché.")
        self.assertFalse(demande.applied_effects)

    # ------------------------------------------------------------------
    # 3. La portée au registre
    # ------------------------------------------------------------------

    def test_le_registre_refuse_une_anonymisation_en_portee_entiere(self):
        contact = self._contact("Portée entière 27007")
        with self.assertRaises(UserError):
            self.Register.create(self._vals_registre(
                res_model="res.partner",
                res_id=contact.id,
                res_name=contact.name,
            ))
        # 🔴 La garde rendait la main dès qu'il n'y
        # avait pas de `res_id`, or l'entrée construite depuis une demande
        # RETIRE `res_model` et `res_id` quand rien n'a été vidé. Une entrée qui
        # nomme un MODÈLE anonymisé doit déclarer sa portée partielle, qu'elle
        # désigne un identifiant ou un ensemble.
        with self.assertRaises(UserError):
            self.Register.create(self._vals_registre(
                res_model="res.partner",
                res_name="Lot de 180 fiches",
            ))
        # ⚠ Et elle se tait quand l'entrée ne nomme AUCUN modèle : sans modèle,
        # la portée n'a pas de référent, et c'est la forme de l'entrée d'une
        # demande dont la cible avait déjà disparu, où
        # « l'enregistrement n'existe plus » est VRAI.
        self.assertTrue(self.Register.create(self._vals_registre(
            res_name="Cible déjà disparue",
        )).exists())

    def test_le_registre_refuse_une_portee_partielle_sans_champs(self):
        contact = self._contact("Portée sans champs 27007")
        with self.assertRaises(UserError):
            self.Register.create(self._vals_registre(
                res_model="res.partner",
                res_id=contact.id,
                res_name=contact.name,
                destruction_scope="partial",
            ))

    def test_le_registre_accepte_une_anonymisation_qui_nomme_ses_champs(self):
        contact = self._contact("Portée déclarée 27007")
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner",
            res_id=contact.id,
            res_name=contact.name,
            destruction_scope="partial",
            res_field="email, phone, street",
        ))
        self.assertEqual(entree.destruction_scope, "partial")
        self.assertTrue(entree.verification_hash, "L'entrée doit être scellée.")

    def test_une_campagne_inscrit_lanonymisation_en_portee_partielle(self):
        contact = self._contact("Campagne anonymise 27007")
        self._classer(contact, sujet=contact)
        campagne = self._campagne()
        campagne.with_user(self.officer).action_execute()

        ligne = campagne.line_ids.filtered(lambda l: l.res_id == contact.id)
        self.assertTrue(ligne, "La campagne doit avoir balayé la fiche classée.")
        self.assertEqual(ligne.state, "done")
        self.assertEqual(ligne.applied_scope, "partial")
        self.assertIn("email", ligne.applied_fields or "")
        entree = ligne.register_entry_id
        self.assertTrue(entree, "La ligne doit porter son entrée de registre.")
        self.assertEqual(entree.destruction_method, "anonymize")
        self.assertEqual(
            entree.destruction_scope, "partial",
            "Une anonymisation garde l'enregistrement : la portée est partielle.",
        )
        self.assertIn("email", entree.res_field or "")

    # ------------------------------------------------------------------
    # 4. Rien détruit, rien certifié
    # ------------------------------------------------------------------

    def test_une_demande_sans_effet_ne_rend_pas_de_certificat(self):
        """Le cas mesuré : un « droit à l'oubli » sur une fiche non classifiée.

        Avant le correctif : aucun champ touché, et pourtant un certificat
        intitulé « CERTIFICAT DE DESTRUCTION » plus une entrée de registre.
        """
        contact = self._contact("Fiche nue 27007")
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "partner_id": contact.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id,
        })
        demande.with_user(self.officer).action_approve()
        with self.assertRaises(UserError):
            demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.state, "approved")
        self.assertFalse(
            self.Register.search([("destruction_request_id", "=", demande.id)]),
            "Rien ne doit être inscrit au registre pour une demande sans effet.",
        )
        contact.invalidate_recordset()
        self.assertTrue(contact.active, "La fiche n'a pas été touchée, et c'est le constat.")

    def test_une_methode_manuelle_reste_permise_sans_effet(self):
        """« Manuelle » ne prétend rien faire : elle programme une revue."""
        contact = self._contact("Revue manuelle 27007")
        calendrier = self.env["privacy.retention.calendar"].create({
            "name": "Conservation 27007 (manuelle)",
            "code": "T27007M",
            "document_type": "contract",
            "legal_basis": "Essai",
            "active_retention_years": 0,
            "semi_active_retention_years": 0,
            "destruction_method": "manual",
        })
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "partner_id": contact.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": calendrier.id,
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).action_execute()
        self.assertEqual(demande.state, "executed")
        self.assertEqual(demande.destruction_method_used, "manual")

    def test_une_cible_deja_disparue_se_certifie_quand_meme(self):
        """La cible avait déjà disparu : c'est une tentative, pas un néant.

        🔴 La branche « DÉJÀ
        ABSENT » remplit les détruits et le journal, mais jamais les effets.
        Une demande dont le seul document avait été supprimé avant l'exécution
        (par un pont qui supprime vraiment avant de relayer à `super()`, ou à la
        main) n'avait donc ni effet ni saut : la garde levait, et le `rollback`
        de la levée annulait la destruction qui venait de réussir. La demande
        restait approuvée, le droit n'était jamais honoré, et le cron rejouait
        l'échec chaque jour.
        """
        piece = self._piece("preuve-disparue-27007.txt")
        classification = self._classer(piece)
        demande = self._demande(classification)
        piece.unlink()

        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.state, "executed")
        self.assertIn("DÉJÀ ABSENT", demande.execution_log or "")
        self.assertEqual(demande.destroyed_count, 1)
        self.assertEqual(demande.skipped_count, 0)
        self.assertFalse(
            demande.applied_effects,
            "Cette demande n'a rien détruit elle-même : les effets restent vides.",
        )
        self.assertTrue(
            self.Register.search([("destruction_request_id", "=", demande.id)]),
            "La demande doit être certifiée et inscrite, pas annulée par une levée.",
        )

    def test_le_certificat_ne_compte_pas_le_consentement_refuse(self):
        """Le certificat nomme ce qui a été détruit, et DIT ce qui a été refusé.

        🔴 La catégorie
        « Enregistrement de consentement » n'était conditionnée qu'à la présence
        d'un `consent_id`. Sur le chemin le plus courant (un `erasure_right` sur
        un contact qui porte un consentement ET une pièce classifiée), la pièce
        était anonymisée donc la garde passait, le consentement restait intact,
        et le certificat attestait quand même sa destruction. Avant le lot,
        cette branche l'archivait au moins : l'attestation était donc PLUS
        fausse après le correctif qu'avant.
        """
        contact = self._contact("Sujet certificat 27007")
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité certificat 27007",
            "code": "T27007CERT",
        })
        consentement = self.env["privacy.consent"].create({
            "subject_partner_id": contact.id,
            "purpose_id": finalite.id,
            "notes": "Note d'origine qui doit survivre au refus.",
        })
        classification = self._classer(
            self._piece("piece-certificat-27007.txt"), sujet=contact)
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "partner_id": contact.id,
            "consent_id": consentement.id,
            "classification_ids": [(6, 0, classification.ids)],
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id,
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.state, "executed")
        consentement.invalidate_recordset()
        self.assertTrue(consentement.active, "Le consentement est resté intact.")
        self.assertIn("origine", consentement.notes or "")
        self.assertFalse(demande.consent_applied)
        self.assertTrue(demande.consent_refused_reason)

        contenu = demande.certificate_content or ""
        self.assertIn("- Documents classifiés (1)", contenu)
        self.assertNotIn(
            "- Enregistrement de consentement", contenu,
            "Le certificat comptait parmi les données détruites un "
            "consentement que le lot vient de refuser de toucher.",
        )
        self.assertIn("NON DÉTRUIT", contenu)
        self.assertIn("aucun élément retiré", contenu)
        self.assertIn("Sujet", contenu, "Le motif du refus doit être imprimé.")

        # Et l'entrée de registre ne DÉSIGNE pas le consentement intact, tout en
        # déclarant la portée partielle de ce qui a vraiment été vidé.
        entree = self.Register.search([("destruction_request_id", "=", demande.id)])
        self.assertTrue(entree)
        self.assertFalse(
            entree.res_id,
            "L'entrée désignerait un consentement qu'elle n'a pas touché.",
        )
        self.assertEqual(entree.destruction_scope, "partial")
        self.assertIn("ir.attachment", entree.res_field or "")

    def test_le_registre_ne_scelle_pas_le_nom_efface(self):
        """Le nom d'avant ne survit pas dans la table qui refuse `write`.

        🔴 La ligne de campagne fige
        `res_name` au classement, et le socle le recopie dans l'entrée de
        registre, la seule table du module qui refuse `write` et `unlink` : sur
        une fiche devenue « Personne anonymisée », le nom d'origine y était
        scellé, et survivait ainsi à son propre effacement.
        """
        contact = self._contact("Jeanne Exemple")
        classification = self._classer(contact, sujet=contact)
        campagne = self._campagne("Campagne nom 27007")
        campagne.with_user(self.officer).action_execute()

        ligne = campagne.line_ids.filtered(lambda l: l.res_id == contact.id)
        self.assertTrue(ligne)
        self.assertEqual(ligne.state, "done")
        entree = ligne.register_entry_id
        self.assertTrue(entree, "La ligne doit porter son entrée de registre.")
        classification.invalidate_recordset()
        for texte, ou in (
            (ligne.res_name, "la ligne de campagne"),
            (entree.res_name, "l'entrée de registre"),
            (entree.document_description, "la description de l'entrée"),
            (classification.res_name, "la classification"),
        ):
            self.assertNotIn(
                "Nom-De-Personne", texte or "",
                f"Le nom effacé survit dans {ou}.",
            )
        self.assertIn("anonymis", (entree.res_name or "").lower())
        # L'archivage n'est pas un effacement : il ne se nomme pas parmi les
        # éléments détruits.
        self.assertNotIn("active", ligne.applied_fields or "")
        self.assertNotIn("active", entree.res_field or "")

    def test_une_demande_ne_scelle_pas_le_nom_efface(self):
        """Le même constat 3, sur le chemin « demande ».

        L'instantané du consentement est pris AVANT l'exécution, parce que
        « Suppression » le supprime vraiment. Son `display_name` est « <nom du
        sujet> - <finalité> » : quand la MÊME demande anonymise la fiche du
        sujet, l'instantané scellait au registre immuable le nom qu'elle venait
        d'effacer. Le nom se relit donc quand le consentement a survécu.
        """
        contact = self._contact("Gérard Lavoie 27007")
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité demande 27007",
            "code": "T27007DEM",
        })
        consentement = self.env["privacy.consent"].create({
            "subject_partner_id": contact.id,
            "purpose_id": finalite.id,
        })
        self.assertIn("Lavoie", consentement.display_name)
        classification = self._classer(contact, sujet=contact)
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "partner_id": contact.id,
            "consent_id": consentement.id,
            "classification_ids": [(6, 0, classification.ids)],
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id,
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).action_execute()

        contact.invalidate_recordset()
        self.assertIn("anonymis", (contact.name or "").lower())
        entree = self.Register.search([("destruction_request_id", "=", demande.id)])
        self.assertTrue(entree)
        for texte, ou in (
            (entree.res_name, "le nom de l'entrée"),
            (entree.document_description, "la description de l'entrée"),
        ):
            self.assertNotIn("Lavoie", texte or "", f"Le nom effacé survit dans {ou}.")

    def test_une_preuve_de_consentement_perd_son_instantane(self):
        """L'instantané JSON porte le nom du sujet, en clair.

        🔴 L'anonymiseur vidait huit champs,
        mais ni `consent_snapshot` (qui porte « subject : <nom> ») ni
        `digital_signature`. C'est l'argument qui fait REFUSER l'anonymisation
        d'un `privacy.consent`, appliqué à sa preuve avec la conclusion inverse.
        """
        contact = self._contact("Sujet de la preuve 27007")
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité preuve 27007",
            "code": "T27007PR",
        })
        consentement = self.env["privacy.consent"].create({
            "subject_partner_id": contact.id,
            "purpose_id": finalite.id,
        })
        preuve = self.env["privacy.consent.evidence"].create({
            "consent_id": consentement.id,
            "evidence_type": "portal_log",
            "note": "Note de preuve 27007",
            "ip_address": "198.51.100.7",
            "user_agent": "Mozilla/5.0 (essai 27007)",
            "consent_snapshot": json.dumps({
                "id": consentement.id,
                "status": "granted",
                "subject": contact.name,
            }),
            "digital_signature": "c2lnbmF0dXJlIGQnZXNzYWk=",
        })
        self.assertIn("Sujet de la preuve", preuve.consent_snapshot)

        classification = self._classer(preuve, sujet=contact)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()

        preuve.invalidate_recordset()
        self.assertFalse(
            preuve.consent_snapshot,
            "L'instantané nommait encore la personne après l'anonymisation.",
        )
        self.assertFalse(preuve.digital_signature)
        self.assertFalse(preuve.ip_address)
        self.assertFalse(preuve.user_agent)
        self.assertIn("ANONYMIS", (preuve.note or "").upper())
        self.assertIn("consent_snapshot", demande.document_anonymized_fields or "")
        self.assertIn("digital_signature", demande.document_anonymized_fields or "")

    def test_une_demande_anonymisante_inscrit_sa_portee_partielle(self):
        """La promesse  vaut aussi pour le chemin « demande ».

        🔴 Le chemin « demande » ne lisait que
        `applied.label` et jetait `applied.scope` et `applied.fields` ; la portée
        partielle ne s'écrivait que depuis le consentement, dont l'anonymiseur
        lève toujours. Donc aucune demande n'écrivait JAMAIS de portée
        partielle : deux pièces jointes anonymisées donnaient une entrée en
        portée « entière », dont l'aide du champ dit « affirme que
        l'enregistrement visé n'existe plus ».
        """
        classifications = self.Classification
        for i in range(2):
            classifications |= self._classer(self._piece(f"piece-portee-{i}-27007.txt"))
        demande = self._demande(classifications)
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.destroyed_count, 2)
        self.assertIn("ir.attachment", demande.document_anonymized_fields or "")
        self.assertIn("datas", demande.document_anonymized_fields or "")
        entree = self.Register.search([("destruction_request_id", "=", demande.id)])
        self.assertTrue(entree)
        self.assertEqual(
            entree.destruction_scope, "partial",
            "Les pièces survivent, vidées : la portée ne peut pas être « entière ».",
        )
        self.assertIn("datas", entree.res_field or "")

    def test_une_ligne_sans_effet_nest_pas_certifiee(self):
        """« Rien détruit, rien certifié » vaut aussi sur la campagne.

        🔴 La garde ne vivait que sur
        la demande, pas sur la campagne, qui est le chemin qui écrit le plus
        d'entrées. Un calendrier en méthode « Révision manuelle » ne touchait
        rien : la ligne passait à « fait », la classification était archivée donc
        invisible aux campagnes suivantes, et une entrée immuable était scellée.
        La primitive déclare maintenant qu'elle n'a rien retiré (`removed`), et
        la phrase « aucun champ vidé, traitement manuel », qui satisfaisait la
        garde du registre en disant que rien n'était parti, a disparu.
        """
        calendrier = self._calendrier("T27007CM", "manual")
        contact = self._contact("Revue manuelle en campagne 27007")
        classification = self._classer(contact, calendrier=calendrier, sujet=contact)
        campagne = self._campagne("Campagne manuelle 27007", calendrier=calendrier)
        campagne.with_user(self.officer).action_execute()

        ligne = campagne.line_ids.filtered(lambda l: l.res_id == contact.id)
        self.assertTrue(ligne, "La campagne doit avoir balayé la fiche classée.")
        self.assertEqual(ligne.state, "skipped")
        self.assertFalse(ligne.register_entry_id)
        self.assertFalse(
            self.Register.search([("campaign_id", "=", campagne.id)]),
            "Rien n'a été retiré : rien ne s'inscrit au registre immuable.",
        )
        classification.invalidate_recordset()
        self.assertTrue(
            classification.active,
            "La classification reste à traiter, sinon la campagne suivante ne la voit plus.",
        )
        contact.invalidate_recordset()
        self.assertTrue(contact.email, "Rien ne devait être touché.")

    def test_les_identifiants_directs_sont_vides(self):
        """NEQ, coordonnées géographiques et code-barres identifient directement.

        🔴 `_PRIVACY_ANONYMIZE_CLEARED`
        laissait `company_registry` (le NEQ au Québec), `partner_latitude`,
        `partner_longitude` (sept décimales pointent le domicile après que rue,
        ville et code postal ont été vidés) et `barcode`.
        """
        contact = self._contact("Identifiants directs 27007")
        vals = {}
        for champ, valeur in (
            ("company_registry", "1148765432"),
            ("barcode", "CB-27007"),
            ("partner_latitude", 46.8139),
            ("partner_longitude", -71.2080),
        ):
            if champ in contact._fields:
                vals[champ] = valeur
        self.assertTrue(vals, "Aucun des quatre champs n'existe sur ce locataire.")
        contact.write(vals)

        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()

        contact.invalidate_recordset()
        for champ in vals:
            self.assertFalse(
                contact[champ], f"« {champ} » identifie encore la personne.")
            self.assertIn(
                champ, demande.document_anonymized_fields or "",
                f"« {champ} » doit être nommé parmi les éléments détruits.",
            )

    def test_l_effet_applique_se_lit_comme_une_chaine(self):
        """Les quatre formes de formatage rendent l'étiquette.

        🔴 Le retour était un `NamedTuple`
        dont la docstring promettait qu'il « se lit comme une chaîne » :
        `f"{x}"` et `str(x)` rendaient bien l'étiquette, mais `"%s" % x` la
        lisait comme un n-uplet d'arguments et `", ".join([..., x])` levait une
        `TypeError`. Aucun appelant en arbre ne le faisait, mais le lot 5 et la
        copie publique partent de cette docstring. Rendu vrai en héritant de
        `str`, et épinglé ici.
        """
        ops = self._ops()
        applied = ops.Applied("anonymisation (email)", "partial", "email")
        self.assertEqual(f"{applied}", "anonymisation (email)")
        self.assertEqual(str(applied), "anonymisation (email)")
        self.assertEqual("%s" % applied, "anonymisation (email)")
        self.assertEqual(
            ", ".join(["fait", applied]), "fait, anonymisation (email)")
        self.assertEqual(applied.label, "anonymisation (email)")
        self.assertEqual(applied.scope, "partial")
        self.assertEqual(applied.fields, "email")
        self.assertTrue(applied.removed)

        # Et sur le vrai retour de la primitive, pas seulement sur un bouchon.
        piece = self._piece("piece-formatage-27007.txt")
        rendu = ops.destroy_record(piece, "anonymize")
        self.assertEqual("%s" % rendu, rendu.label)
        self.assertEqual(", ".join([rendu]), rendu.label)

    def test_une_methode_qui_ne_retire_rien_le_declare(self):
        """`archive` et `manual` ne nomment plus de champs vidés.

        🔴 Elles rendaient
        `res_field` = « aucun champ vidé, mise de côté » / « ... traitement
        manuel », une phrase qui satisfaisait la garde « toute portée partielle
        nomme ce qui est parti » en disant que rien n'était parti. La garde ne
        contrôle que la présence d'un texte : c'est donc la primitive qui doit
        dire la vérité, et l'appelant qui doit la lire.
        """
        ops = self._ops()
        contact = self._contact("Mis de côté 27007")
        archive = ops.destroy_record(contact, "archive")
        self.assertFalse(archive.removed)
        self.assertEqual(archive.fields, "")
        contact.invalidate_recordset()
        self.assertFalse(contact.active)
        self.assertTrue(contact.email, "L'archivage ne retire rien.")

        manuel = ops.destroy_record(contact, "manual")
        self.assertFalse(manuel.removed)
        self.assertEqual(manuel.fields, "")

        # Et une telle portée ne peut pas s'inscrire au registre.
        with self.assertRaises(UserError):
            self.Register.create(self._vals_registre(
                res_model="res.partner",
                res_id=contact.id,
                res_name=contact.name,
                destruction_method="archive",
                destruction_scope="partial",
                res_field=archive.fields,
            ))

    # ------------------------------------------------------------------
    # 5. Rien retiré, rien au registre (arbitrage du 2026-11-15)
    # ------------------------------------------------------------------

    def test_un_archivage_ninscrit_rien_au_registre(self):
        """« Archivage » met de côté : il n'atteste rien, donc il n'écrit rien.

        🔴 Règle retenue du 2026-11-15, et constat La branche `archive` du volet consentement était le
        TROISIÈME écrivain du registre, et le seul qui ne lisait pas `removed` :
        elle journalisait avec `effet=True`, donc `applied_effects` se
        remplissait pour une opération dont la primitive dit qu'elle n'a rien
        retiré. La garde de certification passait, le certificat se scellait, et
        une entrée immuable s'écrivait. Elle partait même avec le défaut du
        champ de portée, `full`, l'entrée ne désignant plus le consentement.
        """
        contact = self._contact("Sujet archivé 27007")
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité archivage 27007",
            "code": "T27007AR",
        })
        consentement = self.env["privacy.consent"].create({
            "subject_partner_id": contact.id,
            "purpose_id": finalite.id,
            "notes": "Note qui survit à une mise de côté.",
        })
        calendrier = self._calendrier("T27007CA", "delete")
        # ⚠ `archive` n'est pas dans la sélection du calendrier : la méthode
        # vient de la politique de conservation, qui l'offre.
        politique = self.env["privacy.retention.policy"].create({
            "purpose_id": finalite.id,
            "retention_days": 0,
            "destruction_method": "archive",
        })
        self.assertTrue(calendrier)
        demande = self.Destruction.create({
            "request_type": "consent",
            "consent_id": consentement.id,
            "partner_id": contact.id,
            "policy_id": politique.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.state, "executed")
        self.assertFalse(
            demande.applied_effects,
            "L'archivage ne retire rien : aucun effet réel ne se consigne.",
        )
        self.assertFalse(
            self.Register.search([("destruction_request_id", "=", demande.id)]),
            "Rien n'a été retiré : le registre immuable ne reçoit rien.",
        )
        self.assertIn("REGISTRE : aucune entrée", demande.execution_log or "")
        consentement.invalidate_recordset()
        self.assertFalse(consentement.active, "La mise de côté a bien eu lieu.")
        self.assertIn("survit", consentement.notes or "")
        self.assertTrue(
            consentement.subject_partner_id,
            "Et la personne reste désignée, ce que le registre n'atteste plus.",
        )

    def test_une_demande_dont_tous_les_documents_sont_refuses_ninscrit_rien(self):
        """🔴 Le cas le plus grave de N1 : le § 1.4 sur un chemin que le lot a créé.

        Une demande en méthode « Anonymisation » dont TOUS les documents ont été
        refusés (fiche porteuse d'une pièce comptable ou d'un compte usager,
        donc les deux refus que ce lot ajoute) a `skipped_count` à 1. Elle
        passait la garde de certification PAR cette condition, n'avait touché à
        rien, et scellait une entrée en portée « entière », dont l'aide du champ
        dit « affirme que l'enregistrement visé n'existe plus ».

        Aucun essai ne lisait la portée d'une entrée née de ce chemin.
        """
        contact = self._contact("Refus total 27007")
        self.env["res.users"].create({
            "partner_id": contact.id,
            "login": "compte_refus_total_27007",
        })
        classification = self._classer(contact, sujet=contact)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()

        # L'exécution est permise : le refus est consigné, pas avalé.
        self.assertEqual(demande.state, "executed")
        self.assertEqual(demande.skipped_count, 1)
        self.assertEqual(demande.destroyed_count, 0)
        self.assertFalse(demande.applied_effects)
        self.assertIn(classification, demande.skipped_classification_ids)
        # Et RIEN n'est scellé.
        self.assertFalse(
            self.Register.search([("destruction_request_id", "=", demande.id)]),
            "Aucun document n'a été touché : l'entrée de registre serait fausse, "
            "et le registre ne se corrige pas.",
        )
        self.assertIn("REGISTRE : aucune entrée", demande.execution_log or "")
        contact.invalidate_recordset()
        self.assertEqual(contact.name, "Refus total 27007")

    def test_une_destruction_partielle_sinscrit_quand_meme(self):
        """Le bébé et l'eau du bain : une destruction PARTIELLE s'atteste.

        ⚠ Le pendant obligatoire des deux essais ci-dessus. Une demande dont un
        document a été détruit et un autre refusé a bien détruit quelque chose :
        son entrée s'écrit, et le certificat nomme l'échec dans sa section
        « Documents NON détruits », comme la garde du socle l'a posé.
        """
        piece = self._piece("piece-partiellement-detruite-27007.txt")
        refuse = self._contact("Refus dans un lot 27007")
        self.env["res.users"].create({
            "partner_id": refuse.id,
            "login": "compte_lot_partiel_27007",
        })
        classifications = self._classer(piece) | self._classer(refuse, sujet=refuse)
        demande = self._demande(classifications)
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.destroyed_count, 1)
        self.assertEqual(demande.skipped_count, 1)
        entree = self.Register.search([("destruction_request_id", "=", demande.id)])
        self.assertTrue(
            entree, "Une destruction partielle est une destruction : elle s'inscrit.")
        self.assertEqual(entree.destruction_scope, "partial")
        self.assertIn("datas", entree.res_field or "")

    def test_une_methode_manuelle_ninscrit_rien_au_registre(self):
        """« Révision manuelle » ne prétend rien faire : elle n'atteste rien."""
        contact = self._contact("Revue manuelle sans registre 27007")
        calendrier = self._calendrier("T27007MR", "manual")
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "partner_id": contact.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": calendrier.id,
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.state, "executed")
        self.assertFalse(
            self.Register.search([("destruction_request_id", "=", demande.id)]),
            "Une revue humaine programmée n'est pas une destruction.",
        )

    def test_un_compteur_pose_par_rpc_ne_fait_pas_certifier(self):
        """La garde lit ce que CETTE exécution a écrit, pas un champ stocké d'avant.

        🔴 La garde de certification lit
        trois compteurs STOCKÉS, dont le `readonly` ne vaut qu'à l'écran : un
        `destroyed_count` posé par RPC avant l'exécution faisait certifier une
        demande qui n'a rien touché. Durcissement, pas une brèche connue
        (exécuter demande l'état « approuvée » et le groupe du responsable),
        mais c'est l'invariant porteur du lot.
        """
        contact = self._contact("Compteur menti 27007")
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "partner_id": contact.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id,
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).write({
            "destroyed_count": 5,
            "skipped_count": 3,
            "applied_effects": "DÉTRUIT (anonymisation) : rien, en réalité",
        })
        with self.assertRaises(UserError):
            demande.with_user(self.officer).action_execute()
        self.assertEqual(demande.state, "approved")
        self.assertFalse(
            self.Register.search([("destruction_request_id", "=", demande.id)]))

    # ------------------------------------------------------------------
    # 6. les formes relevées en second
    # ------------------------------------------------------------------

    def test_une_piece_jointe_perd_le_nom_de_son_fichier(self):
        """🔴 N2 : le nom du fichier EST un renseignement personnel.

        L'anonymiseur ne touchait pas `name`, et `_rec_name` vaut `name` :
        `refresh_res_name` relisait donc une valeur INCHANGÉE, la ligne de
        campagne gardait son `res_name` d'origine, et l'entrée immuable portait
        « Document : consentement-nom-de-personne.pdf » avec « Éléments détruits :
        datas, description ». C3 rouvert sur le seul modèle que le module ait
        toujours prétendu effacer pour de vrai.
        """
        piece = self._piece("consentement-nom-de-personne.pdf")
        classification = self._classer(piece)
        campagne = self._campagne("Campagne nom de fichier 27007")
        campagne.with_user(self.officer).action_execute()

        piece.invalidate_recordset()
        self.assertNotIn("nom-de-personne", (piece.name or "").lower(),
                         "Le nom du fichier nommait la personne.")
        self.assertFalse(piece.datas)

        ligne = campagne.line_ids.filtered(lambda l: l.res_id == piece.id)
        self.assertEqual(ligne.state, "done")
        self.assertIn("name", ligne.applied_fields or "")
        self.assertNotIn("nom-de-personne", (ligne.res_name or "").lower())
        classification.invalidate_recordset()
        self.assertNotIn("nom-de-personne", (classification.res_name or "").lower())

        entree = ligne.register_entry_id
        self.assertTrue(entree)
        self.assertNotIn("nom-de-personne", (entree.res_name or "").lower(),
                         "Le registre est immuable : le nom y survivrait sans recours.")
        self.assertNotIn("nom-de-personne", (entree.document_description or "").lower())
        self.assertIn("name", entree.res_field or "")

    def test_la_bascule_de_la_regle_livree_ne_touche_que_la_valeur_livree(self):
        """🔴 N3 et arbitrage 2 : CST-001 passe à « Supprimer », les choix restent.

        La règle livrée « Enregistrements de consentement » (CST-001) portait
        `destruction_method = anonymize`, et `privacy.consent` est classifiable.
        Depuis le lot, son anonymiseur lève TOUJOURS : chaque ligne de campagne
        tombait en « Échec », la classification restait active, `action_scan` ne
        déduplique que sur « en attente » et « fait », donc la campagne suivante
        recréait la ligne et rééchouait. L'obligation de l'art. 23 n'était
        jamais honorée, mois après mois.

        ⚠ Ce que cet essai éprouve surtout, c'est la CONDITION : un locataire
        qui a choisi sa méthode lui-même n'est pas touché par la migration.
        """
        Calendrier = self.env["privacy.retention.calendar"]
        livree = self._calendrier("T27007L1", "anonymize")
        livree.final_disposition = "anonymize"
        choisie = self._calendrier("T27007L2", "manual")
        choisie.final_disposition = "anonymize"

        touchees = (livree | choisie)._privacy_switch_delivered_consent_rule()

        self.assertEqual(touchees, livree | choisie,
                         "Les deux portent le sort final livré, donc les deux bougent.")
        self.assertEqual(livree.destruction_method, "delete")
        self.assertEqual(livree.final_disposition, "destroy")
        self.assertEqual(
            choisie.destruction_method, "manual",
            "Une méthode choisie par le locataire n'est pas réécrite par une montée.",
        )
        self.assertEqual(choisie.final_disposition, "destroy")

        # Et rejouée, elle ne trouve plus rien à faire.
        self.assertFalse((livree | choisie)._privacy_switch_delivered_consent_rule())
        # ⚠ Une règle supprimée ne fait pas LEVER : une migration qui lève ne se
        # rejoue pas au prochain `-u`, et le module passerait en 18.0.6.0.0 sans
        # elle, en silence.
        disparue = self.env["privacy.retention.calendar"].browse(2147483000)
        self.assertFalse(disparue._privacy_switch_delivered_consent_rule())

        # La vraie CST-001, par le chemin de la migration (sans enregistrement).
        cst = Calendrier.browse()
        for module in MODULES:
            cst = self.env.ref(f"{module}.retention_calendar_consent_records",
                               raise_if_not_found=False) or cst
        self.assertTrue(cst, "La règle livrée CST-001 doit exister.")
        cst.write({"destruction_method": "anonymize", "final_disposition": "anonymize"})
        self.assertEqual(Calendrier._privacy_switch_delivered_consent_rule(), cst)
        self.assertEqual(cst.destruction_method, "delete")
        self.assertEqual(cst.final_disposition, "destroy")

    def test_une_campagne_qui_ignore_leve_une_activite_de_revue(self):
        """🔴 N4 : la boucle se ferme par une décision humaine, pas en silence.

        Une ligne « Ignoré » laisse sa classification ACTIVE, et c'est voulu :
        l'archiver la rendrait invisible aux campagnes suivantes. Sans geste, la
        campagne du mois prochain la rebalaye, la réignore, et personne n'est
        jamais saisi : des centaines de lignes ignorées par mois sur une base réelle,
        dont le défaut réel est la règle de conservation.

        ⚠ Pas d'état terminal dédupliqué à `action_scan` : il ferait
        disparaître la classification de TOUTES les campagnes à venir, et
        l'obligation ne serait plus jamais honorée, en silence. Le second
        balayage ci-dessous épingle ce choix : l'écart se redit.
        """
        calendrier = self._calendrier("T27007N4", "manual")
        contact = self._contact("Ignoré en campagne 27007")
        classification = self._classer(contact, calendrier=calendrier, sujet=contact)
        campagne = self._campagne("Campagne ignorée 27007", calendrier=calendrier)
        campagne.with_user(self.officer).action_execute()

        ligne = campagne.line_ids.filtered(lambda l: l.res_id == contact.id)
        self.assertEqual(ligne.state, "skipped")
        activites = self.env["mail.activity"].search([
            ("res_model", "=", "privacy.destruction.campaign"),
            ("res_id", "=", campagne.id),
        ])
        self.assertTrue(activites, "Une revue humaine doit être programmée.")
        texte = " ".join(activites.mapped("summary")) + " ".join(activites.mapped("note"))
        self.assertIn("ignoré", texte.lower())
        self.assertIn(calendrier.name, texte)

        # Et l'écart se redit tant que la règle n'a pas changé.
        classification.invalidate_recordset()
        self.assertTrue(classification.active)
        seconde = self._campagne("Campagne ignorée 27007 (bis)", calendrier=calendrier)
        self.assertTrue(
            seconde.line_ids.filtered(lambda l: l.res_id == contact.id),
            "La classification reste à traiter : le balayage suivant la revoit.",
        )

    def test_le_registre_ne_nomme_que_les_champs_qui_portaient_une_valeur(self):
        """🔴 N5 : dix-neuf éléments attestés pour deux réels.

        L'anonymiseur écrivait `False` sur les 17 champs présents et rendait les
        clés de `vals` : une fiche qui ne portait qu'un nom faisait attester
        dix-neuf éléments détruits. C'est le surclaim que toute cette tâche
        combat, et le lot avait déjà retiré `active` de la liste pour exactement
        ce motif.
        """
        nue = self.env["res.partner"].create({"name": "Fiche sans rien 27007"})
        classification = self._classer(nue, sujet=nue)
        demande = self._demande(classification)
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.destroyed_count, 1)
        nommes = demande.document_anonymized_fields or ""
        self.assertIn("name", nommes, "Le nom portait une valeur, et il est remplacé.")
        for absent in ("zip", "email", "phone", "comment", "barcode", "city"):
            self.assertNotIn(
                absent, nommes,
                f"« {absent} » ne contenait rien : l'attester est un surclaim.",
            )
        entree = self.Register.search([("destruction_request_id", "=", demande.id)])
        self.assertTrue(entree)
        self.assertIn("name", entree.res_field or "")
        self.assertNotIn("zip", entree.res_field or "")

    def test_le_pdf_imprime_le_refus_et_les_categories(self):
        """🔴 N6 : le correctif C2 doit atteindre le document LIVRÉ.

        `certificate_content` n'est affiché par aucune vue et imprimé par aucun
        rapport : la section « NON DÉTRUIT » ne vivait que dans la chaîne
        hachée, et le PDF imprimait « Référence du consentement » puis
        « Méthode : Anonymisation » sans bloc de refus.
        """
        contact = self._contact("Sujet du PDF 27007")
        finalite = self.env["privacy.purpose"].create({
            "name": "Finalité PDF 27007",
            "code": "T27007P",
        })
        consentement = self.env["privacy.consent"].create({
            "subject_partner_id": contact.id,
            "purpose_id": finalite.id,
        })
        piece = self._piece("piece-du-pdf-27007.txt")
        classification = self._classer(piece)
        demande = self.Destruction.create({
            "request_type": "erasure_right",
            "consent_id": consentement.id,
            "partner_id": contact.id,
            "classification_ids": [(6, 0, classification.ids)],
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
            "retention_calendar_id": self.calendrier_anonymise.id,
        })
        demande.with_user(self.officer).action_approve()
        demande.with_user(self.officer).action_execute()

        self.assertFalse(demande.consent_applied, "Le consentement a refusé.")
        self.assertEqual(demande.destroyed_count, 1, "La pièce, elle, est partie.")

        html = self.env["ir.actions.report"]._render_qweb_html(
            "privacy_consent.action_report_destruction_certificate", demande.ids)[0]
        if isinstance(html, bytes):
            html = html.decode("utf-8")
        self.assertIn("NON DÉTRUIT", html,
                      "Le PDF doit nommer le consentement resté intact.")
        self.assertIn("Catégories de données détruites", html)
        self.assertIn("Documents classifiés (1)", html)
        # ⚠ La chaîne se cherche DANS la liste des catégories : le bloc « NON
        # DÉTRUIT » porte la même étiquette, et un `assertNotIn` sur le
        # document entier passerait pour un succès en mesurant l'inverse.
        self.assertNotIn(
            "<li>Enregistrement de consentement", html,
            "Un consentement refusé ne se compte pas parmi les données détruites.",
        )
        self.assertIn("<li>Documents classifiés (1)", html)

    def test_un_nom_relu_vide_devient_un_substitut_non_identifiant(self):
        """🔴 N7 : un nom VIDÉ ne doit pas laisser l'ancien partir au registre.

        `refresh_res_name` rendait la main quand le nom relu était vide, donc
        l'ancien nom restait sur la ligne et partait au registre immuable. C'est
        exactement ce que fera un anonymiseur qui VIDE son champ de nom au lieu
        de le remplacer : le lot 4 prévoit de vider `name` sur
        `bf.outreach.target`. On écrit le substitut que `bf_outreach_privacy`
        écrit déjà à la main.
        """
        ops = self._ops()
        piece = self._piece("nom-a-vider-27007.txt")
        classification = self._classer(piece)
        self.assertIn("nom-a-vider", classification.res_name or "")

        def _nom_vide(self):
            for enregistrement in self:
                enregistrement.display_name = ""

        with patch.object(type(piece), "_compute_display_name", _nom_vide):
            piece.invalidate_recordset(["display_name"])
            rendu = ops.refresh_res_name(piece, classification)

        self.assertEqual(rendu, "ir.attachment #%s" % piece.id)
        self.assertEqual(classification.res_name, rendu)
        self.assertNotIn("nom-a-vider", classification.res_name or "")

    def test_une_ligne_ignoree_garde_ce_que_la_primitive_a_repondu(self):
        """Les trois colonnes `applied_*` survivent au point de sauvegarde.

        🔴 Elles étaient écrites DANS le
        point de sauvegarde, donc annulées avec lui pour toute ligne qui finit
        « Ignoré » ou « Échec » : elles restaient vides précisément sur les
        lignes qu'on veut comprendre, et seul `error_message` survivait.
        """
        calendrier = self._calendrier("T27007SP", "manual")
        contact = self._contact("Ligne ignorée lisible 27007")
        self._classer(contact, calendrier=calendrier, sujet=contact)
        campagne = self._campagne("Campagne savepoint 27007", calendrier=calendrier)
        campagne.with_user(self.officer).action_execute()

        ligne = campagne.line_ids.filtered(lambda l: l.res_id == contact.id)
        self.assertEqual(ligne.state, "skipped")
        self.assertTrue(
            ligne.applied_label,
            "Ce que la primitive a répondu doit rester lisible sur la ligne.",
        )
        self.assertIn("manuel", (ligne.applied_label or "").lower())
        self.assertEqual(ligne.applied_scope, "partial")
        self.assertTrue(ligne.error_message)

    def test_l_effet_applique_survit_a_la_copie(self):
        """`copy`, `deepcopy` et `pickle` gardent les attributs ; le reste non.

        🔴 Le protocole hérité de `str`
        ne reconstruisait que les caractères : les trois opérations levaient ou
        rendaient un objet amputé, et le premier accès à `scope` aurait levé un
        `AttributeError` loin de la cause. `__reduce__` les rend correctes.

        ⚠ L'essai épingle AUSSI les limites qui restent, parce que la docstring
        les promet : toute opération de chaîne rend un `str` NU.
        """
        ops = self._ops()
        applied = ops.Applied("anonymisation (email)", "partial", "email")

        for refait in (copy.copy(applied), copy.deepcopy(applied),
                       pickle.loads(pickle.dumps(applied))):
            self.assertEqual(refait, "anonymisation (email)")
            self.assertEqual(refait.label, "anonymisation (email)")
            self.assertEqual(refait.scope, "partial")
            self.assertEqual(refait.fields, "email")
            self.assertTrue(refait.removed)

        # Et les limites que la docstring annonce.
        self.assertFalse(hasattr(applied.strip(), "scope"))
        self.assertFalse(hasattr(applied + " ", "scope"))
        self.assertFalse(bool(ops.Applied("", "partial", "x")),
                         "Une étiquette vide est fausse : on teste `removed`.")
