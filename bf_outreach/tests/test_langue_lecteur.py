# -*- coding: utf-8 -*-
"""L'avis « … vous est attribuée » d'une relance arrive dans la langue de l'assigné.

Quand une activité est créée pour quelqu'un d'autre, Odoo lui envoie un avis
(`mail.activity.action_notify`). Le corps de cet avis est rendu dans la langue de
l'assigné, mais son OBJET (`"%(activity_name)s: %(summary)s" assigned to you`)
passe par `_()`, qui lit la langue de l'environnement qui a créé l'activité. Les
relances du démarchage sont créées par l'action planifiée : l'objet sortait dans
la langue du travail, et non dans celle du responsable qui le lit.

Le module crée l'activité dans l'environnement de l'assigné
(outreach_campaign.py:572, outreach_target.py:854). Chaque essai fait agir une
langue pour un assigné de l'autre et lit l'objet de l'avis posé.

Les essais activent fr_CA et en_CA et donnent une langue explicite à chaque
usager créé. Aucun courriel ne sort : l'avis est un `mail.message`, son envoi
n'est pas forcé.
"""
from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged

# L'objet de l'avis d'Odoo (module mail) : source anglaise, français de mail/i18n/fr.po.
OBJET_EN = "assigned to you"
OBJET_FR = "vous est attribuée"


@tagged("post_install", "-at_install", "bf_outreach")
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for code in ("fr_CA", "en_CA"):
            cls.env["res.lang"]._activate_lang(code)
        cls.env["ir.module.module"]._load_module_terms(
            ["bf_outreach"], ["fr_CA"], overwrite=True)
        groupes = [
            Command.link(cls.env.ref("base.group_user").id),
            Command.link(cls.env.ref("bf_outreach.group_bf_outreach_user").id),
            Command.link(cls.env.ref("sales_team.group_sale_salesman").id),
        ]
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.user_fr = Users.create({
            "name": "Francine Avis", "login": "essai_avis_fr",
            "lang": "fr_CA", "groups_id": groupes,
        })
        cls.user_en = Users.create({
            "name": "Edward Notice", "login": "essai_avis_en",
            "lang": "en_CA", "groups_id": groupes,
        })
        cls.today = fields.Date.context_today(cls.env["bf.outreach.campaign"])

    def _campagne(self, owner, **values):
        return self.env["bf.outreach.campaign"].create(dict({
            "name": "Campagne avis",
            "user_id": owner.id,
            "date_start": self.today - timedelta(days=30),
            "state": "running",
            "call_target_count": 2,
            "email_target_count": 2,
            "working_days_only": False,
        }, **values))

    def _cible(self, campagne, owner, **values):
        return self.env["bf.outreach.target"].create(dict({
            "name": "Cible avis",
            "campaign_id": campagne.id,
            "user_id": owner.id,
            "email": "cible.avis@example.com",
        }, **values))

    def _repere(self):
        """Le dernier message avant la relance : l'avis « vous avez été assigné » de la
        campagne ou de la cible, posé à leur création, n'est pas celui qu'on lit."""
        return self.env["mail.message"].search([], order="id desc", limit=1).id

    def _objet_de_l_avis(self, activite, assigne, repere):
        self.assertEqual(activite.user_id, assigne)
        avis = self.env["mail.message"].search([
            ("id", ">", repere),
            ("model", "=", activite.res_model),
            ("res_id", "=", activite.res_id),
            ("message_type", "=", "user_notification"),
            ("partner_ids", "in", assigne.partner_id.ids),
        ])
        self.assertEqual(len(avis), 1, "un seul avis attendu pour l'assigné")
        return avis.subject or ""

    # ------------------------------------------------------------------
    # Relance quotidienne de la campagne (outreach_campaign.py:572)
    # ------------------------------------------------------------------
    def test_avis_de_relance_de_campagne_responsable_francais(self):
        """Travail en en_CA, responsable fr_CA : l'objet de l'avis est en français."""
        campagne = self._campagne(self.user_fr)
        self._cible(campagne, self.user_fr)
        repere = self._repere()
        activite = campagne.with_context(
            lang="en_CA", mail_notify_force_send=False)._generate_followup_activities()
        objet = self._objet_de_l_avis(activite, self.user_fr, repere)
        self.assertIn(OBJET_FR, objet)
        self.assertNotIn(OBJET_EN, objet)
        self.assertIn("Démarchage :", objet)

    def test_avis_de_relance_de_campagne_responsable_anglais(self):
        """Travail en fr_CA, responsable en_CA : l'objet de l'avis est en anglais."""
        campagne = self._campagne(self.user_en)
        self._cible(campagne, self.user_en)
        repere = self._repere()
        activite = campagne.with_context(
            lang="fr_CA", mail_notify_force_send=False)._generate_followup_activities()
        objet = self._objet_de_l_avis(activite, self.user_en, repere)
        self.assertIn(OBJET_EN, objet)
        self.assertNotIn(OBJET_FR, objet)

    # ------------------------------------------------------------------
    # Relance par cible (outreach_target.py:854)
    # ------------------------------------------------------------------
    def test_avis_de_relance_par_cible_assigne_francais(self):
        """Travail en en_CA, assignée fr_CA : objet en français."""
        campagne = self._campagne(self.user_en, activity_mode="target")
        cible = self._cible(campagne, self.user_fr)
        repere = self._repere()
        activite = cible.with_context(
            lang="en_CA", mail_notify_force_send=False)._ensure_followup_activity()
        objet = self._objet_de_l_avis(activite, self.user_fr, repere)
        self.assertIn(OBJET_FR, objet)
        self.assertIn("Appel de démarchage", objet)

    def test_avis_de_relance_par_cible_assigne_anglais(self):
        """Travail en fr_CA, assigné en_CA : objet en anglais."""
        campagne = self._campagne(self.user_fr, activity_mode="target")
        cible = self._cible(campagne, self.user_en)
        repere = self._repere()
        activite = cible.with_context(
            lang="fr_CA", mail_notify_force_send=False)._ensure_followup_activity()
        objet = self._objet_de_l_avis(activite, self.user_en, repere)
        self.assertIn(OBJET_EN, objet)
        self.assertNotIn(OBJET_FR, objet)
        self.assertIn("Outreach call", objet)

    def test_langue_du_lecteur_bornee_aux_langues_installees(self):
        """Une langue désactivée (usager ou société) ne part plus au contexte : elle ferait lever
        « Invalid language code » à la création de l'activité. On retombe sur une langue installée."""
        campagne = self._campagne(self.user_fr)
        # Une langue qui n'est pas installée sur la base (jamais activée ici).
        inconnu = self.env["res.users"].new({"lang": "xx_XX"})
        lang = campagne._outreach_lang(inconnu)
        installees = {code for code, _n in self.env["res.lang"].get_installed()}
        self.assertIn(lang, installees)
        self.assertEqual(campagne._outreach_lang(self.user_en), "en_CA")
