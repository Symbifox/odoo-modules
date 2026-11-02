from datetime import timedelta
from unittest.mock import patch

from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

NAVIGATEUR = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)
JOURNAL = "odoo.addons.bf_editorial_audience_privacy.models.website_visitor"
ROBOT = "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)"


@tagged("post_install", "-at_install", "bf_editorial_audience_privacy")
class TestEditorialAudiencePrivacy(TransactionCase):
    """Déclarer, appliquer la durée déclarée, et inscrire chaque effacement
    sans rien certifier de plus que ce qui a eu lieu."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Visitor = cls.env["website.visitor"].sudo()
        cls.Register = cls.env["privacy.destruction.register"].sudo()
        cls.Param = cls.env["ir.config_parameter"].sudo()
        cls.purpose = cls.env.ref(
            "bf_editorial_audience_privacy.purpose_editorial_audience")
        cls.policy = cls.env.ref(
            "bf_editorial_audience_privacy.retention_policy_editorial_audience")
        cls.calendar = cls.env.ref(
            "bf_editorial_audience_privacy.retention_editorial_audience_agent")
        # Les visiteurs déjà en base ne doivent rien fausser des comptes.
        cls.env.cr.execute("UPDATE website_visitor SET user_agent = NULL")
        cls.Visitor.invalidate_model(["user_agent"])
        cls.Param.set_param("website.visitor.live.days", "60")
        cls.partner = cls.env["res.partner"].create({
            "name": "Lectrice rattachée (essai)",
            "email": "lectrice-essai@nunyabiznu.invalid",
        })
        cls._seq = 0

    # --- outils ----------------------------------------------------------
    def _visiteur(self, jours, agent=NAVIGATEUR, partner=None, site=None):
        type(self)._seq += 1
        token = str(partner.id) if partner else "%032x" % (0xB0000A000 + self._seq)
        vals = {"access_token": token}
        if site:
            vals["website_id"] = site.id
        visiteur = self.Visitor.create(vals)
        if agent:
            from odoo.addons.bf_editorial_audience.models import robots
            is_bot, famille = robots.classer(agent)
            visiteur.write({
                "user_agent": agent, "is_bot": is_bot, "agent_family": famille,
            })
        visiteur.write({
            "last_connection_datetime":
                fields.Datetime.now() - timedelta(days=jours),
        })
        return visiteur

    def _entrees_depuis(self, avant):
        return self.Register.search(
            [("id", ">", avant), ("res_model", "=", "website.visitor")],
            order="id")

    def _dernier_id(self):
        return self.Register.search([], order="id desc", limit=1).id or 0

    def _autre_societe(self):
        autre = self.env["res.company"].create({"name": "Autre société (essai)"})
        site_autre = self.env["website"].create({
            "name": "Site de l'autre société (essai)", "company_id": autre.id,
        })
        return autre, site_autre

    # --- déclarations ----------------------------------------------------
    def test_declarations(self):
        self.assertEqual(self.policy.purpose_id, self.purpose)
        self.assertEqual(self.policy.retention_days, 60)
        self.assertFalse(self.purpose.requires_consent)
        self.assertEqual(self.calendar.code, "AUD-001")
        self.assertEqual(self.calendar.applies_to_model, "website.visitor")
        self.assertFalse(self.calendar.requires_approval)
        self.assertEqual(self.calendar.destruction_method, "delete")

    # --- chemin 1 : le ménage --------------------------------------------
    def test_menage_lit_la_politique_pas_le_parametre(self):
        # Un paramètre à 5 jours effacerait aussi celui de 10 jours : c'est la
        # politique (60) qui doit gagner.
        self.Param.set_param("bf_editorial_audience.ua_retention_days", "5")
        vieux = self._visiteur(61)
        vieux_rattache = self._visiteur(61, partner=self.partner)
        vieux_robot = self._visiteur(61, agent=ROBOT)
        recent = self._visiteur(59)
        dix_jours = self._visiteur(10)
        famille_robot = vieux_robot.agent_family
        avant = self._dernier_id()

        n = self.Visitor._cron_forget_agents()

        self.assertEqual(n, 3)
        for v in (vieux, vieux_rattache, vieux_robot):
            self.assertFalse(v.user_agent)
        self.assertTrue(vieux_robot.is_bot, "le verdict survit à l'effacement")
        self.assertEqual(vieux_robot.agent_family, famille_robot)
        self.assertEqual(recent.user_agent, NAVIGATEUR)
        self.assertEqual(dix_jours.user_agent, NAVIGATEUR)

        entrees = self._entrees_depuis(avant)
        self.assertEqual(len(entrees), 1)
        e = entrees
        self.assertEqual(e.destruction_scope, "partial")
        self.assertEqual(e.res_field, "user_agent")
        self.assertEqual(e.destruction_method, "delete")
        self.assertFalse(e.res_id)
        self.assertEqual(e.subject_count, 2, "trois visiteurs dont un robot")
        self.assertEqual(e.retention_calendar_id, self.calendar)
        self.assertIn("3 chaîne(s)", e.document_description)
        self.assertIn("60 jours", e.document_description)
        self.assertIn("Dont 1 robot(s)", e.document_description)
        self.assertIn("1 visiteur(s) rattaché(s)", e.document_description)
        self.assertNotIn(NAVIGATEUR, e.document_description or "")
        self.assertTrue(e.verification_hash)

    def test_menage_suit_un_changement_de_politique(self):
        dix_jours = self._visiteur(10)
        self.policy.retention_days = 5
        self.assertEqual(self.Visitor._cron_forget_agents(), 1)
        self.assertFalse(dix_jours.user_agent)

    def test_rien_d_echu_rien_d_inscrit(self):
        recent = self._visiteur(30)
        avant = self._dernier_id()
        self.assertEqual(self.Visitor._cron_forget_agents(), 0)
        self.assertEqual(recent.user_agent, NAVIGATEUR)
        self.assertFalse(self._entrees_depuis(avant))

    def test_politique_archivee_rend_la_main_au_parametre(self):
        vieux = self._visiteur(61)
        self.policy.active = False
        self.Param.set_param("bf_editorial_audience.ua_retention_days", "0")
        avant = self._dernier_id()
        self.assertEqual(self.Visitor._cron_forget_agents(), 0)
        self.assertEqual(vieux.user_agent, NAVIGATEUR,
                         "paramètre à 0 : rien n'est effacé")
        self.Param.set_param("bf_editorial_audience.ua_retention_days", "30")
        self.assertEqual(self.Visitor._cron_forget_agents(), 1)
        self.assertFalse(vieux.user_agent)
        self.assertFalse(self._entrees_depuis(avant),
                         "sans politique, aucune durée déclarée, rien d'inscrit")

    def test_duree_nulle_refusee_pour_cette_finalite(self):
        with self.assertRaises(ValidationError):
            self.policy.retention_days = 0
        autre = self.env["privacy.purpose"].create({
            "code": "essai_autre", "name": "Autre finalité (essai)",
        })
        # La garde ne vise que la finalité du pont.
        self.env["privacy.retention.policy"].create({
            "purpose_id": autre.id, "retention_days": 0,
        })

    # --- chemin 2 : la purge native d'Odoo -------------------------------
    def test_purge_native_inscrite_portee_entiere(self):
        porteur = self._visiteur(61)
        porteur_robot = self._visiteur(61, agent=ROBOT)
        sans_chaine = self._visiteur(61, agent=None)
        rattache = self._visiteur(61, partner=self.partner)
        ids = (porteur | porteur_robot | sans_chaine).ids
        avant = self._dernier_id()

        self.Visitor._cron_unlink_old_visitors()

        self.assertFalse(self.Visitor.browse(ids).exists())
        self.assertTrue(rattache.exists(), "Odoo ne purge pas un visiteur rattaché")
        entrees = self._entrees_depuis(avant)
        self.assertEqual(len(entrees), 1)
        self.assertEqual(entrees.destruction_scope, "full")
        self.assertFalse(entrees.res_field)
        self.assertFalse(entrees.res_id)
        self.assertEqual(entrees.subject_count, 1)
        self.assertIn("2 visiteur(s) anonyme(s)", entrees.document_description)

    def test_purge_sans_chaine_rien_d_inscrit(self):
        sans_chaine = self._visiteur(61, agent=None)
        avant = self._dernier_id()
        self.Visitor._cron_unlink_old_visitors()
        self.assertFalse(sans_chaine.exists())
        self.assertFalse(self._entrees_depuis(avant))

    def test_suppression_hors_purge_rien_d_inscrit(self):
        # Fusion de visiteurs, suppression à la main : pas une destruction au
        # titre de la conservation.
        visiteur = self._visiteur(1)
        avant = self._dernier_id()
        visiteur.unlink()
        self.assertFalse(self._entrees_depuis(avant))

    def test_les_deux_chemins_ne_comptent_pas_deux_fois(self):
        self._visiteur(61)
        avant = self._dernier_id()
        self.Visitor._cron_forget_agents()
        self.Visitor._cron_unlink_old_visitors()
        entrees = self._entrees_depuis(avant)
        self.assertEqual(entrees.mapped("destruction_scope"), ["partial"],
                         "la purge emporte un visiteur déjà sans chaîne")

    # --- correctifs de la relecture adverse --------------------------------
    def test_purge_continue_si_le_registre_est_en_panne(self):
        """L'inscription ne bloque jamais la purge native : le visiteur part,
        l'échec est journalisé, la transaction reste utilisable.

        La panne est une erreur SQL (transaction avortée sans point de
        reprise), le cas le plus dur. Pas d'assertRaises ici : on conclut sur
        l'état après la purge, qui ne doit pas avoir levé.
        """
        porteur = self._visiteur(61)
        avant = self._dernier_id()

        def panne(*args, **kwargs):
            self.env.cr.execute("SELECT 1/0", log_exceptions=False)

        with patch.object(type(self.Register), "create", side_effect=panne), \
                self.assertLogs(JOURNAL, level="ERROR") as journal:
            self.Visitor._cron_unlink_old_visitors()

        self.assertFalse(porteur.exists(), "la purge a supprimé le visiteur")
        self.assertFalse(self._entrees_depuis(avant))
        self.assertEqual(len(journal.records), 1)
        self.assertIn("1 chaîne(s) d'agent emportée(s) par la purge native SANS "
                      "inscription", journal.output[0])
        # Le registre remarche ensuite, dans la même transaction.
        self._visiteur(61)
        self.Visitor._cron_unlink_old_visitors()
        self.assertEqual(len(self._entrees_depuis(avant)), 1)

    def test_purge_ignore_un_visiteur_disparu_entre_deux_lots(self):
        porteur = self._visiteur(61)
        disparu = self._visiteur(61)
        lot = self.Visitor.browse([porteur.id, disparu.id])
        # Fusionné par une connexion entre la recherche et son lot.
        self.env.cr.execute("DELETE FROM website_visitor WHERE id = %s", [disparu.id])
        self.env.invalidate_all()
        avant = self._dernier_id()

        lot.with_context(bf_ua_privacy_purge=True).unlink()

        self.assertFalse(porteur.exists())
        entrees = self._entrees_depuis(avant)
        self.assertEqual(len(entrees), 1)
        self.assertIn("1 visiteur(s) anonyme(s)", entrees.document_description)

    def test_menage_inscrit_a_la_societe_du_site(self):
        autre, _site_autre = self._autre_societe()
        site = self.env.ref("website.default_website")
        self.assertNotEqual(site.company_id, autre)
        vieux = self._visiteur(61, site=site)
        avant = self._dernier_id()

        # L'usager du cron travaille dans l'autre société.
        self.Visitor.with_company(autre)._cron_forget_agents()

        self.assertFalse(vieux.user_agent)
        entrees = self._entrees_depuis(avant)
        self.assertEqual(len(entrees), 1)
        self.assertEqual(entrees.company_id, site.company_id)

    def test_purge_une_entree_par_societe(self):
        autre, site_autre = self._autre_societe()
        site = self.env.ref("website.default_website")
        self._visiteur(61, site=site)
        self._visiteur(61, site=site_autre)
        self._visiteur(61, site=site_autre, agent=ROBOT)
        sans_site = self._visiteur(61)
        avant = self._dernier_id()

        self.Visitor.with_company(autre)._cron_unlink_old_visitors()

        self.assertFalse(sans_site.exists())
        entrees = self._entrees_depuis(avant)
        par_societe = {e.company_id: e for e in entrees}
        self.assertEqual(len(entrees), 2)
        self.assertEqual(set(par_societe), {site.company_id, autre})
        # Le visiteur sans site va à la société principale.
        self.assertEqual(self.env.ref("base.main_company"), site.company_id)
        self.assertIn("2 visiteur(s) anonyme(s)",
                      par_societe[site.company_id].document_description)
        self.assertIn("2 visiteur(s) anonyme(s)",
                      par_societe[autre].document_description)
        self.assertEqual(par_societe[autre].subject_count, 1)

    # --- correctifs du tour 2 ------------------------------------------------
    def test_drapeau_forge_par_un_concepteur_ignore(self):
        """Un appelant non superutilisateur qui forge le drapeau de la purge
        supprime le visiteur comme d'ordinaire : rien n'est inscrit au registre
        immuable."""
        concepteur = new_test_user(
            self.env, login="concepteur-essai",
            groups="base.group_user,website.group_website_designer",
        )
        site = self.env.ref("website.default_website")
        porteur = self._visiteur(61, site=site)
        avant = self._dernier_id()

        lot = self.env["website.visitor"].with_user(concepteur).browse(porteur.id)
        self.assertFalse(lot.env.su)
        lot.with_context(bf_ua_privacy_purge=True).unlink()

        self.assertFalse(porteur.exists(), "la suppression ordinaire a lieu")
        self.assertFalse(self._entrees_depuis(avant),
                         "un drapeau forgé ne grave aucune « purge native »")

    def test_purge_native_sous_su_meme_sans_sudo_de_l_appelant(self):
        """Le cron appelle la purge sur ``model`` (pas de ``sudo()``) : les
        lots passés à ``unlink`` portent quand même ``su``."""
        porteur = self._visiteur(61)
        avant = self._dernier_id()
        appelant = self.env["website.visitor"].with_user(self.env.ref("base.user_admin"))
        self.assertFalse(appelant.env.su)

        appelant._cron_unlink_old_visitors()

        self.assertFalse(porteur.exists())
        self.assertEqual(len(self._entrees_depuis(avant)), 1)

    def test_date_du_nom_au_fuseau_de_toronto(self):
        # 02 h UTC le 17 = 22 h à Toronto le 16 (l'usager root n'a pas de fuseau).
        with freeze_time("2026-10-17 02:00:00"):
            self._visiteur(61)
            avant = self._dernier_id()
            self.Visitor._cron_forget_agents()
        entree = self._entrees_depuis(avant)
        self.assertEqual(len(entree), 1)
        self.assertIn("2026-10-16", entree.res_name)
        self.assertNotIn("2026-10-17", entree.res_name)
