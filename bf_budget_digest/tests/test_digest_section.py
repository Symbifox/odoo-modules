"""La section « Budget » du digest quotidien.

Ce qui compte ici : qu'un jour calme reste calme, que la section n'aille qu'à
la personne qui répond du budget, et que le bloc injecté porte sa propre ligne
de tableau (sans quoi il flotte au-dessus de la carte dans le courriel).
"""
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from odoo.addons.bf_budget.tests.common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestDigestBudgetSection(BfBudgetCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env["daily.digest.config"].create({
            "name": "Digest d'essai budget", "company_id": cls.company.id,
        })
        # 🔴 De VRAIES personnes, pas `env.user`. Le décor d'un TransactionCase
        # tourne en superutilisateur (uid 1), que le socle refuse exprès comme
        # destinataire d'alerte : la section ne part qu'à qui peut lire le
        # budget (`_alert_recipient` : active, interne, de la société, membre du
        # groupe Budgets). C'est aussi ce que fait la production.
        groupe = cls.env.ref("bf_budget.group_bf_budget_user")
        commun = {
            "company_id": cls.company.id, "company_ids": [(6, 0, [cls.company.id])],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, groupe.id])],
        }
        cls.responsable = cls.env["res.users"].create(dict(
            commun, name="Responsable du budget", login="resp_budget_digest",
        ))
        cls.autre = cls.env["res.users"].create(dict(
            commun, name="Quelqu'un d'autre", login="autre_budget_digest",
        ))

    def _budget_en_alerte(self, user=None, planned=1200.0, spent=12000.0):
        budget = self._make_budget(planned=planned, state="open")
        budget.user_id = user or self.responsable
        self._post_bill(self.account_software, spent)
        self.env.invalidate_all()
        self.assertTrue(budget.line_ids.is_alert, "le décor doit vraiment alerter")
        return budget

    def test_un_jour_calme_reste_calme(self):
        """Pas de ligne signalée, pas de section. Un digest qui parle tous les
        jours pour ne rien dire finit par ne plus être lu."""
        self._make_budget(planned=1200.0, state="open")
        self.assertEqual(self.config._render_budget_section(self.responsable), "")

    def test_une_ligne_signalee_apparait(self):
        budget = self._budget_en_alerte()
        html = self.config._render_budget_section(self.responsable)
        self.assertIn("Budget", html)
        self.assertIn(self.position_software.name, html)
        self.assertIn(budget.display_name, html)
        self.assertIn("1 flagged line(s)", html)

    def test_la_case_eteint_la_section(self):
        self._budget_en_alerte()
        self.assertNotEqual(self.config._render_budget_section(self.responsable), "")
        self.config.include_budget = False
        self.assertEqual(self.config._render_budget_section(self.responsable), "")

    def test_la_section_suit_le_responsable(self):
        """Le budget de quelqu'un d'autre ne part pas dans mon digest.

        C'est la même règle que l'alerte du module : une personne par budget,
        sinon personne ne s'en occupe.
        """
        self._budget_en_alerte(user=self.autre)
        self.assertEqual(self.config._render_budget_section(self.responsable), "")
        self.assertNotEqual(self.config._render_budget_section(self.autre), "")

    def test_un_budget_sur_une_autre_de_mes_societes_parle_quand_meme(self):
        """🔴 La société COURANTE de l'écran n'est pas la portée des responsabilités.

        Le décor laisse l'usager sur sa société par défaut et met le budget sur
        la société d'essai, qui est une des siennes : c'est la situation
        ordinaire d'une base multi-sociétés. Filtrer sur
        `user.company_id` aurait tu l'alerte.
        """
        self.assertNotEqual(
            self.env.user.company_id, self.company,
            "l'essai n'a de sens que si la société courante est une autre",
        )
        self.assertIn(self.company, self.env.user.company_ids)
        self._budget_en_alerte()
        # ⚠️ `company_id` porte un défaut (la société courante) : un gabarit
        # sans société se crée en le vidant, pas en l'omettant.
        sans_societe = self.env["daily.digest.config"].create(
            {"name": "Gabarit sans société", "company_id": False}
        )
        self.assertFalse(sans_societe.company_id)
        self.assertNotEqual(sans_societe._render_budget_section(self.responsable), "")

    def test_un_responsable_qui_ne_lit_pas_les_budgets_ne_recoit_rien(self):
        """🔴 Le courriel ne part pas à qui l'ouverture du budget refuserait.

        Être nommé responsable ne donne pas le droit de lire : `user_id` n'a
        qu'un domaine de champ, qu'un import, un script ou un `write` par RPC
        ignorent, et une personne peut perdre le groupe Budgets après coup. Le
        socle refuse alors de lui confier l'activité. Le digest, lui, part par
        courriel : il doit refuser la même chose, sinon les chiffres d'un budget
        sortent chaque matin vers quelqu'un qui n'y a pas accès.
        """
        sans_droit = self.env["res.users"].create({
            "name": "Commis sans accès aux budgets", "login": "sans_droit_budget",
            "company_id": self.company.id, "company_ids": [(6, 0, [self.company.id])],
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
        })
        budget = self._budget_en_alerte(user=sans_droit)
        self.assertFalse(
            budget._alert_recipient(),
            "le socle refuse déjà de lui confier l'activité",
        )
        with self.assertRaises(AccessError):
            budget.with_user(sans_droit).read(["name"])
        self.assertEqual(
            self.config._render_budget_section(sans_droit), "",
            "le digest ne doit pas dire ce que la fiche refuse de montrer",
        )

    def test_un_responsable_archive_ne_recoit_rien(self):
        """Même règle, autre porte : un compte désactivé reste `user_id`."""
        parti = self.env["res.users"].create({
            "name": "Personne partie", "login": "parti_budget",
            "company_id": self.company.id, "company_ids": [(6, 0, [self.company.id])],
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("bf_budget.group_bf_budget_user").id,
            ])],
        })
        self._budget_en_alerte(user=parti)
        self.assertNotEqual(self.config._render_budget_section(parti), "")
        parti.active = False
        self.env.invalidate_all()
        self.assertEqual(self.config._render_budget_section(parti), "")

    def test_l_ecart_du_courriel_est_nomme_pour_ce_qu_il_est(self):
        """🔴 « Écart » tout court veut dire l'écart AU PLAN dans l'écran Budgets.

        La colonne du courriel porte l'écart au THÉORIQUE. Sous le même mot, le
        courriel aurait dit 1 900 $ là où la fiche affiche 0.
        """
        self._budget_en_alerte()
        html = self.config._render_budget_section(self.responsable)
        self.assertIn("Variance from theoretical", html)

    def test_un_budget_ferme_ou_brouillon_ne_parle_pas(self):
        """Seuls les budgets OUVERTS sont suivis au jour le jour."""
        budget = self._budget_en_alerte()
        self.assertNotEqual(self.config._render_budget_section(self.responsable), "")
        budget.action_close()
        self.env.invalidate_all()
        self.assertEqual(
            self.config._render_budget_section(self.responsable), "",
            "un budget clos n'a plus de geste quotidien à provoquer",
        )

    def test_un_depassement_assume_fait_taire_la_ligne(self):
        """Même règle que l'alerte : assumé, avec son motif, donc silencieux."""
        budget = self._budget_en_alerte()
        budget.line_ids.write({
            "overrun_accepted": True,
            "overrun_reason": "Dépense décidée, suivie ailleurs.",
        })
        self.env.invalidate_all()
        self.assertEqual(self.config._render_budget_section(self.responsable), "")

    def test_les_montants_sont_dans_le_tableau(self):
        self._budget_en_alerte(planned=1200.0, spent=12000.0)
        html = self.config._render_budget_section(self.responsable)
        for entete in ("Planned", "Committed", "Theoretical", "Variance"):
            self.assertIn(entete, html)
        # L'engagé et le prévu, formatés dans la langue du destinataire.
        self.assertIn("12", html)
        self.assertIn("%", html)

    def test_le_nombre_de_lignes_non_montrees_est_dit(self):
        """Au-delà de dix lignes, le reste est compté, jamais tronqué en silence."""
        budget = self.env["bf.budget"].create({
            "name": "Budget à douze postes",
            "company_id": self.company.id,
            "budget_type": "expense",
            "date_start": self.date_start,
            "date_end": self.date_end,
        })
        for index in range(12):
            compte = self.env["account.account"].with_company(self.company).create({
                "name": "Charge %s" % index, "code": "6031%02d" % index,
                "account_type": "expense",
            })
            position = self.env["bf.budget.position"].create({
                "name": "Poste %s" % index, "code": "P%02d" % index,
                "budget_type": "expense", "company_id": self.company.id,
                "account_ids": [(6, 0, compte.ids)],
            })
            self.env["bf.budget.line"].create({
                "budget_id": budget.id, "position_id": position.id,
                "amount_planned": 100.0,
            })
            self._post_bill(compte, 5000.0)
        budget.user_id = self.responsable
        budget.action_open()
        self.env.invalidate_all()
        self.assertEqual(len(budget.line_ids.filtered("is_alert")), 12)
        html = self.config._render_budget_section(self.responsable)
        self.assertIn("more flagged line(s) not shown", html)
        self.assertIn("2 more", html)

    def test_le_bloc_injecte_porte_sa_propre_ligne(self):
        """⚠️ Le marqueur « Divider » est ENTRE deux <tr>. Une section nue y
        serait sortie de la table par l'analyseur HTML et flotterait au-dessus
        de la carte du courriel. Elle doit porter son propre <tr>."""
        enveloppe = "<table><tr><td>haut</td></tr><!-- Divider --><tr><td>bas</td></tr></table>"
        section = "<h3>Budget</h3>"
        sortie = self.config._splice_budget_section(enveloppe, section)
        index = sortie.index(section)
        # 🔴 Regarder JUSTE avant et JUSTE après. Une première version lisait
        # 120 caractères de part et d'autre : elle voyait le <tr><td> de
        # l'enveloppe et rendait vert même sans ligne autour de la section.
        self.assertRegex(sortie[:index], r"<tr><td[^>]*>$")
        self.assertTrue(sortie[index + len(section):].startswith("</td></tr>"))

    def test_sans_marqueur_le_courriel_n_est_pas_touche(self):
        intact = "<table><tr><td>rien à épisser</td></tr></table>"
        self.assertEqual(
            self.config._splice_budget_section(intact, "<h3>Budget</h3>"), intact
        )
