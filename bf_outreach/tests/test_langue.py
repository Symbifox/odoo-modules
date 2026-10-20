# -*- coding: utf-8 -*-
"""Langue des textes écrits et livrés par le démarchage.

La source du module est l'anglais ; le français vit dans `i18n/fr_CA.po`. Ces
essais prouvent que :

* les textes écrits par un travail planifié (relances quotidiennes, relances par
  cible) suivent la langue de leur lecteur, le responsable ou l'assigné, et non
  celle du travail ; une relance par lettre porte son propre résumé ;
* le résumé de repli d'un courriel journalisé suit la langue du responsable de
  la cible ;
* la note au fil d'une interaction suit la langue de l'usager qui agit, et son
  nom affiché se recalcule dans la langue de lecture ;
* le nom et l'historique d'une opportunité CRM suivent la langue du vendeur ;
* la configuration de recherche universelle est semée avec une source anglaise
  et une traduction française, sans écraser une valeur retouchée ;
* les données livrées (étapes, accueil, règle de couleur) ont une source anglaise
  et une traduction française.

Les essais activent fr_CA et en_CA et donnent une langue explicite à chaque
usager créé : l'administrateur de la base peut être réglé sur l'une ou l'autre.
Chaque essai fait agir une langue pour un lecteur de l'autre.
"""
from datetime import timedelta

from lxml import etree

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged
from odoo.tools import file_open

from odoo.addons.bf_outreach.models import post_init


@tagged("post_install", "-at_install", "bf_outreach")
class TestLangue(TransactionCase):

    # (fichier de données, identifiant, champs) relus tels que livrés.
    LIVRES = (
        ("data/outreach_stage_data.xml", "stage_to_contact", ("name", "description")),
        ("data/bf_onboarding.xml", "bf_onb_step_campaign", ("title", "button_text")),
        ("data/bf_onboarding.xml", "bf_onboarding_panel", ("name",)),
        ("data/bf_color_rules.xml", "bf_color_rule_target_user", ("name",)),
    )

    @classmethod
    def _valeur_livree(cls, fichier, xmlid, champ):
        """La valeur source écrite dans le fichier de données du module."""
        with file_open(f"bf_outreach/{fichier}", "rb") as f:
            arbre = etree.parse(f)
        noeud = arbre.find(f".//record[@id='{xmlid}']/field[@name='{champ}']")
        return (noeud.text or "").strip()

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        # Une base copiée peut porter des données retouchées (elles sont `noupdate`) :
        # on remet la source livrée avant de charger le catalogue.
        for fichier, xmlid, champs in cls.LIVRES:
            rec = cls.env.ref(f"bf_outreach.{xmlid}").sudo().with_context(lang="en_US")
            rec.write({c: cls._valeur_livree(fichier, xmlid, c) for c in champs})
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_outreach"], ["fr_CA"], overwrite=True
        )
        groupes = [
            Command.link(cls.env.ref("base.group_user").id),
            Command.link(cls.env.ref("bf_outreach.group_bf_outreach_user").id),
            Command.link(cls.env.ref("sales_team.group_sale_salesman").id),
        ]
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.user_fr = Users.create({
            "name": "Francine Langue", "login": "essai_fr",
            "lang": "fr_CA", "groups_id": groupes,
        })
        cls.user_en = Users.create({
            "name": "Edward Language", "login": "essai_en",
            "lang": "en_CA", "groups_id": groupes,
        })
        cls.today = fields.Date.context_today(cls.env["bf.outreach.campaign"])
        cls.mt_comment = cls.env.ref("mail.mt_comment")

    def _campagne(self, owner, **values):
        return self.env["bf.outreach.campaign"].create(dict({
            "name": "Campagne langue",
            "user_id": owner.id,
            "date_start": self.today - timedelta(days=30),
            "state": "running",
            "call_target_count": 2,
            "email_target_count": 2,
            "working_days_only": False,
        }, **values))

    def _cible(self, campagne, owner, **values):
        return self.env["bf.outreach.target"].create(dict({
            "name": "Cible langue",
            "campaign_id": campagne.id,
            "user_id": owner.id,
            "email": "cible.langue@example.com",
        }, **values))

    # ------------------------------------------------------------------
    # 1. Activité quotidienne de la campagne (action planifiée)
    # ------------------------------------------------------------------
    def test_relance_de_campagne_dans_la_langue_du_responsable(self):
        """Le résumé quotidien suit le responsable, pas l'environnement du travail."""
        campagne = self._campagne(self.user_fr)
        self._cible(campagne, self.user_fr)
        activite = campagne.with_context(lang="en_CA")._generate_followup_activities()
        self.assertEqual(len(activite), 1)
        self.assertTrue(activite.summary.startswith("Démarchage : 1 contact(s) dus"),
                        activite.summary)
        # Second passage du jour : l'activité existante est réécrite, toujours en français.
        encore = campagne.with_context(lang="en_CA")._generate_followup_activities()
        self.assertEqual(encore, activite)
        self.assertTrue(activite.summary.startswith("Démarchage :"), activite.summary)

    def test_relance_de_campagne_anglaise_lancee_en_francais(self):
        campagne = self._campagne(self.user_en)
        self._cible(campagne, self.user_en)
        activite = campagne.with_context(lang="fr_CA")._generate_followup_activities()
        self.assertTrue(activite.summary.startswith("Outreach: 1 contact(s) due"),
                        activite.summary)

    # ------------------------------------------------------------------
    # 2. Relances par cible : langue de l'assigné, résumé propre à la lettre
    # ------------------------------------------------------------------
    def test_relance_par_cible_dans_la_langue_de_l_assigne(self):
        campagne = self._campagne(self.user_en, activity_mode="target")
        cible = self._cible(campagne, self.user_fr)
        self.assertEqual(cible.next_action_kind, "call")
        activite = cible.with_context(lang="en_CA")._ensure_followup_activity()
        self.assertEqual(activite.user_id, self.user_fr)
        self.assertEqual(activite.summary, "Appel de démarchage")

    def test_relance_par_lettre_a_son_propre_resume(self):
        """Une relance par lettre ne s'appelle plus « Courriel de démarchage »."""
        campagne = self._campagne(
            self.user_en, activity_mode="target",
            call_target_count=0, email_target_count=0, letter_target_count=1,
        )
        cible_en = self._cible(campagne, self.user_en, name="Lettre EN")
        cible_fr = self._cible(campagne, self.user_fr, name="Lettre FR")
        self.assertEqual(cible_en.next_action_kind, "letter")
        act_en = cible_en.with_context(lang="fr_CA")._ensure_followup_activity()
        act_fr = cible_fr.with_context(lang="en_CA")._ensure_followup_activity()
        self.assertEqual(act_en.summary, "Outreach letter")
        self.assertEqual(act_fr.summary, "Lettre de démarchage")

    # ------------------------------------------------------------------
    # 3. Repli « Email sent » stocké sur l'interaction
    # ------------------------------------------------------------------
    def test_envoi_en_lot_resume_dans_la_langue_du_responsable(self):
        campagne = self._campagne(self.user_en)
        cible_fr = self._cible(campagne, self.user_fr, name="Lot FR")
        cible_en = self._cible(campagne, self.user_en, name="Lot EN",
                               email="lot.en@example.com")
        touches = cible_fr.with_context(lang="en_CA")._log_mass_email_touches(None)
        self.assertEqual(touches.summary, "Courriel envoyé")
        touches = cible_en.with_context(lang="fr_CA")._log_mass_email_touches(None)
        self.assertEqual(touches.summary, "Email sent")

    def test_courriel_du_fil_resume_dans_la_langue_du_responsable(self):
        campagne = self._campagne(self.user_en)
        partenaire = self.env["res.partner"].create({
            "name": "Destinataire langue", "email": "cible.langue@example.com",
        })
        cible = self._cible(campagne, self.user_fr, partner_id=partenaire.id)
        # Message fabriqué sans sujet, sans passer par l'envoi : rien ne sort.
        message = self.env["mail.message"].create({
            "model": "bf.outreach.target",
            "res_id": cible.id,
            "message_type": "comment",
            "subtype_id": self.mt_comment.id,
            "body": "<p>Bonjour</p>",
            "partner_ids": [Command.link(partenaire.id)],
        })
        cible.with_context(lang="en_CA")._log_touch_from_message(message)
        touch = cible.touch_ids.filtered(lambda t: t.mail_message_id == message)
        self.assertEqual(len(touch), 1)
        self.assertEqual(touch.summary, "Courriel envoyé")

    # ------------------------------------------------------------------
    # 4. Note au fil de l'interaction : langue de qui agit
    # ------------------------------------------------------------------
    def _note_de_l_interaction(self, cible, lang):
        avant = cible.message_ids
        self.env["bf.outreach.touch"].with_context(lang=lang).create({
            "target_id": cible.id, "kind": "call", "direction": "out",
            "outcome": "no_answer",
        })
        # Seule la note de l'interaction porte du gras (le suivi d'étape, non).
        notes = (cible.message_ids - avant).filtered(lambda m: "</b> (" in str(m.body))
        self.assertEqual(len(notes), 1)
        return str(notes.body)

    def test_note_d_interaction_dans_la_langue_de_qui_agit(self):
        campagne = self._campagne(self.user_en)
        cible = self._cible(campagne, self.user_en)
        corps_en = self._note_de_l_interaction(cible, "en_CA")
        self.assertIn("<b>Call</b> (Outgoing): No answer", corps_en)
        self.assertNotIn("—", corps_en)
        corps_fr = self._note_de_l_interaction(cible, "fr_CA")
        self.assertIn("<b>Appel</b> (Sortant) — Pas de réponse", corps_fr)

    # ------------------------------------------------------------------
    # 5. Nom affiché de l'interaction (calcul non stocké)
    # ------------------------------------------------------------------
    def test_nom_affiche_de_l_interaction_suit_la_langue(self):
        campagne = self._campagne(self.user_en)
        cible = self._cible(campagne, self.user_en)
        touch = self.env["bf.outreach.touch"].create({
            "target_id": cible.id, "kind": "call", "outcome": "voicemail",
        })
        touch.invalidate_recordset(["display_name"])
        nom_en = touch.with_context(lang="en_CA").display_name
        nom_fr = touch.with_context(lang="fr_CA").display_name
        self.assertTrue(nom_en.startswith("Call — "), nom_en)
        self.assertTrue(nom_fr.startswith("Appel — "), nom_fr)
        self.assertIn("Boîte vocale", nom_fr)

    # ------------------------------------------------------------------
    # 6. Opportunité CRM : langue du vendeur
    # ------------------------------------------------------------------
    def _opportunite(self, vendeur, lang_qui_agit):
        campagne = self._campagne(self.user_en, name="Campagne CRM")
        cible = self._cible(campagne, vendeur, name="Cible CRM")
        self.env["bf.outreach.touch"].create({
            "target_id": cible.id, "kind": "call", "outcome": "voicemail",
            "summary": "Message laissé",
        })
        self.env["bf.outreach.touch"].create({
            "target_id": cible.id, "kind": "letter", "outcome": "sent",
        })
        cible.with_context(lang=lang_qui_agit).action_create_lead()
        return cible.lead_id

    def test_opportunite_dans_la_langue_du_vendeur_francais(self):
        lead = self._opportunite(self.user_fr, "en_CA")
        self.assertEqual(lead.name, "Campagne CRM — Cible CRM")
        description = str(lead.description)
        self.assertIn("Issue de la campagne de démarchage", description)
        self.assertIn(" — Appel : Message laissé", description)
        self.assertIn(" — Lettre", description)
        self.assertNotIn("From the outreach campaign", description)

    def test_opportunite_dans_la_langue_du_vendeur_anglais(self):
        lead = self._opportunite(self.user_en, "fr_CA")
        self.assertEqual(lead.name, "Campagne CRM - Cible CRM")
        description = str(lead.description)
        self.assertIn("From the outreach campaign", description)
        self.assertIn(" - Call: Message laissé", description)
        self.assertIn(" - Letter", description)
        self.assertNotIn("—", description)

    # ------------------------------------------------------------------
    # 7. Recherche universelle : nom semé en anglais, traduit en fr_CA
    # ------------------------------------------------------------------
    def _config_recherche(self):
        if "bf.universal.search.config" not in self.env:
            self.skipTest("bf_universal_search n'est pas installé")
        model = self.env["ir.model"]._get("bf.outreach.target")
        return self.env["bf.universal.search.config"].sudo(), model

    def _nom_brut(self, config):
        config.flush_recordset(["name"])
        self.env.cr.execute(
            f'SELECT name FROM "{config._table}" WHERE id = %s', (config.id,)
        )
        return self.env.cr.fetchone()[0]

    def _poser_nom_brut(self, config, valeur):
        config.flush_recordset()
        self.env.cr.execute(
            f'UPDATE "{config._table}" SET name = %s::jsonb WHERE id = %s',
            (__import__("json").dumps(valeur), config.id),
        )
        config.invalidate_recordset(["name"])

    def test_recherche_universelle_creee_en_anglais(self):
        Config, model = self._config_recherche()
        Config.search([("model_id", "=", model.id)]).unlink()
        # Contexte vide, comme le crochet d'installation et la migration.
        env = self.env(context={})
        self.assertTrue(post_init.register_universal_search(env))
        config = Config.search([("model_id", "=", model.id)])
        self.assertEqual(len(config), 1)
        self.assertEqual(config.with_context(lang="en_US").name, "Outreach targets")
        self.assertEqual(config.with_context(lang="fr_CA").name, "Cibles de démarchage")
        if "category" in config._fields:
            self.assertEqual(config.category, "Démarchage")

    def test_recherche_universelle_ancien_nom_rafraichi(self):
        Config, model = self._config_recherche()
        configs = Config.search([("model_id", "=", model.id)])
        if not configs:
            post_init.register_universal_search(self.env(context={}))
            configs = Config.search([("model_id", "=", model.id)])
        config = configs[:1]
        # Ancienne valeur livrée : français dans la source, sans traduction.
        self._poser_nom_brut(config, {"en_US": "Cibles de démarchage"})
        post_init.register_universal_search(self.env(context={}))
        brut = self._nom_brut(config)
        self.assertEqual(brut.get("en_US"), "Outreach targets")
        self.assertEqual(brut.get("fr_CA"), "Cibles de démarchage")
        self.assertNotEqual(brut.get("en_CA"), "Cibles de démarchage")

    def test_recherche_universelle_retouche_laissee(self):
        Config, model = self._config_recherche()
        configs = Config.search([("model_id", "=", model.id)])
        if not configs:
            post_init.register_universal_search(self.env(context={}))
            configs = Config.search([("model_id", "=", model.id)])
        config = configs[:1]
        # Source retouchée par le client : rien ne bouge.
        self._poser_nom_brut(config, {"en_US": "Ma recherche"})
        post_init.register_universal_search(self.env(context={}))
        self.assertEqual(self._nom_brut(config), {"en_US": "Ma recherche"})
        # Traduction française retouchée : la source bascule, la retouche reste.
        self._poser_nom_brut(config, {"en_US": "Cibles de démarchage", "fr_CA": "Mes cibles"})
        post_init.register_universal_search(self.env(context={}))
        brut = self._nom_brut(config)
        self.assertEqual(brut.get("en_US"), "Outreach targets")
        self.assertEqual(brut.get("fr_CA"), "Mes cibles")

    # ------------------------------------------------------------------
    # 8. Données livrées : source anglaise, traduction française
    # ------------------------------------------------------------------
    def _en_et_fr(self, xmlid, champ):
        rec = self.env.ref(f"bf_outreach.{xmlid}")
        return rec.with_context(lang="en_US")[champ], rec.with_context(lang="fr_CA")[champ]

    def test_etape_nom_et_description_traduits(self):
        etape = self.env.ref("bf_outreach.stage_to_contact")
        self.assertTrue(etape._fields["description"].translate)
        self.assertEqual(self._en_et_fr("stage_to_contact", "name"), ("To contact", "À contacter"))
        self.assertEqual(
            self._en_et_fr("stage_to_contact", "description"),
            ("Target added to the campaign, not contacted yet.",
             "Cible ajoutée à la campagne, aucun contact encore fait."),
        )

    def test_accueil_traduit(self):
        self.assertEqual(self._en_et_fr("bf_onb_step_campaign", "title"),
                         ("Create a campaign", "Créer une campagne"))
        self.assertEqual(self._en_et_fr("bf_onb_step_campaign", "button_text"),
                         ("See the campaigns", "Voir les campagnes"))
        self.assertEqual(self._en_et_fr("bf_onboarding_panel", "name"),
                         ("Outreach", "Démarchage"))

    def test_regle_de_couleur_traduite(self):
        self.assertEqual(self._en_et_fr("bf_color_rule_target_user", "name"),
                         ("Targets by owner", "Cibles selon le responsable"))
