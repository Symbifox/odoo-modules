"""Tests des règles d'assemblée.

L'arithmétique des majorités est la raison d'être du module : elle est testée
seuil par seuil, y compris les pièges qui ont déjà mordu ici.

1. Les dénominateurs diffèrent : les art. 1096 et 1097 se mesurent sur
   l'assistance, l'art. 1098 sur l'ensemble du syndicat.
2. L'art. 1097 n'exige plus de majorité en nombre depuis le 10 janvier 2020
   (Loi 16, 2019, c. 28, a. 53) ; l'art. 1098, lui, garde la sienne.
3. La privation du droit de vote et la réduction des voix retranchent du total
   du syndicat (art. 1099), l'absence non.
4. À une assemblée de reprise, une décision de l'art. 1097 porte une condition
   supplémentaire (art. 1089 al. 2).
5. La fraction que le syndicat a lui-même acquise ne vote pas et sort du total
   (art. 1076), et ce retranchement se lit au registre : il ne dépend pas de ce
   qu'une feuille de présence a été chargée.
"""
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestGovernance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.syndicat"].create(
            {"name": "Syndicat AG", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble AG", "syndicat_id": cls.syndicat.id}
        )
        # Quatre fractions, quotes-parts volontairement inégales.
        cls.units = cls.env["bf.property.unit"]
        cls.owners = cls.env["res.partner"]
        for i, qp in enumerate([400.0, 300.0, 200.0, 100.0], start=1):
            unit = cls.env["bf.property.unit"].create(
                {"name": "10%d" % i, "building_id": cls.building.id, "quote_part": qp}
            )
            owner = cls.env["res.partner"].create(
                {"name": "Coproprietaire %d" % i, "email": "c%d@example.invalid" % i}
            )
            cls.env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": owner.id}
            )
            cls.units |= unit
            cls.owners |= owner

    def _assembly(self, **kw):
        vals = {
            "name": "AG d'essai",
            "syndicat_id": self.syndicat.id,
            "date": datetime.now() + timedelta(days=20),
            "convocation_date": (datetime.now()).date(),
        }
        vals.update(kw)
        assembly = self.env["bf.property.assembly"].create(vals)
        assembly.action_load_attendance()
        return assembly

    def _set_present(self, assembly, owners):
        for line in assembly.attendance_ids:
            line.status = "present" if line.partner_id in owners else "absent"

    def _fifth_fraction(self, quote_part=0.0, owner=None):
        """Fait passer le syndicat à cinq fractions.

        Sous cinq fractions, l'art. 1091 plafonne le copropriétaire
        majoritaire : une cinquième fraction est le seul moyen d'éprouver une
        autre règle sur un gros porteur sans que ce plafond s'en mêle.
        """
        unit = self.env["bf.property.unit"].create(
            {
                "name": "105",
                "building_id": self.building.id,
                "quote_part": quote_part,
            }
        )
        self.env["bf.property.ownership"].create(
            {"unit_id": unit.id, "partner_id": (owner or self.owners[0]).id}
        )
        return unit

    def _split_first_unit(self, *shares):
        """Met la première fraction en indivision. Rend les indivisaires."""
        unit = self.units[0]
        unit.ownership_ids.share = shares[0]
        partners = self.owners[0]
        for index, share in enumerate(shares[1:], start=2):
            partner = self.env["res.partner"].create(
                {
                    "name": "Indivisaire %d" % index,
                    "email": "ind%d@example.invalid" % index,
                }
            )
            self.env["bf.property.ownership"].create(
                {"unit_id": unit.id, "partner_id": partner.id, "share": share}
            )
            partners |= partner
        return partners

    def _syndicat_acquires(self, unit, share=100.0):
        """Le syndicat acquiert une fraction, ou une part d'une fraction.

        Art. 1076 C.c.Q. Une acquisition totale chasse le titulaire précédent :
        le registre ne porte qu'un seul jeu de titulaires courants.
        """
        if not self.syndicat.partner_id:
            self.syndicat.partner_id = self.env["res.partner"].create(
                {
                    "name": "Syndicat AG — personne morale",
                    "email": "syndicat@example.invalid",
                }
            )
        if share >= 100.0:
            unit.ownership_ids.filtered("is_current").unlink()
        self.env["bf.property.ownership"].create(
            {
                "unit_id": unit.id,
                "partner_id": self.syndicat.partner_id.id,
                "share": share,
            }
        )
        return self.syndicat.partner_id

    def _line(self, assembly, partner, unit=None):
        return assembly.attendance_ids.filtered(
            lambda a: a.partner_id == partner and (not unit or a.unit_id == unit)
        )

    def _resolution(self, assembly, majority_type, for_owners):
        res = self.env["bf.property.resolution"].create(
            {
                "name": "Résolution",
                "assembly_id": assembly.id,
                "majority_type": majority_type,
            }
        )
        res.action_load_ballot()
        for vote in res.vote_ids:
            vote.choice = "for" if vote.partner_id in for_owners else "against"
        return res

    # ── Feuille de présence et voix ──

    def test_attendance_loads_current_owners(self):
        assembly = self._assembly()
        self.assertEqual(len(assembly.attendance_ids), 4)
        self.assertEqual(assembly.total_votes, 1000.0)
        self.assertEqual(assembly.total_owners, 4)

    def test_votes_follow_quote_part(self):
        assembly = self._assembly()
        line = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[0]
        )
        line.status = "present"
        self.assertEqual(line.base_votes, 400.0)
        self.assertEqual(line.votes, 400.0)

    def test_absent_member_carries_no_votes(self):
        assembly = self._assembly()
        line = assembly.attendance_ids[0]
        line.status = "absent"
        self.assertEqual(line.votes, 0.0)

    def test_deprived_member_carries_no_votes(self):
        """Art. 1094 C.c.Q., saisi à la main."""
        assembly = self._assembly()
        line = assembly.attendance_ids[0]
        line.status = "present"
        line.voting_deprived = True
        self.assertEqual(line.votes, 0.0)

    def test_indivision_splits_the_votes(self):
        second = self.env["res.partner"].create(
            {"name": "Indivisaire", "email": "ind@example.invalid"}
        )
        unit = self.units[0]
        unit.ownership_ids.share = 60.0
        self.env["bf.property.ownership"].create(
            {"unit_id": unit.id, "partner_id": second.id, "share": 40.0}
        )
        assembly = self._assembly()
        lines = assembly.attendance_ids.filtered(lambda a: a.unit_id == unit)
        self.assertEqual(len(lines), 2)
        self.assertAlmostEqual(sum(lines.mapped("base_votes")), 400.0, places=4)

    # ── Quorum ──

    def test_quorum_requires_majority_of_all_votes(self):
        """Art. 1089 al. 1."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0])  # 400 sur 1000
        self.assertEqual(assembly.quorum_required, 500.0)
        self.assertFalse(assembly.quorum_reached)
        self._set_present(assembly, self.owners[0] | self.owners[1])  # 700
        self.assertTrue(assembly.quorum_reached)

    def test_quorum_exactly_half_is_not_enough(self):
        """« la majorité des voix » veut dire plus de la moitié, pas la moitié."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners[1] | self.owners[2])  # 300+200 = 500
        self.assertEqual(assembly.votes_present, 500.0)
        self.assertFalse(assembly.quorum_reached)

    def test_reconvened_assembly_uses_those_present(self):
        """Art. 1089 al. 2."""
        assembly = self._assembly(is_reconvened=True)
        self._set_present(assembly, self.owners[3])  # 100 sur 1000
        self.assertTrue(assembly.quorum_reached)
        self.assertIn("1089", assembly.quorum_rule)

    # ── Art. 1099 : ce qui se retranche du total des voix ──

    def test_deprivation_reduces_the_total_votes(self):
        """Art. 1099 : les voix retirées sortent aussi du dénominateur."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        self.assertEqual(assembly.total_votes, 1000.0)
        line = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[0]
        )
        line.voting_deprived = True
        self.assertEqual(line.withheld_votes, 400.0)
        self.assertEqual(assembly.total_votes, 600.0)
        self.assertEqual(assembly.quorum_required, 300.0)
        # Le nombre de copropriétaires, lui, ne bouge pas : privé de vote, il
        # reste copropriétaire pour les trois quarts en nombre de l'art. 1098.
        self.assertEqual(assembly.total_owners, 4)

    def test_deprivation_lets_the_quorum_be_reached(self):
        """Le cas concret : sans le retranchement, le quorum se dit manquant.

        400 voix sur 1000 privées du droit de vote, deux copropriétaires
        présents totalisant 500 voix. La loi demande plus de 300 voix, pas plus
        de 500 : l'assemblée siège.
        """
        assembly = self._assembly()
        self._set_present(assembly, self.owners[1] | self.owners[2])  # 300 + 200
        line = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[0]
        )
        line.voting_deprived = True
        self.assertEqual(assembly.votes_present, 500.0)
        self.assertEqual(assembly.quorum_required, 300.0)
        self.assertTrue(assembly.quorum_reached)

    def test_absence_does_not_reduce_the_total_votes(self):
        """L'absent garde ses voix, il ne les exprime simplement pas."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0])
        absentee = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[1]
        )
        self.assertEqual(absentee.status, "absent")
        self.assertEqual(absentee.withheld_votes, 0.0)
        self.assertEqual(assembly.total_votes, 1000.0)
        self.assertEqual(assembly.quorum_required, 500.0)

    def test_vote_reduction_withholds_only_the_reduced_share(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        line = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[0]
        )
        line.vote_reduction = 150.0
        self.assertEqual(line.votes, 250.0)
        self.assertEqual(line.withheld_votes, 150.0)
        self.assertEqual(assembly.total_votes, 850.0)
        self.assertEqual(assembly.votes_present, 850.0)

    def test_reduction_never_exceeds_the_share_held(self):
        """Une réduction saisie trop grande ne peut pas creuser le total."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        line = assembly.attendance_ids.filtered(
            lambda a: a.partner_id == self.owners[0]
        )
        line.vote_reduction = 900.0
        self.assertEqual(line.votes, 0.0)
        self.assertEqual(line.withheld_votes, 400.0)
        self.assertEqual(assembly.total_votes, 600.0)

    # ── Majorités ──

    def test_1096_simple_majority(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)  # 1000 présentes
        res = self._resolution(assembly, "art_1096", self.owners[0] | self.owners[1])
        self.assertEqual(res.votes_for, 700.0)
        self.assertEqual(res.result, "adopted")

    def test_1096_abstentions_stay_in_the_denominator(self):
        """Une abstention n'est pas neutre : elle reste au dénominateur."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        res = self._resolution(assembly, "art_1096", self.owners[0])  # 400 pour
        for vote in res.vote_ids.filtered(lambda v: v.partner_id != self.owners[0]):
            vote.choice = "abstain"
        self.assertEqual(res.votes_for, 400.0)
        self.assertEqual(res.votes_abstain, 600.0)
        self.assertEqual(res.result, "rejected")

    def test_multi_fraction_owner_counts_once_in_headcounts(self):
        """Le cas normal en copropriété : appartement + stationnement + rangement.

        Défaut trouvé en revue : les têtes se comptaient par ligne de présence,
        donc une même personne pesait autant de fois qu'elle détenait de
        fractions dans la majorité en nombre de l'art. 1098.
        """
        parking = self.env["bf.property.unit"].create(
            {
                "name": "P-1",
                "building_id": self.building.id,
                "unit_type": "parking",
                "quote_part": 0.0,
            }
        )
        self.env["bf.property.ownership"].create(
            {"unit_id": parking.id, "partner_id": self.owners[0].id}
        )
        assembly = self._assembly()
        self.assertEqual(assembly.total_owners, 4)
        self._set_present(assembly, self.owners)
        # Cinq lignes de présence, mais quatre copropriétaires.
        self.assertEqual(len(assembly.attendance_ids), 5)
        self.assertEqual(assembly.owners_present, 4)
        res = self._resolution(assembly, "art_1098", self.owners[0])
        self.assertEqual(res.owners_for, 1)

    def test_1097_is_three_quarters_of_the_votes_present(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)  # 1000 voix présentes
        # 700 voix restent sous le seuil de 750.
        res = self._resolution(assembly, "art_1097", self.owners[0] | self.owners[1])
        self.assertEqual(res.votes_for, 700.0)
        self.assertEqual(res.result, "rejected")
        # 900 voix : au-delà des trois quarts.
        res2 = self._resolution(
            assembly, "art_1097", self.owners[0] | self.owners[1] | self.owners[2]
        )
        self.assertEqual(res2.votes_for, 900.0)
        self.assertEqual(res2.result, "adopted")

    def test_1097_no_longer_requires_a_headcount_majority(self):
        """La Loi 16 a retiré la condition en nombre le 10 janvier 2020.

        2019, c. 28, a. 53 : « à la majorité » est devenu « par », et « de tous
        les copropriétaires » est devenu « des copropriétaires, présents ou
        représentés ». Il ne reste que les trois quarts des voix de
        l'assistance. Le copropriétaire majoritaire seul en faveur emporte donc
        la décision, et une version antérieure de ce module la rejetait —
        exactement l'« erreur dans le calcul des voix » que l'art. 1103 ouvre
        à l'annulation pendant 90 jours.
        """
        self.units[0].quote_part = 800.0
        self.units[1].quote_part = 100.0
        self.units[2].quote_part = 50.0
        self.units[3].quote_part = 50.0
        self._fifth_fraction()  # hors de portée de l'art. 1091
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        res = self._resolution(assembly, "art_1097", self.owners[0])
        self.assertEqual(res.votes_for, 800.0)  # au-delà des 750 requises
        self.assertEqual(res.owners_for, 1)  # 1 sur 4, sans effet ici
        self.assertEqual(res.result, "adopted")

    def test_1098_still_requires_a_headcount_majority(self):
        """L'art. 1098, lui, n'a pas été touché : les têtes comptent toujours."""
        self.units[0].quote_part = 900.0
        self.units[1].quote_part = 40.0
        self.units[2].quote_part = 30.0
        self.units[3].quote_part = 30.0
        self._fifth_fraction()  # hors de portée de l'art. 1091
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        res = self._resolution(assembly, "art_1098", self.owners[0])
        self.assertEqual(res.votes_for, 900.0)  # exactement les 90 % exigés
        self.assertEqual(res.owners_for, 1)  # mais 1 sur 4, il en faut 3
        self.assertEqual(res.result, "rejected")

    def test_1098_measures_against_the_whole_syndicat(self):
        """Le piège : dénominateurs sur tous les copropriétaires, présents ou non."""
        assembly = self._assembly()
        # Trois présents sur quatre, unanimes : 900 voix sur 1000.
        self._set_present(
            assembly, self.owners[0] | self.owners[1] | self.owners[2]
        )
        res = self._resolution(
            assembly, "art_1098", self.owners[0] | self.owners[1] | self.owners[2]
        )
        self.assertEqual(res.votes_for, 900.0)
        self.assertEqual(res.owners_for, 3)
        # 3 sur 4 atteint exactement les trois quarts, et 900 sur 1000 atteint
        # exactement les 90 %. Les deux seuils se lisent « au moins », donc
        # l'égalité suffit et la résolution passe.
        self.assertEqual(res.result, "adopted")

    def test_1098_rejected_when_absentees_sink_the_ratio(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0] | self.owners[1])
        res = self._resolution(assembly, "art_1098", self.owners[0] | self.owners[1])
        self.assertEqual(res.votes_for, 700.0)  # 70 % de tous les votes
        self.assertEqual(res.result, "rejected")

    def test_reconvened_assembly_blocks_1097_below_half_of_all_votes(self):
        """Art. 1089 al. 2, deuxième phrase."""
        assembly = self._assembly(is_reconvened=True)
        self._set_present(assembly, self.owners[3])  # 100 sur 1000
        res = self._resolution(assembly, "art_1097", self.owners[3])
        self.assertEqual(res.result, "rejected")
        self.assertIn("1089", res.result_detail)

    # ── Art. 1090 al. 2 : le mandat présumé entre indivisaires ──

    def test_absent_indivisaire_mandates_the_others(self):
        """L'indivisaire absent est présumé avoir mandaté les autres."""
        holder, absentee = self._split_first_unit(60.0, 40.0)
        assembly = self._assembly()
        self._set_present(assembly, holder)
        line = self._line(assembly, holder, self.units[0])
        self.assertEqual(line.indivision_votes, 160.0)
        self.assertEqual(line.votes, 400.0)  # 240 + 160
        self.assertEqual(self._line(assembly, absentee).votes, 0.0)
        # Rien n'est retranché : la voix est exercée, par un autre.
        self.assertEqual(assembly.total_votes, 1000.0)

    def test_mandate_splits_between_several_holders(self):
        """« proportionnellement aux droits des autres indivisaires »."""
        partners = self._split_first_unit(50.0, 30.0, 20.0)
        assembly = self._assembly()
        self._set_present(assembly, partners[0] | partners[1])
        # Les 80 voix de l'absent se partagent 50/30 entre les deux présents.
        self.assertAlmostEqual(
            self._line(assembly, partners[0], self.units[0]).votes, 250.0, places=4
        )
        self.assertAlmostEqual(
            self._line(assembly, partners[1], self.units[0]).votes, 150.0, places=4
        )

    def test_expressed_refusal_blocks_the_presumed_mandate(self):
        holder, absentee = self._split_first_unit(60.0, 40.0)
        assembly = self._assembly()
        self._set_present(assembly, holder)
        self._line(assembly, absentee).mandate_refused = True
        line = self._line(assembly, holder, self.units[0])
        self.assertEqual(line.indivision_votes, 0.0)
        self.assertEqual(line.votes, 240.0)
        # Un refus n'est pas une réduction : le total du syndicat ne bouge pas.
        self.assertEqual(assembly.total_votes, 1000.0)

    def test_indivisaire_represented_by_a_third_party_keeps_the_votes(self):
        """Le mandat écrit à un tiers se lit « Représenté », pas « Absent »."""
        holder, other = self._split_first_unit(60.0, 40.0)
        third = self.env["res.partner"].create(
            {"name": "Mandataire", "email": "mand@example.invalid"}
        )
        assembly = self._assembly()
        self._set_present(assembly, holder)
        other_line = self._line(assembly, other)
        other_line.write({"status": "represented", "proxy_partner_id": third.id})
        self.assertEqual(other_line.votes, 160.0)
        self.assertEqual(
            self._line(assembly, holder, self.units[0]).indivision_votes, 0.0
        )

    def test_deprived_indivisaire_transfers_nothing(self):
        """On ne mandate pas plus de voix qu'on n'en a (art. 1094 puis 1090)."""
        holder, absentee = self._split_first_unit(60.0, 40.0)
        assembly = self._assembly()
        self._set_present(assembly, holder)
        self._line(assembly, absentee).voting_deprived = True
        self.assertEqual(
            self._line(assembly, holder, self.units[0]).indivision_votes, 0.0
        )
        self.assertEqual(assembly.total_votes, 840.0)

    # ── Art. 1076 : la fraction que le syndicat a acquise ──

    def test_1076_the_syndicat_fraction_carries_no_votes(self):
        """« Il ne dispose d'aucune voix pour ces parties » (art. 1076)."""
        syndicat_partner = self._syndicat_acquires(self.units[3])
        assembly = self._assembly()
        line = self._line(assembly, syndicat_partner)
        line.status = "present"
        self.assertTrue(line.syndicat_held)
        self.assertEqual(line.base_votes, 100.0)
        self.assertEqual(line.votes, 0.0)

    def test_1076_reduces_the_total_votes(self):
        """« Le total des voix qui peuvent être exprimées est réduit d'autant »."""
        self._syndicat_acquires(self.units[3])
        assembly = self._assembly()
        self.assertEqual(assembly.syndicat_held_votes, 100.0)
        self.assertEqual(assembly.total_votes, 900.0)

    def test_1076_reduces_the_total_without_any_attendance_line(self):
        """Le retranchement se lit au REGISTRE, pas à la feuille de présence.

        C'est ce qui sépare l'art. 1076 de la privation de l'art. 1094 : celle-ci
        naît d'un fait porté à la main sur une ligne, celui-là de la propriété
        de la fraction. Une feuille de présence jamais chargée, ou dont on a
        retiré la ligne du syndicat, ne doit pas rendre ses voix au total.
        """
        syndicat_partner = self._syndicat_acquires(self.units[3])
        assembly = self._assembly()
        self._line(assembly, syndicat_partner).unlink()
        self.assertEqual(len(assembly.attendance_ids), 3)
        self.assertEqual(assembly.total_votes, 900.0)

    def test_1076_lets_the_quorum_be_reached(self):
        """Sans le retranchement, le quorum se dirait manquant à tort."""
        self._syndicat_acquires(self.units[0])
        assembly = self._assembly()
        self._set_present(assembly, self.owners[1] | self.owners[3])
        # 300 + 100 voix présentes, sur un total ramené de 1000 à 600.
        self.assertEqual(assembly.total_votes, 600.0)
        self.assertEqual(assembly.votes_present, 400.0)
        self.assertEqual(assembly.quorum_required, 300.0)
        self.assertTrue(assembly.quorum_reached)

    def test_1076_leaves_the_headcount_alone(self):
        """⚠️ Voulu, et non tranché : le syndicat reste AU NOMBRE.

        L'art. 1076 ne parle que des voix. Il ne dit pas si le syndicat,
        propriétaire d'une fraction, entre dans les trois quarts EN NOMBRE de
        l'art. 1098. Tant que ce n'est pas tranché (P2.3), il reste au
        dénominateur : le seuil en est durci, ce qui ne fait pas adopter une
        résolution qui ne devrait pas l'être. Ce test garde le choix pour que
        personne ne le « corrige » sans l'avoir tranché.
        """
        self._syndicat_acquires(self.units[3])
        assembly = self._assembly()
        self.assertEqual(assembly.total_owners, 4)
        self.assertEqual(assembly.total_votes, 900.0)

    def test_1076_in_indivision_leaves_the_other_share_alone(self):
        """Le syndicat indivisaire : sa part ne vote pas, celle du tiers oui."""
        self._split_first_unit(60.0)
        self._syndicat_acquires(self.units[0], share=40.0)
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0])
        self.assertEqual(
            self._line(assembly, self.owners[0], self.units[0]).votes, 240.0
        )
        self.assertEqual(assembly.syndicat_held_votes, 160.0)
        self.assertEqual(assembly.total_votes, 840.0)

    def test_1076_syndicat_receives_no_presumed_mandate(self):
        """Le mandat de l'art. 1090 al. 2 ne passe pas par le syndicat.

        L'admettre comme mandataire lui rendrait à l'assemblée la voix que
        l'art. 1076 lui refuse. Les voix de l'indivisaire absent vont donc
        entièrement à l'autre indivisaire présent, et non au prorata d'une part
        qui ne peut rien exprimer.
        """
        indivisaires = self._split_first_unit(30.0, 30.0)
        syndicat_partner = self._syndicat_acquires(self.units[0], share=40.0)
        absentee, holder = indivisaires[0], indivisaires[1]
        assembly = self._assembly()
        self._set_present(assembly, holder | syndicat_partner)
        holder_line = self._line(assembly, holder, self.units[0])
        # 120 voix en propre, plus les 120 de l'absent : la totalité, pas
        # 120 × 30 / 70.
        self.assertEqual(holder_line.indivision_votes, 120.0)
        self.assertEqual(holder_line.votes, 240.0)
        self.assertEqual(self._line(assembly, syndicat_partner).votes, 0.0)
        self.assertEqual(
            self._line(assembly, syndicat_partner).indivision_votes, 0.0
        )
        self.assertEqual(self._line(assembly, absentee, self.units[0]).votes, 0.0)

    def test_1076_syndicat_transfers_nothing_when_absent(self):
        """Une part sans voix n'a rien à mandater."""
        self._split_first_unit(60.0)
        syndicat_partner = self._syndicat_acquires(self.units[0], share=40.0)
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0])
        self.assertEqual(self._line(assembly, syndicat_partner).status, "absent")
        self.assertEqual(
            self._line(assembly, self.owners[0], self.units[0]).indivision_votes,
            0.0,
        )
        self.assertEqual(assembly.total_votes, 840.0)

    def test_1076_issues_no_ballot_to_the_syndicat(self):
        """Le bulletin est l'instrument d'une voix ; il n'y en a pas ici.

        ⚠️ Traitement volontairement distinct de celui du copropriétaire privé
        de vote (art. 1094), qui reçoit, lui, un bulletin sans poids : celui-là
        reste un copropriétaire présent dont le module garde la trace, le
        syndicat ne dispose « d'aucune voix pour ces parties ». À porter à P2.3
        avec la question du nombre.
        """
        syndicat_partner = self._syndicat_acquires(self.units[3])
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0] | syndicat_partner)
        resolution = self._resolution(assembly, "art_1096", self.owners[0])
        self.assertNotIn(
            syndicat_partner, resolution.vote_ids.mapped("partner_id")
        )
        self.assertEqual(len(resolution.vote_ids), 1)
        self.assertEqual(resolution.result, "adopted")

    def test_1076_shrinks_the_base_of_the_1091_cap(self):
        """« Le total des voix qui peuvent être exprimées » commande aussi 1091.

        Le plafond de l'art. 1091 joue contre celui qui détient plus de la
        moitié de l'ensemble des voix. Retrancher les voix du syndicat abaisse
        cette moitié, et fait mordre un plafond qui ne mordait pas.
        """
        self._syndicat_acquires(self.units[1])
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0] | self.owners[2])
        # Ensemble des voix ramené à 700 : le porteur de 400 dépasse la moitié
        # (350) alors qu'il ne dépassait pas 500. Ses voix tombent à la somme
        # de celles des autres présents, soit 200.
        self.assertEqual(assembly.syndicat_held_votes, 300.0)
        line = self._line(assembly, self.owners[0])
        self.assertEqual(line.votes, 200.0)
        self.assertIn("1091", line.cap_rule)
        # Art. 1099 : les 200 voix retirées par le plafond sortent du total à
        # leur tour, après les 300 de l'art. 1076.
        self.assertEqual(line.withheld_votes, 200.0)
        self.assertEqual(assembly.total_votes, 500.0)

    def test_1076_stops_when_the_syndicat_has_resold(self):
        """L'art. 1076 vaut tant que le syndicat détient, pas après.

        « Le syndicat peut […] acquérir ou aliéner des fractions. » Une fraction
        revendue rend ses voix au total : le retranchement suit le registre
        courant, pas l'historique.
        """
        syndicat_partner = self._syndicat_acquires(self.units[3])
        held = self.units[3].ownership_ids.filtered(
            lambda o: o.partner_id == syndicat_partner
        )
        today = fields.Date.context_today(held)
        held.write(
            {
                "date_start": fields.Date.subtract(today, years=1),
                "date_end": fields.Date.subtract(today, days=1),
            }
        )
        buyer = self.env["res.partner"].create(
            {"name": "Acquereur", "email": "acq@example.invalid"}
        )
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[3].id, "partner_id": buyer.id}
        )
        assembly = self._assembly()
        self.assertEqual(assembly.syndicat_held_votes, 0.0)
        self.assertEqual(assembly.total_votes, 1000.0)
        self.assertEqual(self._line(assembly, buyer).base_votes, 100.0)

    def test_1076_needs_the_syndicat_to_have_a_partner(self):
        """Sans fiche de personne morale, rien ne distingue le syndicat.

        Le module ne devine pas : il ne retranche que ce que le registre lui
        dit, et le registre ne le dit qu'à travers cette fiche.
        """
        stranger = self.env["res.partner"].create(
            {"name": "Tiers acquéreur", "email": "tiers@example.invalid"}
        )
        self.units[3].ownership_ids.filtered("is_current").unlink()
        self.env["bf.property.ownership"].create(
            {"unit_id": self.units[3].id, "partner_id": stranger.id}
        )
        assembly = self._assembly()
        self.assertEqual(assembly.syndicat_held_votes, 0.0)
        self.assertEqual(assembly.total_votes, 1000.0)
        self.assertFalse(self._line(assembly, stranger).syndicat_held)

    # ── Art. 1091 : le plafond des petites copropriétés ──

    def test_1091_caps_the_majority_owner(self):
        """Moins de cinq fractions, plus de la moitié des voix."""
        self.units[0].quote_part = 700.0
        self.units[1].quote_part = 100.0
        self.units[2].quote_part = 100.0
        self.units[3].quote_part = 100.0
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0] | self.owners[1])
        line = self._line(assembly, self.owners[0])
        # Réduites à la somme des voix des autres présents, soit 100.
        self.assertEqual(line.votes, 100.0)
        self.assertEqual(line.cap_reduction, 600.0)
        self.assertIn("1091", line.cap_rule)
        # Art. 1099 : le total suit la réduction, donc le quorum aussi.
        self.assertEqual(assembly.total_votes, 400.0)
        self.assertEqual(assembly.votes_present, 200.0)
        # Conséquence voulue par l'art. 1091 : ramené à la somme des autres
        # présents, le majoritaire ne pèse jamais que la moitié de ce qui
        # reste. À lui seul avec un autre copropriétaire, le quorum de la
        # majorité des voix n'est pas atteint — il faut du monde en face.
        self.assertFalse(assembly.quorum_reached)

    def test_1091_quorum_is_reached_when_more_owners_attend(self):
        self.units[0].quote_part = 700.0
        self.units[1].quote_part = 100.0
        self.units[2].quote_part = 100.0
        self.units[3].quote_part = 100.0
        assembly = self._assembly()
        self._set_present(
            assembly, self.owners[0] | self.owners[1] | self.owners[2]
        )
        line = self._line(assembly, self.owners[0])
        self.assertEqual(line.votes, 200.0)  # somme des deux autres présents
        self.assertEqual(assembly.total_votes, 500.0)  # 1000 - 500 retranchées
        self.assertEqual(assembly.votes_present, 400.0)
        self.assertTrue(assembly.quorum_reached)

    def test_1091_does_not_apply_from_five_fractions(self):
        self.units[0].quote_part = 700.0
        self.units[1].quote_part = 100.0
        self.units[2].quote_part = 100.0
        self.units[3].quote_part = 100.0
        self._fifth_fraction(owner=self.owners[3])
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0] | self.owners[1])
        line = self._line(assembly, self.owners[0])
        self.assertEqual(line.votes, 700.0)
        self.assertEqual(line.cap_reduction, 0.0)

    def test_1091_leaves_a_minority_holder_alone(self):
        """400 sur 1000 : gros porteur, mais pas plus de la moitié."""
        assembly = self._assembly()
        self._set_present(assembly, self.owners[0] | self.owners[3])
        line = self._line(assembly, self.owners[0])
        self.assertEqual(line.votes, 400.0)
        self.assertFalse(line.cap_rule)

    # ── Art. 1092 : le plafond du promoteur ──

    def _promoter_setup(self, age):
        """Cinq fractions, promoteur à 700 voix sur 1000, dont 50 qu'il occupe.

        `age` est l'ancienneté de l'inscription de la déclaration au jour de
        l'assemblée, qui se tient vingt jours après aujourd'hui.
        """
        self.units[0].quote_part = 650.0
        self.units[1].quote_part = 100.0
        self.units[2].quote_part = 100.0
        self.units[3].quote_part = 100.0
        occupied = self._fifth_fraction(quote_part=50.0, owner=self.owners[0])
        self.syndicat.write(
            {
                "declaration_date": fields.Date.today() - age,
                "promoter_partner_id": self.owners[0].id,
                "promoter_unit_id": occupied.id,
            }
        )
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        return assembly

    def test_1092_caps_the_promoter_at_sixty_percent(self):
        assembly = self._promoter_setup(relativedelta(years=2, months=6))
        lines = self._line(assembly, self.owners[0])
        # 60 % des 1000 voix, outre les 50 voix de la fraction occupée.
        self.assertAlmostEqual(sum(lines.mapped("votes")), 650.0, places=4)
        self.assertAlmostEqual(sum(lines.mapped("cap_reduction")), 50.0, places=4)
        self.assertIn("1092", lines[0].cap_rule)
        self.assertAlmostEqual(assembly.total_votes, 950.0, places=4)

    def test_1092_falls_to_a_quarter_after_the_third_year(self):
        assembly = self._promoter_setup(relativedelta(years=5))
        lines = self._line(assembly, self.owners[0])
        self.assertAlmostEqual(sum(lines.mapped("votes")), 300.0, places=4)
        self.assertAlmostEqual(assembly.total_votes, 600.0, places=4)

    def test_1092_does_not_bite_before_the_second_year(self):
        assembly = self._promoter_setup(relativedelta(months=6))
        lines = self._line(assembly, self.owners[0])
        self.assertAlmostEqual(sum(lines.mapped("votes")), 700.0, places=4)
        self.assertAlmostEqual(assembly.total_votes, 1000.0, places=4)

    # ── Art. 1102.1 : transmission du procès-verbal ──

    def test_minutes_deadline_is_thirty_days(self):
        assembly = self._assembly()
        self.assertEqual(
            assembly.minutes_deadline, assembly.date.date() + timedelta(days=30)
        )
        self.assertEqual(assembly.minutes_state, "pending")

    def test_minutes_sent_within_the_deadline(self):
        assembly = self._assembly()
        assembly.minutes_sent_date = assembly.minutes_deadline
        self.assertEqual(assembly.minutes_state, "sent")

    def test_minutes_sent_after_the_deadline(self):
        assembly = self._assembly()
        assembly.minutes_sent_date = assembly.minutes_deadline + timedelta(days=1)
        self.assertEqual(assembly.minutes_state, "sent_late")

    def test_minutes_overdue_once_the_deadline_has_passed(self):
        assembly = self._assembly(
            date=datetime.now() - timedelta(days=40),
            convocation_date=(datetime.now() - timedelta(days=60)).date(),
        )
        self.assertEqual(assembly.minutes_state, "overdue")

    def test_cron_flips_a_lapsed_minutes_state(self):
        """L'état bascule au passage d'une date, pas à une écriture."""
        assembly = self._assembly(
            date=datetime.now() - timedelta(days=40),
            convocation_date=(datetime.now() - timedelta(days=60)).date(),
        )
        # On remet la valeur d'hier en base pour éprouver le cron lui-même.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE bf_property_assembly SET minutes_state = 'pending' WHERE id = %s",
            (assembly.id,),
        )
        self.env.invalidate_all()
        self.assertEqual(assembly.minutes_state, "pending")
        self.assertEqual(self.env["bf.property.assembly"]._cron_refresh_minutes_state(), 1)
        self.assertEqual(assembly.minutes_state, "overdue")

    # ── Ce que l'utilisateur lit ──

    def test_the_fifth_case_of_1097_is_shown(self):
        """2020, c. 5, a. 198 : l'art. 1097 compte cinq cas, pas quatre."""
        help_text = self.env["bf.property.resolution"]._fields["majority_type"].help
        self.assertIn("5°", help_text)
        self.assertIn("1070", help_text)

    # ── Convocation ──

    def test_notice_too_short_is_flagged(self):
        assembly = self._assembly(
            date=datetime.now() + timedelta(days=5),
            convocation_date=datetime.now().date(),
        )
        self.assertEqual(assembly.convocation_state, "late")

    def test_notice_too_long_is_flagged(self):
        assembly = self._assembly(
            date=datetime.now() + timedelta(days=60),
            convocation_date=datetime.now().date(),
        )
        self.assertEqual(assembly.convocation_state, "early")

    def test_notice_within_the_window_is_ok(self):
        assembly = self._assembly()
        self.assertEqual(assembly.convocation_state, "ok")
        self.assertEqual(
            assembly.agenda_request_deadline,
            assembly.convocation_date + timedelta(days=5),
        )

    # ── Garde-fous ──

    def test_manual_override_requires_a_reason(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        res = self._resolution(assembly, "art_1096", self.owners[0])
        with self.assertRaises(ValidationError):
            res.result_override = "adopted"

    def test_manual_override_wins_when_motivated(self):
        assembly = self._assembly()
        self._set_present(assembly, self.owners)
        res = self._resolution(assembly, "art_1096", self.owners[0])
        self.assertEqual(res.result, "rejected")
        res.write(
            {"result_override": "adopted", "override_reason": "Erreur de décompte au PV"}
        )
        self.assertEqual(res.result, "adopted")

    def test_vote_from_another_assembly_rejected(self):
        first = self._assembly()
        second = self._assembly(name="Autre AG")
        res = self.env["bf.property.resolution"].create(
            {"name": "R", "assembly_id": first.id}
        )
        with self.assertRaises(ValidationError):
            self.env["bf.property.vote"].create(
                {
                    "resolution_id": res.id,
                    "attendance_id": second.attendance_ids[0].id,
                }
            )
