"""La tuile Budget : ce qu'elle montre, et à qui.

Les essais de rôle sont le cœur : le tableau de bord est l'écran d'accueil de
TOUT usager interne, alors que `bf.budget` n'est ouvert qu'à un groupe. Une
tuile qui lit sans garde transforme chaque visite d'un employé ordinaire en
`AccessError` journalisée, et affiche « Données non disponibles » à la place
d'une tuile qui n'est simplement pas pour lui.
"""
import glob
import logging
import os

from odoo import fields
from odoo.tests.common import tagged
from odoo.modules.module import get_module_path
from odoo.tools import mute_logger

from odoo.addons.bf_budget.tests.common import BfBudgetCommon

#: Le journal de ce pont. Les collecteurs de `bf_home` journalisent sous le
#: leur, que les essais de rôle font taire : ce qu'ils vérifient, c'est la tuile.
TILE_LOGGER = "odoo.addons.bf_budget_dashboard"
HOME_LOGGER = "odoo.addons.bf_home"

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class TestBudgetTile(BfBudgetCommon):

    def test_la_cle_est_toujours_la(self):
        """Le gabarit lit `state.data.budget` : la clé doit exister, vide ou non."""
        data = self.env["bf.dashboard"].get_dashboard_data()
        self.assertIn("budget", data)

    def test_l_ancre_du_gabarit_tient_toujours(self):
        """🔴 Un xpath d'extension OWL qui ne résout plus NE LÈVE PAS.

        La tuile disparaîtrait de l'accueil sans un mot, et rien dans les essais
        Python ne le verrait. On vérifie donc que l'expression sur laquelle la
        tuile s'ancre existe encore dans le gabarit de `bf_home`, et que c'est
        bien celle que le nôtre cite.
        """
        ancre = "state.data.overdue_activities"
        gabarits = os.path.join(get_module_path("bf_home"), "static/src/xml")
        textes = ""
        for chemin in sorted(glob.glob(os.path.join(gabarits, "*.xml"))):
            with open(chemin, encoding="utf-8") as fichier:
                textes += fichier.read()
        self.assertIn(ancre, textes, "bf_home a déplacé l'ancre de la tuile")
        with open(os.path.join(
            get_module_path("bf_budget_dashboard"), "static/src/xml/tile.xml"),
            encoding="utf-8",
        ) as fichier:
            self.assertIn(ancre, fichier.read())

    def test_le_cout_de_la_tuile_reste_borne(self):
        """L'accueil se charge à chaque visite : la tuile doit rester bon marché.

        Le plafond ne prétend pas être une science : il fige l'ordre de grandeur
        mesuré sur un budget d'essai de douze lignes. Il tombera le jour où un
        calcul se mettra à lire ligne par ligne ce qu'il lisait par groupe.
        """
        budget = self._make_budget(planned=1200.0, state="draft")
        for index in range(11):
            compte = self.env["account.account"].with_company(self.company).create({
                "name": "Charge %s" % index, "code": "6041%02d" % index,
                "account_type": "expense",
            })
            position = self.env["bf.budget.position"].create({
                "name": "Poste coût %s" % index, "code": "C%02d" % index,
                "budget_type": "expense", "company_id": self.company.id,
                "account_ids": [(6, 0, compte.ids)],
            })
            self.env["bf.budget.line"].create({
                "budget_id": budget.id, "position_id": position.id,
                "amount_planned": 100.0,
            })
        budget.action_open()
        self.env.invalidate_all()
        self.env.flush_all()
        avant = self.cr.sql_log_count
        summary = self.env["bf.dashboard"]._get_budget_summary()
        cout = self.cr.sql_log_count - avant
        self.assertTrue(summary)
        _logger.info("coût de la tuile : %s requêtes pour 12 lignes", cout)
        self.assertLess(
            cout, 70,
            "la tuile a coûté %s requêtes pour 12 lignes (mesuré : 49). "
            "Le plafond fige l'ordre de grandeur : trop lâche, il laisse "
            "passer une régression d'un facteur 4." % cout,
        )

    def test_sans_budget_ouvert_pas_de_tuile(self):
        """Un brouillon n'est pas un budget suivi : rien à montrer, aucune erreur."""
        self._make_budget(planned=1200.0)
        summary = self.env["bf.dashboard"]._get_budget_summary()
        self.assertIsNone(summary)

    def test_la_tuile_resume_le_budget_ouvert(self):
        budget = self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 600.0)
        self.env.invalidate_all()
        summary = self.env["bf.dashboard"]._get_budget_summary()
        self.assertTrue(summary)
        self.assertEqual(summary["name"], budget.display_name)
        self.assertEqual(summary["kind"], "expense")
        self.assertEqual(summary["consumed_display"], "50 %")
        self.assertEqual(summary["budget_count"], 1)

    def test_les_lignes_signalees_comptent_tous_les_budgets_ouverts(self):
        """Le compte porte sur tous les budgets ouverts, pas sur le seul cité.

        🔴 L'alerte est posée exprès sur le budget qui n'est PAS cité : à
        exercice égal, la tuile cite le dernier créé. Une première version
        mettait la dérive sur le budget cité, et comptait donc la même chose des
        deux façons : la mutation « ne compter que le budget cité » passait au
        vert. Manquer un dépassement parce qu'il est sur l'autre budget serait
        pire que de n'avoir aucune tuile.
        """
        premier = self._make_budget(planned=1200.0, state="open")
        second = self._make_budget(
            positions=[self.position_telecom], planned=1200.0, state="open"
        )
        # Une dérive franche sur le PREMIER : 12 000 $ dépensés sur 1 200 $ prévus.
        self._post_bill(self.account_software, 12000.0)
        self.env.invalidate_all()
        self.assertTrue(premier.line_ids.is_alert)
        self.assertFalse(second.line_ids.is_alert)
        summary = self.env["bf.dashboard"]._get_budget_summary()
        self.assertEqual(
            summary["name"], second.display_name,
            "le budget cité doit être celui SANS alerte pour que l'essai discrimine",
        )
        self.assertEqual(summary["budget_count"], 2)
        self.assertEqual(summary["alert_lines"], 1)

    def test_un_budget_sans_montant_ne_porte_pas_de_pourcentage(self):
        """Plutôt rien qu'une division par zéro déguisée en « 0 % »."""
        budget = self._make_budget(planned=0.0, state="open")
        self.env.invalidate_all()
        summary = self.env["bf.dashboard"]._get_budget_summary()
        self.assertIsNone(summary["consumed_display"])
        self.assertEqual(summary["name"], budget.display_name)

    def test_la_tuile_voit_les_societes_cochees_pas_la_seule_societe_par_defaut(self):
        """🔴 L'accueil et le digest doivent dire la même chose.

        Avec `env.company`, un dépassement sur une autre société cochée dans le
        sélecteur restait invisible sur l'accueil pendant que le digest du matin
        le signalait. Une instance réelle compte souvent plusieurs sociétés :
        ce n'est pas un cas d'école.
        """
        autre = self.env["res.company"].create({"name": "Deuxième société"})
        self.env.user.write({"company_ids": [(4, autre.id)]})
        compte = self.env["account.account"].with_company(autre).create({
            "name": "Licences", "code": "602100", "account_type": "expense",
        })
        poste = self.env["bf.budget.position"].create({
            "name": "Licences (autre société)", "code": "SOFT2",
            "budget_type": "expense", "company_id": autre.id,
            "account_ids": [(6, 0, compte.ids)],
        })
        budget = self.env["bf.budget"].create({
            "name": "Exercice de l'autre société",
            "company_id": autre.id,
            "budget_type": "expense",
            "date_start": self.date_start,
            "date_end": self.date_end,
            "line_ids": [(0, 0, {"position_id": poste.id, "amount_planned": 1200.0})],
        })
        budget.action_open()
        self.env["account.move"].create({
            "move_type": "entry", "date": fields.Date.context_today(self.env.user),
            "company_id": autre.id,
            "journal_id": self.env["account.journal"].create({
                "name": "OD", "code": "OD2", "type": "general", "company_id": autre.id,
            }).id,
            "line_ids": [
                (0, 0, {"account_id": compte.id, "debit": 12000.0, "credit": 0.0, "name": "Charge"}),
                (0, 0, {"account_id": self.env["account.account"].with_company(autre).create({
                    "name": "Fournisseurs", "code": "200000",
                    "account_type": "liability_payable", "reconcile": True,
                }).id, "debit": 0.0, "credit": 12000.0, "name": "Contrepartie"}),
            ],
        }).action_post()
        self.env.invalidate_all()
        self.assertTrue(budget.line_ids.is_alert)

        une_seule = self.env["bf.dashboard"].with_company(self.company)
        deux = self.env["bf.dashboard"].with_context(
            allowed_company_ids=[self.company.id, autre.id]
        )
        self.assertEqual(une_seule._get_budget_summary(), None)
        self.env.invalidate_all()
        sommaire = deux._get_budget_summary()
        self.assertTrue(sommaire)
        self.assertEqual(
            sommaire["alert_lines"], 1,
            "le dépassement de l'autre société cochée doit être compté",
        )

    def test_le_compte_se_borne_quand_les_budgets_sont_gros(self):
        """Au-delà de la borne, la tuile compte le budget cité et le DIT.

        Le socle lit le réel ligne par ligne : laisser le compte courir sur des
        centaines de lignes coûterait des secondes à chaque chargement de
        l'accueil. Un chiffre partiel sans mention serait pire que la borne.
        """
        from odoo.addons.bf_budget_dashboard.models.bf_dashboard import MAX_LINES_SCANNED
        budget = self.env["bf.budget"].create({
            "name": "Gros budget",
            "company_id": self.company.id,
            "budget_type": "expense",
            "date_start": self.date_start,
            "date_end": self.date_end,
        })
        for index in range(MAX_LINES_SCANNED + 1):
            compte = self.env["account.account"].with_company(self.company).create({
                "name": "Charge %s" % index, "code": "605%03d" % index,
                "account_type": "expense",
            })
            poste = self.env["bf.budget.position"].create({
                "name": "Poste large %s" % index, "code": "L%03d" % index,
                "budget_type": "expense", "company_id": self.company.id,
                "account_ids": [(6, 0, compte.ids)],
            })
            self.env["bf.budget.line"].create({
                "budget_id": budget.id, "position_id": poste.id,
                "amount_planned": 100.0,
            })
        budget.action_open()
        # Un deuxième budget ouvert : sans lui, se borner au budget cité donne
        # le même chiffre que tout compter, et l'essai ne prouverait rien.
        autre = self._make_budget(positions=[self.position_telecom],
                                  planned=1200.0, state="open")
        self._post_bill(self.account_telecom, 12000.0)
        self.env.invalidate_all()
        self.assertTrue(autre.line_ids.is_alert)
        sommaire = self.env["bf.dashboard"]._get_budget_summary()
        self.assertTrue(
            sommaire["partial_count"],
            "au-delà de la borne, le compte se limite au budget cité",
        )
        self.assertEqual(sommaire["name"], autre.display_name)
        self.assertEqual(
            sommaire["alert_lines"], sommaire["shown_alert_lines"],
            "borné, le compte est celui du seul budget cité",
        )

    def test_le_rouge_de_la_tuile_suit_le_budget_cite(self):
        """Une bordure rouge au-dessus du nom d'un budget tenu dit une fausseté."""
        premier = self._make_budget(planned=1200.0, state="open")
        second = self._make_budget(
            positions=[self.position_telecom], planned=1200.0, state="open"
        )
        self._post_bill(self.account_software, 12000.0)
        self.env.invalidate_all()
        self.assertTrue(premier.line_ids.is_alert)
        sommaire = self.env["bf.dashboard"]._get_budget_summary()
        self.assertEqual(sommaire["name"], second.display_name)
        self.assertEqual(sommaire["alert_lines"], 1)
        self.assertEqual(
            sommaire["shown_alert_lines"], 0,
            "le budget cité n'a pas d'alerte : la tuile ne doit pas rougir",
        )

    def test_la_derive_est_un_montant_lisible(self):
        """« +1900$ » n'est pas un montant. En fr_CA on attend « 1 900,00 $ »."""
        self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 12000.0)
        self.env.invalidate_all()
        sommaire = self.env["bf.dashboard"]._get_budget_summary()
        self.assertIn("10", sommaire["drift_display"])
        self.assertNotEqual(sommaire["drift_display"], str(sommaire["drift"]))

    def test_le_budget_dont_je_reponds_passe_devant(self):
        autre = self.env["res.users"].create({
            "name": "Autre responsable", "login": "autre_budget_tuile",
            "company_id": self.company.id, "company_ids": [(6, 0, [self.company.id])],
        })
        # Les dates se posent à la création : un budget ouvert ne laisse plus
        # toucher son plan, et c'est le dessein du module.
        ancien = self.env["bf.budget"].create({
            "name": "Exercice d'avant",
            "company_id": self.company.id,
            "budget_type": "expense",
            "date_start": fields.Date.to_date("2024-01-01"),
            "date_end": fields.Date.to_date("2024-12-31"),
            "line_ids": [(0, 0, {
                "position_id": self.position_software.id,
                "amount_planned": 1200.0,
            })],
        })
        ancien.action_open()
        ancien.user_id = self.env.user
        recent = self._make_budget(
            positions=[self.position_telecom], planned=1200.0, state="open"
        )
        recent.user_id = autre
        self.env.invalidate_all()
        summary = self.env["bf.dashboard"]._get_budget_summary()
        self.assertEqual(
            summary["name"], ancien.display_name,
            "le budget dont la personne répond passe devant le plus récent",
        )


@tagged("post_install", "-at_install")
class TestBudgetTileRoles(BfBudgetCommon):
    """La tuile pour un employé ordinaire, et pour un lecteur de budget."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        commun = {"company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])]}
        group_user = cls.env.ref("base.group_user")
        group_budget = cls.env.ref("bf_budget.group_bf_budget_user")
        cls.ordinaire = cls.env["res.users"].create(dict(
            commun, name="Employée ordinaire", login="plain_budget_tile",
            groups_id=[(6, 0, [group_user.id])],
        ))
        cls.lectrice = cls.env["res.users"].create(dict(
            commun, name="Lectrice de budgets", login="reader_budget_tile",
            groups_id=[(6, 0, [group_user.id, group_budget.id])],
        ))
        cls.budget = cls._make_budget(planned=1200.0, state="open")
        cls._post_bill(cls.account_software, 600.0)

    def _dashboard(self, user):
        self.env.invalidate_all()
        return self.env["bf.dashboard"].with_user(user).with_company(self.company)

    def test_un_employe_ordinaire_n_a_pas_la_tuile_et_rien_au_journal(self):
        """🔴 La garde doit REFUSER avant de lire, pas rattraper après.

        Le niveau DEBUG est le point : sans la garde, la tuile tente la lecture,
        l'`AccessError` tombe dans le filet du bas et la tuile rend `None` quand
        même, en laissant une trace à chaque chargement de l'accueil. L'essai
        passerait alors pour la mauvaise raison, et on ne distinguerait plus
        « pas pour vous » de « cassé ».
        """
        with self.assertNoLogs(TILE_LOGGER, level="DEBUG"):
            summary = self._dashboard(self.ordinaire)._get_budget_summary()
        self.assertIsNone(summary)

    def test_le_vrai_chemin_de_l_accueil_pour_un_employe_ordinaire(self):
        """`get_dashboard_data()`, ce que l'écran appelle vraiment."""
        with mute_logger(HOME_LOGGER), self.assertNoLogs(TILE_LOGGER, level="WARNING"):
            data = self._dashboard(self.ordinaire).get_dashboard_data()
        self.assertIn("budget", data)
        self.assertIsNone(data["budget"])
        self.assertNotIn(
            "budget", data.get("failed") or {},
            "ne pas avoir le droit n'est pas une panne",
        )

    def test_une_lectrice_de_budgets_voit_la_tuile(self):
        with mute_logger(HOME_LOGGER), self.assertNoLogs(TILE_LOGGER, level="WARNING"):
            data = self._dashboard(self.lectrice).get_dashboard_data()
        summary = data["budget"]
        self.assertTrue(summary)
        self.assertEqual(summary["consumed_display"], "50 %")

    def test_la_lectrice_lit_les_montants_sans_droits_comptables(self):
        """Le socle calcule le réel en `sudo` : la tuile n'élargit rien.

        Ce que la lectrice voit sur la tuile, elle le voit déjà dans l'écran
        Budgets. L'essai le fige : si le socle cessait un jour de passer en
        `sudo`, la tuile deviendrait muette pour elle et on le saurait ici.
        """
        self.assertFalse(
            self.lectrice.has_group("account.group_account_readonly"),
            "l'essai n'a de sens que sans droits comptables",
        )
        summary = self._dashboard(self.lectrice)._get_budget_summary()
        self.assertEqual(summary["consumed_display"], "50 %")
