"""« Suppression » doit supprimer, et le registre ne doit certifier que le vrai.

Le module portait le même bloc à trois endroits ::

    elif method == "delete":
        if hasattr(record, "active"):
            record.sudo().write({"active": False})
        else:
            record.sudo().unlink()

Tout modèle qui porte un champ ``active`` — la majorité — était donc ARCHIVÉ.
Le contenu restait en base, consultable en cochant « Archivé », pendant que
``action_execute`` inscrivait au registre IMMUABLE une entrée disant
« Suppression ». Le registre refuse ``write`` et ``unlink`` : une seule campagne
laissait une certification fausse et définitive.

Quatre volets, tous couverts ici :

1. la destruction elle-même — les trois copies du bloc ;
2. **la certification d'une ligne non détruite** : ``_execute_destruction`` ne
   lève pas toujours, il écrit parfois « échec » ou « ignoré » dans l'état de la
   ligne et rend la main ; la boucle enchaînait alors sur l'entrée de registre
   et RÉÉCRIVAIT l'état à « fait » ;
3. la **portée partielle**, sans laquelle la garde promue au socle prendrait
   pour des faux positifs les trois ponts qui détruisent une partie d'un
   enregistrement en le laissant debout ;
4. la **compatibilité des empreintes** : ajouter deux champs à un registre déjà
   scellé ne doit invalider aucune entrée existante.

⚠ Ce fichier est écrit pour rester identique entre ``cq_consent`` et
``privacy_consent`` : les groupes sont résolus sans préfixe de module en dur.
"""
import hashlib
import json
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

MODULES = ("cq_consent", "privacy_consent")


@tagged("privacy_consent", "privacy_destruction_truth", "t24897")
class TestDestructionReallyDestroys(TransactionCase):

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
        cls.Campaign = cls.env["privacy.destruction.campaign"]
        cls.Classification = cls.env["privacy.document.classification"]
        cls.Register = cls.env["privacy.destruction.register"]
        cls.Destruction = cls.env["privacy.destruction.request"]

        # ⚠ `base.group_partner_manager` est indispensable : l'exécuteur contrôle
        # les droits d'écriture sur la cible avant d'escalader en sudo. Sans lui
        # la ligne échoue — et c'est le bon comportement, pas un défaut du test.
        cls.officer = cls.env["res.users"].create({
            "name": "Responsable 24897",
            "login": "test_officer_24897",
            "email": "officer-24897@example.invalid",
            "groups_id": [
                (4, cls._groupe("group_privacy_officer").id),
                (4, cls.env.ref("base.group_partner_manager").id),
            ],
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Gestionnaire 24897",
            "login": "test_manager_24897",
            "email": "manager-24897@example.invalid",
            "groups_id": [
                (4, cls._groupe("group_privacy_manager").id),
                (4, cls.env.ref("base.group_partner_manager").id),
            ],
        })
        cls.calendar = cls.env["privacy.retention.calendar"].create({
            "name": "Conservation 24897",
            "code": "T24897",
            "document_type": "contract",
            "legal_basis": "Essai",
            "active_retention_years": 0,
            "semi_active_retention_years": 0,
            "destruction_method": "delete",
        })

    # ------------------------------------------------------------------
    # Outillage
    # ------------------------------------------------------------------

    def _cible(self, nom="Cible 24897"):
        """Une fiche contact NEUVE — donc porteuse d'un champ `active`, et
        détachée de toute écriture comptable qui bloquerait son unlink."""
        partner = self.env["res.partner"].create({"name": nom})
        self.assertIn(
            "active", partner._fields,
            "Prérequis du test : la cible doit porter un champ « actif », "
            "c'est exactement ce qui déclenchait l'archivage.",
        )
        return partner

    def _classer(self, record):
        return self.Classification.create({
            "res_model": record._name,
            "res_id": record.id,
            "pi_category": "identification",
            "retention_calendar_id": self.calendar.id,
        })

    def _campagne_executee(self, nom="Campagne 24897"):
        campaign = self.Campaign.create({
            "name": nom,
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campaign.action_scan()
        campaign.with_user(self.officer).action_approve()
        campaign.with_user(self.officer).action_execute()
        return campaign

    def _vals_registre(self, **extra):
        vals = {
            "destruction_date": fields.Datetime.now(),
            "destroyed_by_id": self.env.user.id,
            "approved_by_id": self.env.user.id,
            "document_description": "Entrée d'essai 24897.",
            "destruction_method": "delete",
            "legal_basis": "Art. 23 LPRPSP (essai)",
            "company_id": self.env.company.id,
        }
        vals.update(extra)
        return vals

    # ------------------------------------------------------------------
    # 1. La destruction elle-même
    # ------------------------------------------------------------------

    def test_campaign_delete_actually_unlinks(self):
        """RÉGRESSION — une campagne « Suppression » doit SUPPRIMER.

        ⚠ Avant correctif, la fiche était seulement archivée : `exists()`
        rendait vrai et `active` valait False. Le test doit donc contrôler
        l'ABSENCE de la ligne, pas son état — un contrôle sur `active` aurait
        passé au vert sur le code fautif.
        """
        cible = self._cible()
        self._classer(cible)
        cible_id = cible.id

        campaign = self._campagne_executee()

        self.assertEqual(campaign.state, "completed")
        self.assertEqual(campaign.failed_count, 0, campaign.line_ids.mapped("error_message"))
        survivant = self.env["res.partner"].with_context(
            active_test=False
        ).browse(cible_id).exists()
        self.assertFalse(
            survivant,
            "La fiche existe toujours après une campagne « Suppression ». "
            "C'est le défaut corrigé en 18.0.5.0.0 : archivée, pas supprimée.",
        )

    def test_campaign_delete_writes_a_truthful_entry(self):
        """L'entrée écrite par la campagne affirme une portée ENTIÈRE, et elle
        dit vrai — la cible n'est plus là."""
        cible = self._cible()
        self._classer(cible)
        cible_id = cible.id
        campaign = self._campagne_executee()

        entree = self.Register.search([("campaign_id", "=", campaign.id)])
        self.assertEqual(len(entree), 1)
        self.assertEqual(entree.destruction_method, "delete")
        self.assertEqual(entree.destruction_scope, "full")
        self.assertFalse(entree.res_field)
        self.assertEqual(entree.res_id, cible_id)
        self.assertFalse(
            self.env["res.partner"].with_context(active_test=False).browse(cible_id).exists()
        )

    def test_document_request_delete_actually_unlinks(self):
        """Seconde copie du défaut : la voie « demande de destruction »."""
        cible = self._cible("Cible demande 24897")
        classification = self._classer(cible)
        cible_id = cible.id

        demande = self.Destruction.create({
            "request_type": "document",
            "classification_ids": [(6, 0, classification.ids)],
            "retention_calendar_id": self.calendar.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        })
        demande.with_user(self.manager).action_approve()
        demande.with_user(self.officer).action_execute()

        self.assertEqual(demande.state, "executed")
        self.assertIn(classification, demande.destroyed_classification_ids)
        self.assertFalse(
            self.env["res.partner"].with_context(active_test=False).browse(cible_id).exists(),
            "La demande de destruction archivait au lieu de supprimer.",
        )

    def test_consent_delete_unlinks_and_the_request_survives(self):
        """Troisième copie du défaut, sur l'objet le plus sensible.

        🔴 Et le piège qu'elle réveille : `privacy.destruction.request.consent_id`
        était `ondelete="cascade"`. Tant que « Suppression » archivait, la
        cascade ne se déclenchait jamais. Une fois la suppression réelle, elle
        aurait emporté la demande elle-même au milieu de `action_execute` —
        AVANT le certificat et l'entrée de registre. La destruction aurait
        effacé sa propre trace.
        """
        partner = self._cible("Sujet consentement 24897")
        purpose = self.env["privacy.purpose"].search([], limit=1)
        self.assertTrue(purpose, "Prérequis : au moins une finalité en base.")
        consent = self.env["privacy.consent"].create({
            "subject_partner_id": partner.id,
            "purpose_id": purpose.id,
        })
        consent_id = consent.id

        demande = self.Destruction.create({
            "request_type": "consent",
            "consent_id": consent_id,
            "partner_id": partner.id,
            "retention_calendar_id": self.calendar.id,
            "trigger_date": fields.Datetime.now(),
            "scheduled_date": fields.Date.today(),
        })
        demande_id = demande.id
        demande.with_user(self.manager).action_approve()
        demande.with_user(self.officer).action_execute()

        self.assertFalse(
            self.env["privacy.consent"].with_context(
                active_test=False
            ).browse(consent_id).exists(),
            "Le consentement a été archivé, pas supprimé.",
        )
        self.assertTrue(
            self.Destruction.browse(demande_id).exists(),
            "La demande de destruction a été emportée par la cascade : elle a "
            "effacé la trace de sa propre exécution.",
        )
        entree = self.Register.search([("destruction_request_id", "=", demande_id)])
        self.assertEqual(len(entree), 1)
        self.assertEqual(
            entree.res_id, consent_id,
            "L'entrée doit nommer le consentement détruit — l'instantané pris "
            "AVANT l'exécution est ce qui le permet.",
        )
        self.assertTrue(entree.res_name)

    # ------------------------------------------------------------------
    # 2. Ne jamais certifier ce qui n'a pas été détruit
    # ------------------------------------------------------------------

    def test_skipped_line_is_not_certified(self):
        """RÉGRESSION — une ligne que l'exécuteur marque « ignoré » ne doit
        produire AUCUNE entrée de registre.

        ⚠ `_execute_destruction` écrit l'état et rend la main sans lever. La
        boucle enchaînait sur `Register.create()` puis réécrivait « fait » :
        une ligne sautée ressortait certifiée détruite.

        Le chemin « ignoré » se fabrique en pointant une ligne vers un
        enregistrement déjà parti — ce que `action_scan` ne produit jamais,
        d'où la ligne posée à la main.
        """
        cible = self._cible("Déjà partie 24897")
        cible_id = cible.id
        cible.unlink()

        campaign = self.Campaign.create({
            "name": "Campagne ligne ignorée",
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        ligne = self.env["privacy.destruction.campaign.line"].create({
            "campaign_id": campaign.id,
            "res_model": "res.partner",
            "res_id": cible_id,
            "res_name": "Déjà partie 24897",
            "destruction_method": "delete",
            "retention_calendar_id": self.calendar.id,
        })
        campaign.write({"state": "review"})

        avant = self.Register.search_count([])
        campaign.with_user(self.officer).action_approve()
        campaign.with_user(self.officer).action_execute()

        self.assertEqual(
            self.Register.search_count([]), avant,
            "Une ligne ignorée a produit une entrée de registre.",
        )
        self.assertEqual(ligne.state, "skipped")
        self.assertFalse(ligne.register_entry_id)

    def test_failure_rolls_back_the_destruction(self):
        """Ou bien détruit ET certifié, ou bien ni l'un ni l'autre.

        On force l'échec au moment de la certification — une entrée sans base
        légale viole une contrainte NOT NULL — et on vérifie que la cible a été
        REMISE : sans point de sauvegarde, elle serait détruite sans entrée.
        """
        cible = self._cible("Cible transaction 24897")
        self._classer(cible)
        cible_id = cible.id

        campaign = self.Campaign.create({
            "name": "Campagne certification en échec",
            "retention_calendar_id": self.calendar.id,
            "cutoff_date": fields.Date.today() + timedelta(days=1),
        })
        campaign.action_scan()
        campaign.with_user(self.officer).action_approve()

        Register = self.env["privacy.destruction.register"]
        original = type(Register).create

        def create_qui_echoue(self, vals_list):
            raise UserError("Échec simulé de la certification.")

        self.patch(type(Register), "create", create_qui_echoue)
        campaign.with_user(self.officer).action_execute()

        self.assertTrue(
            self.env["res.partner"].with_context(
                active_test=False
            ).browse(cible_id).exists(),
            "La cible a été détruite alors que sa certification a échoué : "
            "le point de sauvegarde n'a pas joué.",
        )
        self.assertEqual(campaign.line_ids.mapped("state"), ["failed"])
        self.assertIn("Échec simulé", campaign.line_ids.error_message or "")
        del original

    # ------------------------------------------------------------------
    # 3. La portée
    # ------------------------------------------------------------------

    def test_register_refuses_to_certify_a_survivor(self):
        """La garde du socle : une entrée qui affirme la disparition d'un
        enregistrement encore en base est refusée."""
        survivant = self._cible("Survivant 24897")
        with self.assertRaises(UserError):
            self.Register.create(self._vals_registre(
                res_model="res.partner",
                res_id=survivant.id,
                res_name=survivant.name,
                destruction_method="delete",
            ))

    def test_register_refuses_even_when_only_archived(self):
        """⚠ Le cas EXACT du défaut : la cible est archivée, donc invisible
        d'une recherche ordinaire. La garde lit avec `active_test=False`,
        sinon elle passerait à côté de ce qu'elle doit attraper."""
        archive = self._cible("Archivé 24897")
        archive.write({"active": False})
        with self.assertRaises(UserError):
            self.Register.create(self._vals_registre(
                res_model="res.partner",
                res_id=archive.id,
                res_name=archive.name,
                destruction_method="secure_wipe",
            ))

    def test_register_accepts_a_declared_partial_destruction(self):
        """Une destruction partielle qui se déclare passe — c'est ce qui permet
        aux trois ponts (transferts, sensibilisation, rencontres) de consigner
        ce qu'ils détruisent réellement."""
        survivant = self._cible("Partiellement détruit 24897")
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner",
            res_id=survivant.id,
            res_name=survivant.name,
            destruction_method="delete",
            destruction_scope="partial",
            res_field="fichiers déposés",
        ))
        self.assertTrue(entree.exists())
        self.assertEqual(entree.destruction_scope, "partial")
        self.assertEqual(entree.res_field, "fichiers déposés")
        self.assertTrue(survivant.exists())

    def test_register_stays_silent_on_anonymisation(self):
        """« Anonymiser » n'affirme pas la disparition : la garde ne dit rien."""
        survivant = self._cible("Anonymisé 24897")
        survivant.write({"active": False})
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner",
            res_id=survivant.id,
            res_name=survivant.name,
            destruction_method="anonymize",
        ))
        self.assertTrue(entree.exists())

    def test_register_stays_silent_on_batch_entries(self):
        """Sans `res_id`, il n'y a pas d'enregistrement précis à contredire."""
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner",
            res_name="Lot de 12 déclarations",
            destruction_method="delete",
        ))
        self.assertTrue(entree.exists())

    def test_register_stays_silent_on_an_unknown_model(self):
        """Modèle absent du registre ORM (module désinstallé) : on ne peut rien
        affirmer, donc on n'affirme rien."""
        entree = self.Register.create(self._vals_registre(
            res_model="x.modele.absent",
            res_id=999999,
            res_name="Inconnu",
            destruction_method="delete",
        ))
        self.assertTrue(entree.exists())

    # ------------------------------------------------------------------
    # 4. Les empreintes
    # ------------------------------------------------------------------

    def _charge_heritee(self, entree, previous_hash=""):
        """La charge à hacher TELLE QU'ELLE ÉTAIT avant la 18.0.5.0.0.

        Recopiée à la main, exprès : si un jour quelqu'un ajoute une clé
        inconditionnelle à `_compute_verification_hash`, ce test tombe, et il
        tombe AVANT que la mise à jour ne casse le sceau d'un registre en
        production.
        """
        return json.dumps({
            "id": entree.id,
            "register_number": entree.register_number,
            "destruction_date": str(entree.destruction_date),
            "destroyed_by_id": entree.destroyed_by_id.id,
            "approved_by_id": entree.approved_by_id.id,
            "res_model": entree.res_model or "",
            "res_id": entree.res_id or 0,
            "res_name": entree.res_name or "",
            "document_description": entree.document_description or "",
            "pi_categories": entree.pi_categories or "",
            "subject_count": entree.subject_count,
            "destruction_method": entree.destruction_method,
            "legal_basis": entree.legal_basis or "",
            "certificate_number": entree.certificate_number or "",
            "previous_hash": previous_hash,
        }, sort_keys=True, ensure_ascii=False)

    def test_full_scope_entries_hash_exactly_as_before(self):
        """🔴 Le contrôle qui protège tous les registres déjà scellés.

        Ajouter deux champs au modèle ne doit invalider AUCUNE entrée
        existante. La portée n'entre dans l'empreinte que lorsqu'elle vaut
        autre chose que « complète » : une entrée d'avant le champ hache donc
        exactement la même chaîne qu'avant, et la chaîne d'intégrité tient sans
        rescellement — lequel, sur un registre légal, se remarque.
        """
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner",
            res_name="Entrée de portée complète",
            destruction_method="delete",
        ))
        attendu = hashlib.sha256(
            self._charge_heritee(entree, entree.previous_hash or "").encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            entree.verification_hash, attendu,
            "L'empreinte d'une entrée « portée complète » a changé : la mise à "
            "jour invaliderait tous les registres déjà en production.",
        )

    def test_partial_scope_is_sealed(self):
        """La portée n'échappe pas au sceau : sur une entrée partielle, la clé
        EST dans la charge, donc la basculer après coup casse l'empreinte."""
        survivant = self._cible("Scellé partiel 24897")
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner",
            res_id=survivant.id,
            res_name=survivant.name,
            destruction_method="delete",
            destruction_scope="partial",
            res_field="pièces jointes",
        ))
        heritee = hashlib.sha256(
            self._charge_heritee(entree, entree.previous_hash or "").encode("utf-8")
        ).hexdigest()
        self.assertNotEqual(
            entree.verification_hash, heritee,
            "Une entrée partielle hache comme une entrée complète : la portée "
            "pourrait être basculée après coup sans que le sceau bronche.",
        )

    def test_chain_integrity_survives_mixed_scopes(self):
        """Le cron d'intégrité ne doit voir aucune rupture après un mélange
        d'entrées complètes et partielles."""
        survivant = self._cible("Chaîne mixte 24897")
        self.Register.create(self._vals_registre(
            res_model="res.partner", res_name="Complète",
            destruction_method="delete",
        ))
        self.Register.create(self._vals_registre(
            res_model="res.partner", res_id=survivant.id, res_name=survivant.name,
            destruction_method="delete", destruction_scope="partial",
            res_field="pièces jointes",
        ))
        self.assertEqual(
            self.Register.cron_verify_chain_integrity(), 0,
            "La chaîne d'intégrité est rompue après l'ajout de la portée.",
        )

    # ------------------------------------------------------------------
    # 5. L'immuabilité couvre les nouveaux champs
    # ------------------------------------------------------------------

    def test_scope_cannot_be_rewritten_after_the_fact(self):
        """`write` n'accepte que `notes` — la portée n'y échappe pas."""
        entree = self.Register.create(self._vals_registre(
            res_model="res.partner", res_name="Immuable 24897",
            destruction_method="delete",
        ))
        with self.assertRaises(UserError):
            entree.write({"destruction_scope": "partial"})
        with self.assertRaises(UserError):
            entree.write({"res_field": "quelque chose"})
        entree.write({"notes": "Une note reste permise."})
        self.assertIn("permise", entree.notes)
