"""Une révision garde la ventilation de son budget source.

🔴 Le défaut que ces essais ferment a été trouvé en PRODUCTION le 2026-10-31,
en traitant la première vraie alerte d'un budget vivant. « Créer une
révision » recopiait bien les périodes, puis les écrasait : `amount_planned`
est un calculé stocké et modifiable, donc copié lui aussi, et son inverse
rappelait `_spread_evenly` à la création de la copie. Une dépense ponctuelle
prévue sur un seul mois se retrouvait étalée sur douze dans la révision, et la
ligne passait en alerte sans que rien n'ait changé dans les livres.

Aucun des 182 essais de la famille ne couvrait la révision ligne à ligne : ils
vérifiaient l'état des budgets, le numéro de révision et le total, qui, lui,
était juste. C'est la ventilation qui changeait.
"""
from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import BfBudgetCommon


@tagged("post_install", "-at_install")
class TestRevisionBreakdown(BfBudgetCommon):

    def _lump_sum_line(self, budget, position, amount, month):
        """Une ligne dont TOUT le montant tombe sur un seul mois."""
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": position.id,
                "amount_planned": amount,
            }
        )
        for period in line.period_ids:
            period.amount_planned = amount if period.date_start.month == month else 0.0
        self.assertEqual(line.amount_planned, amount)
        return line

    def _breakdown(self, line):
        return [
            (period.date_start, period.date_end, period.amount_planned)
            for period in line.period_ids.sorted("date_start")
        ]

    def test_le_total_ne_se_copie_pas(self):
        """La garde qui tient tout le reste.

        Remettre `copy=True` (ou retirer l'attribut : c'est le défaut d'Odoo
        pour un calculé stocké non readonly) ramène le défaut en entier.
        """
        self.assertFalse(
            self.env["bf.budget.line"]._fields["amount_planned"].copy,
            "le total doit se déduire des périodes copiées, jamais se copier",
        )
        self.assertTrue(self.env["bf.budget.line"]._fields["period_ids"].copy)

    def test_revision_garde_la_ventilation(self):
        budget = self.env["bf.budget"].create(
            {
                "name": "Exercice à dépense ponctuelle",
                "company_id": self.company.id,
                "budget_type": "expense",
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        lump = self._lump_sum_line(budget, self.position_software, 12000.0, month=5)
        spread = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_telecom.id,
                "amount_planned": 1200.0,
            }
        )
        avant = {lump.position_id.id: self._breakdown(lump),
                 spread.position_id.id: self._breakdown(spread)}
        budget.action_open()

        budget.action_create_revision()
        revision = budget.revision_ids
        self.assertEqual(len(revision), 1)
        self.assertEqual(len(revision.line_ids), 2)
        for line in revision.line_ids:
            self.assertEqual(
                self._breakdown(line), avant[line.position_id.id],
                "la révision a changé la ventilation de %s" % line.name,
            )
        self.assertEqual(
            sum(revision.line_ids.mapped("amount_planned")), 13200.0,
            "le total doit se recalculer des périodes, à l'identique",
        )

    def test_ligne_sans_periode_garde_son_total(self):
        """Le filet de `copy_data` : sans période, le total est tout ce qu'il y a.

        Une ligne n'a pas de période quand son budget n'a pas de dates, ce que
        le modèle n'autorise plus, mais qu'une donnée ancienne peut porter.
        """
        budget = self._make_budget(planned=900.0)
        line = budget.line_ids
        line.period_ids.unlink()
        self.assertFalse(line.period_ids)
        copie = line.copy({"budget_id": budget.id})
        self.assertEqual(copie.amount_planned, 900.0)

    @freeze_time("2026-11-02")
    def test_la_revision_n_invente_pas_d_alerte(self):
        """Le cas réel, de bout en bout : dépense ponctuelle passée, aucune alerte.

        Sans le correctif, la révision lisse 12 000 $ sur douze mois : au 2
        novembre, le théorique tombe à ~10 067 $ pour un engagé de 12 000 $,
        soit 19 % de dérive — au-dessus des deux seuils (10 % ET 250 $). La
        ligne crie au dépassement dans la révision alors que le mois de mai
        s'est passé exactement comme prévu.
        """
        budget = self.env["bf.budget"].create(
            {
                "name": "Exercice 2026",
                "company_id": self.company.id,
                "budget_type": "expense",
                "date_start": fields.Date.to_date("2026-01-01"),
                "date_end": fields.Date.to_date("2026-12-31"),
            }
        )
        line = self._lump_sum_line(budget, self.position_software, 12000.0, month=5)
        self._post_bill(self.account_software, 12000.0, date=fields.Date.to_date("2026-05-15"))
        budget.action_open()
        self.assertEqual(line.amount_actual, 12000.0)
        self.assertEqual(line.amount_theoretical, 12000.0)
        self.assertFalse(line.is_alert, "le budget source est tenu, pas d'alerte")

        budget.action_create_revision()
        revision = budget.revision_ids
        revision.action_open()
        copiee = revision.line_ids
        self.assertEqual(copiee.amount_planned, 12000.0)
        self.assertEqual(
            copiee.amount_theoretical, 12000.0,
            "le théorique de la révision doit suivre la MÊME ventilation",
        )
        self.assertFalse(
            copiee.is_alert,
            "réviser un budget tenu ne doit pas inventer un dépassement",
        )

    def test_le_duplicata_ordinaire_garde_aussi_la_ventilation(self):
        """Pas seulement la révision : « Dupliquer » passe par le même chemin."""
        budget = self.env["bf.budget"].create(
            {
                "name": "À dupliquer",
                "company_id": self.company.id,
                "budget_type": "expense",
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line = self._lump_sum_line(budget, self.position_software, 6000.0, month=2)
        copie = budget.copy()
        self.assertEqual(self._breakdown(copie.line_ids), self._breakdown(line))
        self.assertEqual(copie.line_ids.amount_planned, 6000.0)

    def test_un_budget_ouvert_refuse_qu_on_retape_son_total(self):
        """🔴 La porte par laquelle le défaut revenait, même après `copy=False`.

        Le gel du plan ne tenait que sur `bf.budget.write`. L'écran autonome de
        la ligne, celui qu'on ouvre justement depuis une alerte, laissait
        retaper `amount_planned` : le MÊME total suffisait, l'inverse se
        redéclenchait et relissait la ventilation.
        """
        budget = self.env["bf.budget"].create({
            "name": "Exercice gelé",
            "company_id": self.company.id,
            "budget_type": "expense",
            "date_start": self.date_start,
            "date_end": self.date_end,
        })
        line = self._lump_sum_line(budget, self.position_software, 12000.0, month=5)
        attendu = self._breakdown(line)
        budget.action_open()

        # 🔴 Les trois gardes se COUVRENT l'une l'autre : retirer celle de la
        # ligne laisse l'inverse écrire sur les périodes, où la garde de la
        # période lève à son tour. Un essai qui ne regarde que « ça refuse »
        # resterait vert avec deux gardes sur trois. Chacune est donc éprouvée
        # par un chemin qui n'emprunte qu'elle, et par son message.
        with self.assertRaises(UserError):
            line.amount_planned = 12000.0

        # a) la garde de la LIGNE, par un champ du plan qui ne touche aucune
        #    période.
        with self.assertRaisesRegex(UserError, "no longer a"):
            line.position_id = self.position_telecom

        # b) la garde de la MÉTHODE, reconnaissable à son message.
        with self.assertRaisesRegex(UserError, "spread on a draft"):
            line.action_spread_evenly()

        # c) la garde de la PÉRIODE, écrite directement.
        with self.assertRaisesRegex(UserError, "breakdown"):
            line.period_ids[0].amount_planned = 999.0

        self.env.invalidate_all()
        self.assertEqual(self._breakdown(line), attendu)
        self.assertEqual(line.position_id, self.position_software)

    def test_le_suivi_reste_ouvert_sur_un_budget_ouvert(self):
        """Geler le plan n'est pas geler la conversation autour du plan."""
        budget = self._make_budget(planned=1200.0, state="open")
        line = budget.line_ids
        line.write({"overrun_accepted": True, "overrun_reason": "Décidé, suivi ailleurs."})
        self.assertTrue(line.overrun_accepted)
        line.write({"alert_raised_date": False})

    def test_une_revision_ne_herite_pas_du_depassement_assume(self):
        """🔴 Une décision prise contre l'ANCIEN plan ne doit pas éteindre le NOUVEAU.

        Le module efface déjà la mémoire du franchissement à la copie, pour
        qu'un nouveau franchissement alerte de nouveau. Recopier le dépassement
        assumé revenait à faire le contraire : une révision à 1 200 $ prévus
        pour 12 000 $ engagés ne disait plus rien, ni au fil, ni à la tuile, ni
        au digest.
        """
        budget = self._make_budget(planned=1200.0, state="open")
        self._post_bill(self.account_software, 12000.0)
        budget.line_ids.write({
            "overrun_accepted": True, "overrun_reason": "Assumé sur l'ancien plan.",
        })
        self.env.invalidate_all()
        self.assertFalse(budget.line_ids.is_alert)

        budget.action_create_revision()
        revision = budget.revision_ids
        revision.action_open()
        self.env.invalidate_all()
        self.assertFalse(revision.line_ids.overrun_accepted)
        self.assertFalse(revision.line_ids.overrun_reason)
        self.assertTrue(
            revision.line_ids.is_alert,
            "la révision doit redire le dépassement, puisque son plan est neuf",
        )

    def test_saisir_un_total_repartit_toujours(self):
        """Le correctif ne doit pas casser la saisie : un total saisi se répartit.

        C'est la contrepartie de `copy=False` : l'inverse reste branché, et
        c'est lui qui rend la saisie d'un total confortable.
        """
        budget = self.env["bf.budget"].create(
            {
                "name": "Saisie",
                "company_id": self.company.id,
                "budget_type": "expense",
                "date_start": fields.Date.to_date("2026-01-01"),
                "date_end": fields.Date.to_date("2026-12-31"),
            }
        )
        line = self.env["bf.budget.line"].create(
            {
                "budget_id": budget.id,
                "position_id": self.position_software.id,
                "amount_planned": 1200.0,
            }
        )
        self.assertEqual(len(line.period_ids), 12)
        self.assertEqual(set(line.period_ids.mapped("amount_planned")), {100.0})
        line.amount_planned = 2400.0
        self.assertEqual(set(line.period_ids.mapped("amount_planned")), {200.0})

    def test_la_ventilation_tient_sur_une_deuxieme_revision(self):
        """La révision d'une révision : le défaut se serait répété à chaque passe."""
        budget = self.env["bf.budget"].create(
            {
                "name": "Chaîne",
                "company_id": self.company.id,
                "budget_type": "expense",
                "date_start": self.date_start,
                "date_end": self.date_end,
            }
        )
        line = self._lump_sum_line(budget, self.position_software, 3000.0, month=3)
        attendu = self._breakdown(line)
        budget.action_open()
        budget.action_create_revision()
        rev1 = budget.revision_ids
        rev1.action_open()
        rev1.action_create_revision()
        rev2 = rev1.revision_ids
        self.assertEqual(rev2.revision, 2)
        self.assertEqual(self._breakdown(rev2.line_ids), attendu)
