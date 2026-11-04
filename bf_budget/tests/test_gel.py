"""Le gel du plan, complet : chaque porte fermée s'éprouve par un chemin qui
n'emprunte QU'ELLE.

🔴 La leçon du lot précédent : les gardes se couvrent l'une l'autre. Un essai
qui ne regarde que « ça refuse » reste vert avec une garde en moins, parce
qu'une voisine lève à sa place. Chaque essai ci-dessous passe donc par le seul
chemin que sa garde protège : le `create` nu, le `copy()` d'une ligne, son
`unlink`, le déplacement d'une ligne de brouillon vers un budget ouvert (la
source est libre, seule la DESTINATION refuse), la période créée, supprimée,
redatée ou reparentée, et le `state` glissé dans le même `write`.
"""
from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestGelDuPlan(BfBudgetCommon):

    def _ouvert(self):
        return self._make_budget(planned=1200.0, state="open")

    # ------------------------------------------------------------------
    # Les lignes
    # ------------------------------------------------------------------
    def test_creer_une_ligne_sur_un_budget_ouvert_refuse(self):
        """⚠️ Le MESSAGE est l'assertion : sans la garde du `create`, la
        création échoue quand même — sur la garde des PÉRIODES, plus loin,
        après l'insertion de la ligne. Un essai qui ne regarde que « ça
        refuse » reste vert avec la garde en moins, rattrapé par une voisine."""
        budget = self._ouvert()
        with self.assertRaises(UserError) as capture:
            self.env["bf.budget.line"].create({
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "amount_planned": 500.0,
            })
        self.assertIn("no line can be added", str(capture.exception))

    def test_dupliquer_une_ligne_d_un_budget_ouvert_refuse(self):
        """« Dupliquer » sur la fiche d'une ligne : un clic, et le prévu
        doublait sans un mot. La copie vise le même budget : c'est le `create`
        qui la refuse, et il faut que `copy()` l'emprunte vraiment. Même
        assertion de message que le `create` nu, même raison."""
        budget = self._ouvert()
        with self.assertRaises(UserError) as capture:
            budget.line_ids.copy()
        self.assertIn("no line can be added", str(capture.exception))

    def test_supprimer_une_ligne_d_un_budget_ouvert_refuse(self):
        budget = self._ouvert()
        with self.assertRaises(UserError):
            budget.line_ids.unlink()

    def test_deplacer_une_ligne_de_brouillon_vers_un_ouvert_refuse(self):
        """La ligne SOURCE est libre (brouillon) : seul l'état du budget de
        DESTINATION peut refuser ce geste-là."""
        ouvert = self._ouvert()
        brouillon = self._make_budget(planned=300.0, state="draft")
        with self.assertRaises(UserError):
            brouillon.line_ids.write({"budget_id": ouvert.id})

    def test_une_ligne_d_un_brouillon_se_cree_se_copie_se_supprime(self):
        """Le gel ne doit rien geler au brouillon : c'est là qu'on travaille."""
        budget = self._make_budget(planned=300.0, state="draft")
        copie = budget.line_ids.copy()
        self.assertEqual(copie.budget_id, budget)
        copie.unlink()
        self.assertEqual(len(budget.line_ids), 1)

    # ------------------------------------------------------------------
    # Les périodes
    # ------------------------------------------------------------------
    def test_creer_une_periode_sur_un_budget_ouvert_refuse(self):
        budget = self._ouvert()
        line = budget.line_ids
        with self.assertRaises(UserError):
            self.env["bf.budget.line.period"].create({
                "line_id": line.id,
                "date_start": self.date_start,
                "date_end": self.date_start + relativedelta(days=27),
                "amount_planned": 100.0,
            })

    def test_supprimer_une_periode_d_un_budget_ouvert_refuse(self):
        budget = self._ouvert()
        with self.assertRaises(UserError):
            budget.line_ids.period_ids[0].unlink()

    def test_redater_une_periode_d_un_budget_ouvert_refuse(self):
        """Les DATES seules, jamais le montant : c'est le champ d'à côté qui
        réécrivait le théorique."""
        budget = self._ouvert()
        periode = budget.line_ids.period_ids.sorted("date_start")[0]
        with self.assertRaises(UserError):
            periode.write({
                "date_start": periode.date_start + relativedelta(months=1),
                "date_end": periode.date_end + relativedelta(months=1),
            })

    def test_reparenter_une_periode_vers_un_budget_ouvert_refuse(self):
        """La période SOURCE est libre (brouillon) : seule la ligne de
        DESTINATION peut refuser."""
        ouvert = self._ouvert()
        brouillon = self._make_budget(planned=300.0, state="draft")
        periode = brouillon.line_ids.period_ids.sorted("date_start")[0]
        with self.assertRaises(UserError):
            periode.write({"line_id": ouvert.line_ids.id})

    def test_les_defauts_du_contexte_ne_sautent_pas_la_garde_des_periodes(self):
        """🔴 Une garde qui lit `vals_list` ne voit rien : l'ORM n'y a
        pas encore injecté les défauts du contexte. Par
        `with_context(default_line_id=…)`, une période naissait sur un budget
        OUVERT sans refus — plan et théorique réécrits sans trace."""
        budget = self._ouvert()
        line = budget.line_ids
        with self.assertRaises(UserError) as capture:
            self.env["bf.budget.line.period"].with_context(
                default_line_id=line.id
            ).create({
                "date_start": self.date_start,
                "date_end": self.date_start + relativedelta(days=27),
                "amount_planned": 5000.0,
            })
        self.assertIn("can no longer be edited", str(capture.exception))
        self.assertEqual(len(line.period_ids), 12, "rien ne doit avoir bougé")

    def test_les_defauts_du_contexte_ne_sautent_pas_la_garde_des_lignes(self):
        """Même chemin, modèle ligne : le refus doit venir de LA garde des
        lignes (son message), pas d'un ricochet sur celle des périodes."""
        budget = self._ouvert()
        with self.assertRaises(UserError) as capture:
            self.env["bf.budget.line"].with_context(
                default_budget_id=budget.id
            ).create({"position_id": self.position_software.id})
        self.assertIn("no line can be added", str(capture.exception))
        self.assertEqual(len(budget.line_ids), 1, "rien ne doit avoir bougé")

    # ------------------------------------------------------------------
    # Le budget
    # ------------------------------------------------------------------
    def test_glisser_state_dans_le_write_n_exempte_plus(self):
        """🔴 L'ancienne garde testait la PRÉSENCE de la clé `state` : y glisser
        l'état courant réécrivait le plan d'un budget ouvert sans trace.

        ⚠️ Le chemin qui n'emprunte QUE cette garde-ci est celui des DATES :
        effacer `line_ids` serait rattrapé par la garde d'`unlink` des lignes
        (un essai par `line_ids` reste vert sans la garde du budget), alors
        que les dates du budget ne sont gardées nulle part ailleurs."""
        budget = self._ouvert()
        with self.assertRaises(UserError):
            budget.write({
                "state": "open",
                "date_end": self.date_end + relativedelta(months=1),
            })
        self.assertEqual(budget.date_end, self.date_end, "rien ne doit avoir bougé")
        with self.assertRaises(UserError):
            budget.write({"state": "open", "line_ids": [(5, 0, 0)]})
        self.assertEqual(len(budget.line_ids), 1, "rien ne doit avoir bougé")

    def test_remettre_au_brouillon_et_corriger_dans_le_meme_write_passe(self):
        """Le seul écrit combiné légitime : l'équivalent des deux gestes permis
        l'un après l'autre."""
        budget = self._ouvert()
        budget.write({
            "state": "draft",
            "date_end": self.date_end + relativedelta(years=1),
        })
        self.assertEqual(budget.state, "draft")

    def test_l_exemption_draft_ne_depasse_pas_les_transitions_permises(self):
        """🔴 Un budget CLOS se redatait par
        `{"state": "draft", …}`, et l'original d'une révision ressuscitait
        pendant que sa révision vit — là où `action_reset_draft` refuse ces
        deux départs. L'exemption s'arrête où l'action s'arrête."""
        clos = self._ouvert()
        clos.action_close()
        with self.assertRaises(UserError):
            clos.write({
                "state": "draft",
                "date_end": self.date_end + relativedelta(years=1),
            })
        revise = self._ouvert()
        revise.action_create_revision()
        self.assertEqual(revise.state, "revised")
        with self.assertRaises(UserError):
            revise.write({
                "state": "draft",
                "date_end": self.date_end + relativedelta(years=1),
            })

    def test_la_revision_passe_les_gardes(self):
        """La révision COPIE lignes et périodes sous un budget neuf au
        brouillon : les gardes de `create` ne doivent pas la casser."""
        budget = self._ouvert()
        action = budget.action_create_revision()
        revision = self.env["bf.budget"].browse(action["res_id"])
        self.assertEqual(revision.state, "draft")
        self.assertEqual(len(revision.line_ids), 1)
        self.assertEqual(
            len(revision.line_ids.period_ids), len(budget.line_ids.period_ids)
        )

    # ------------------------------------------------------------------
    # Le redatage d'un brouillon
    # ------------------------------------------------------------------
    def test_redater_un_brouillon_transporte_la_ventilation(self):
        """Une révision redatée vers l'exercice suivant garde sa saisonnalité :
        la dépense de mai reste au cinquième mois, sous les NOUVELLES dates."""
        budget = self._make_budget(planned=0.0, state="draft")
        line = budget.line_ids
        periodes = line.period_ids.sorted("date_start")
        periodes[4].amount_planned = 1200.0  # tout le montant en mai
        budget.write({
            "date_start": self.date_start + relativedelta(years=1),
            "date_end": self.date_end + relativedelta(years=1),
        })
        nouvelles = line.period_ids.sorted("date_start")
        self.assertEqual(len(nouvelles), 12)
        self.assertEqual(
            nouvelles[0].date_start, self.date_start + relativedelta(years=1)
        )
        self.assertEqual(nouvelles[4].amount_planned, 1200.0)
        self.assertEqual(sum(nouvelles.mapped("amount_planned")), 1200.0)
        self.assertEqual(line.amount_planned, 1200.0)

    def test_redater_vers_un_autre_nombre_de_mois_repartit_egalement(self):
        """Aucune correspondance honnête entre 12 mois et 6 : le total se
        répartit également, comme le ferait sa saisie neuve."""
        budget = self._make_budget(planned=1200.0, state="draft")
        line = budget.line_ids
        budget.write({
            "date_end": self.date_start + relativedelta(months=6, days=-1),
        })
        nouvelles = line.period_ids.sorted("date_start")
        self.assertEqual(len(nouvelles), 6)
        self.assertEqual(sum(nouvelles.mapped("amount_planned")), 1200.0)
        self.assertEqual(nouvelles[0].amount_planned, 200.0)

    def test_le_relissage_laisse_une_note_au_fil(self):
        """Un relissage silencieux est un plan qui ment avec un total juste :
        quand le nombre de mois change, le budget le dit à son fil. Le
        transport par position, lui, ne dit rien — il n'a rien perdu."""
        budget = self._make_budget(planned=1200.0, state="draft")
        avant = len(budget.message_ids)
        budget.write({
            "date_end": self.date_start + relativedelta(months=6, days=-1),
        })
        notes = budget.message_ids[: len(budget.message_ids) - avant]
        self.assertTrue(
            any("spread evenly again" in (m.body or "") for m in notes),
            "le relissage doit se dire au fil",
        )
        # Le transport (même nombre de mois) ne laisse pas de note.
        autre = self._make_budget(planned=1200.0, state="draft")
        avant = len(autre.message_ids)
        autre.write({
            "date_start": self.date_start + relativedelta(years=1),
            "date_end": self.date_end + relativedelta(years=1),
        })
        self.assertEqual(len(autre.message_ids), avant,
                         "le transport n'a rien perdu, rien à dire")

    def test_redater_et_ouvrir_dans_le_meme_write_refuse(self):
        """🔴 Ce write combiné laissait un budget OUVERT daté d'un
        exercice dont la grille mensuelle portait encore l'ancien — le rebase
        se filtrait sur l'état d'APRÈS. Les deux gestes, dans l'ordre."""
        budget = self._make_budget(planned=1200.0, state="draft")
        line = budget.line_ids
        line.period_ids.sorted("date_start")[4].amount_planned = 1200.0
        line.period_ids.sorted("date_start")[0].amount_planned = 0.0
        with self.assertRaises(UserError) as capture:
            budget.write({
                "state": "open",
                "date_start": self.date_start + relativedelta(years=1),
                "date_end": self.date_end + relativedelta(years=1),
            })
        self.assertIn("redate the draft first", str(capture.exception))
        # L'ordre sain, lui, passe et transporte : redater, puis ouvrir.
        budget.write({
            "date_start": self.date_start + relativedelta(years=1),
            "date_end": self.date_end + relativedelta(years=1),
        })
        budget.write({"state": "open"})
        nouvelles = line.period_ids.sorted("date_start")
        self.assertEqual(budget.state, "open")
        self.assertEqual(
            nouvelles[0].date_start, self.date_start + relativedelta(years=1)
        )
        self.assertEqual(nouvelles[4].amount_planned, 1200.0)

    def test_resocieter_le_budget_avec_des_lignes_est_refuse(self):
        """Le TROISIÈME sens du poste étranger : re-societer le
        BUDGET brouillon laissait ses lignes au poste de l'ancienne société —
        le recalcul du `company_id` lié ne rejoue pas la contrainte des
        lignes."""
        autre = self.env["res.company"].create({"name": "Société d'arrivée"})
        budget = self._make_budget(planned=300.0, state="draft")
        with self.assertRaises(ValidationError):
            budget.write({"company_id": autre.id})

    def test_reecrire_les_memes_dates_ne_touche_pas_les_periodes(self):
        """Une édition de masse qui réécrit les mêmes dates ne doit pas
        régénérer la ventilation : la grille colle déjà."""
        budget = self._make_budget(planned=1200.0, state="draft")
        avant = budget.line_ids.period_ids.ids
        budget.write({"date_start": self.date_start, "date_end": self.date_end})
        self.assertEqual(budget.line_ids.period_ids.ids, avant)

    # ------------------------------------------------------------------
    # La société du poste
    # ------------------------------------------------------------------
    def test_un_poste_d_une_autre_societe_est_refuse(self):
        """Un poste étranger lit zéro pour toujours : le domaine du champ ne
        protège que l'écran, pas un import ni un RPC."""
        autre = self.env["res.company"].create({"name": "Autre société"})
        compte = self.env["account.account"].with_company(autre).create({
            "code": "609999",
            "name": "Charges d'ailleurs",
            "account_type": "expense",
        })
        poste_etranger = self.env["bf.budget.position"].create({
            "name": "Poste étranger",
            "code": "ETRG",
            "budget_type": "expense",
            "company_id": autre.id,
            "account_ids": [(6, 0, compte.ids)],
        })
        budget = self._make_budget(planned=300.0, state="draft")
        with self.assertRaises(ValidationError):
            budget.line_ids.write({"position_id": poste_etranger.id})

    def test_reparenter_le_poste_vers_une_autre_societe_est_refuse(self):
        """Le chemin INVERSE : changer la société du POSTE ne
        rejouait pas la contrainte des lignes, et des lignes étrangères
        restaient vivantes, à zéro pour toujours."""
        autre = self.env["res.company"].create({"name": "Société du poste"})
        compte = self.env["account.account"].with_company(autre).create({
            "code": "609998",
            "name": "Charges partagées",
            "account_type": "expense",
        })
        self._make_budget(planned=300.0, state="draft")  # porte position_software
        with self.assertRaises(ValidationError):
            self.position_software.write({
                "company_id": autre.id,
                "account_ids": [(6, 0, compte.ids)],
            })
