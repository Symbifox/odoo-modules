"""Ce que le portail du locataire laisse voir, et surtout ce qu'il refuse.

Six idées sont éprouvées ici, et chacune est un endroit où un portail locatif
ordinaire se tromperait :

1. 🔴 **L'appartenance se lit au BAIL, jamais à la fraction.** La personne
   inscrite comme occupante d'une fraction sans être partie au bail ne doit
   rien voir. C'est le sens inverse de l'erreur habituelle, et le plus grave.
2. **Le co-locataire voit le bail qu'il a signé**, même s'il n'est pas celui
   que le syndicat a porté au registre de l'art. 1070.
3. **Un bail sans fraction reste lisible** : la chambre et le terrain de maison
   mobile ne correspondent à aucune fraction inscrite, et c'est prévu.
4. 🔴 **Le fondement d'une résiliation de l'art. 1974.1 est hors de portée.**
   Violence sexuelle, violence conjugale, violence envers un enfant :
   le champ est réservé à la gestion. Le test le prouve par la LECTURE, pas par
   l'absence sur un écran.
5. 🔴 **La route du formulaire fait DEUX contrôles.** La règle d'accès dit si le
   bail est le mien ; elle ne dit pas que la pièce demandée appartient à ce
   bail. Un identifiant deviné ne doit rien servir.
6. **Le portail ne peut rien écrire.** Ni le bail, ni un avis, ni un versement.
"""
import re
from html import unescape

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import HttpCase, TransactionCase, tagged


def flat(html):
    """Le TEXTE que la page donne à lire, balises retirées.

    ⚠️ Un gabarit XML coupe ses phrases où l'indentation l'exige : « dans les
    dix\n jours », « aux conditions\n antérieures ». Chercher la phrase telle
    qu'on l'a écrite échoue sur une phrase qui EST là, et le test se remet à
    échouer chaque fois que quelqu'un ré-indente le gabarit.

    🔴 **Et les balises doivent tomber, sans quoi le test ment.** Mesuré :
    le gabarit écrivait « art. » devant un champ qui porte déjà
    « art. 1945 », donc la page affichait « (art. art. 1945) ». Le HTML, lui,
    dit `(art. <span>art. 1945</span>)` — les deux « art. » ne se touchent
    jamais dans le balisage. Un `assertNotIn("art. art.", html)` passait au
    VERT sur la faute même qu'il existait pour attraper, et la mutation qui la
    réintroduisait n'était pas rattrapée.

    Ce qui se vérifie est ce que le locataire LIT, pas ce que le serveur écrit.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html,
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text)


class RentalPortalCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.landlord = cls.env["bf.property.organisation"].create(
            {"name": "Immeubles Papineau", "kind": "landlord"}
        )
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat Papineau", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "4400 Papineau", "organisation_id": cls.landlord.id}
        )
        cls.syndicat_building = cls.env["bf.property.building"].create(
            {"name": "4400 Papineau (syndicat)",
             "organisation_id": cls.syndicat.id}
        )

        portal_group = cls.env.ref("base.group_portal")

        def _portal(name, login):
            partner = cls.env["res.partner"].create(
                {"name": name, "email": "%s@example.invalid" % login}
            )
            user = cls.env["res.users"].create(
                {
                    "name": name,
                    "login": login,
                    "password": "%s_pwd" % login,
                    "partner_id": partner.id,
                    "groups_id": [(6, 0, [portal_group.id])],
                }
            )
            return partner, user

        cls.tenant_partner, cls.tenant_user = _portal("Locataire Test", "rt_tenant")
        cls.cotenant_partner, cls.cotenant_user = _portal("Colocataire Test", "rt_cotenant")
        cls.neighbour_partner, cls.neighbour_user = _portal("Voisin", "rt_neighbour")
        # 🔴 Celui-là est le piège du module : inscrit comme OCCUPANT de la
        # fraction au registre de l'art. 1070, et partie à AUCUN bail.
        cls.registered_partner, cls.registered_user = _portal(
            "Occupant au registre", "rt_registered"
        )

        # La fraction du syndicat, et son occupant inscrit.
        cls.unit = cls.env["bf.property.unit"].create(
            {
                "name": "402",
                "building_id": cls.syndicat_building.id,
                "quote_part": 500.0,
                "is_rented": True,
                "occupant_id": cls.registered_partner.id,
            }
        )

        cls.lease = cls.env["bf.rental.lease"].create(
            {
                "name": "BAIL-PORTAIL-1",
                "organisation_id": cls.landlord.id,
                "building_id": cls.building.id,
                "unit_id": cls.unit.id,
                "tenant_ids": [(6, 0, [cls.tenant_partner.id,
                                       cls.cotenant_partner.id])],
                "duration_kind": "fixed",
                "date_start": "2026-07-01",
                "date_end": "2027-06-30",
                "rent": 1200.0,
            }
        )
        # Un bail de chambre : aucune fraction, et c'est licite.
        cls.room_lease = cls.env["bf.rental.lease"].create(
            {
                "name": "BAIL-CHAMBRE",
                "organisation_id": cls.landlord.id,
                "building_id": cls.building.id,
                "form_kind": "general",
                "is_room": True,
                "tenant_ids": [(6, 0, [cls.tenant_partner.id])],
                "duration_kind": "indeterminate",
                "date_start": "2026-07-01",
                "rent": 600.0,
            }
        )
        cls.other_lease = cls.env["bf.rental.lease"].create(
            {
                "name": "BAIL-DU-VOISIN",
                "organisation_id": cls.landlord.id,
                "building_id": cls.building.id,
                "tenant_ids": [(6, 0, [cls.neighbour_partner.id])],
                "duration_kind": "fixed",
                "date_start": "2026-07-01",
                "date_end": "2027-06-30",
                "rent": 950.0,
            }
        )


@tagged("post_install", "-at_install")
class TestRentalPortalScope(RentalPortalCommon):
    """Qui voit quel bail, et par quelle porte l'appartenance est lue."""

    def test_a_tenant_sees_only_their_own_lease(self):
        seen = self.env["bf.rental.lease"].with_user(self.tenant_user).search([])
        self.assertIn(self.lease, seen)
        self.assertNotIn(self.other_lease, seen)

    def test_a_co_tenant_sees_the_lease_they_signed(self):
        """⚠️ Passer par la fraction perdrait ce cas : le registre du syndicat
        ne porte qu'un occupant, et le deuxième signataire ne verrait rien."""
        seen = self.env["bf.rental.lease"].with_user(self.cotenant_user).search([])
        self.assertIn(self.lease, seen)

    def test_a_lease_without_a_unit_stays_readable(self):
        """La chambre ne correspond à aucune fraction, et c'est prévu."""
        self.assertFalse(self.room_lease.unit_id)
        seen = self.env["bf.rental.lease"].with_user(self.tenant_user).search([])
        self.assertIn(self.room_lease, seen)

    def test_the_occupant_registered_at_the_unit_sees_nothing(self):
        """🔴 Le cœur du module.

        Cette personne est portée au registre de l'art. 1070 comme occupante de
        la fraction. Elle n'est partie à aucun bail. Un portail qui résoudrait
        l'auditoire par `unit.occupant_id` — la porte du portail de la
        copropriété — lui servirait le bail d'autrui : un conjoint séparé resté
        au registre, un occupant inscrit à la hâte, un ancien locataire jamais
        retiré.
        """
        self.assertEqual(self.unit.occupant_id, self.registered_partner)
        seen = self.env["bf.rental.lease"].with_user(self.registered_user).search([])
        self.assertFalse(seen)
        with self.assertRaises(AccessError):
            self.lease.with_user(self.registered_user).read(["name"])

    def test_the_helper_reads_membership_at_the_lease(self):
        leases = self.env["bf.rental.lease"].sudo()._portal_leases_for(
            self.cotenant_partner
        )
        self.assertIn(self.lease, leases)
        self.assertNotIn(self.other_lease, leases)
        self.assertFalse(
            self.env["bf.rental.lease"].sudo()._portal_leases_for(
                self.registered_partner
            )
        )

    def test_an_ended_lease_stays_readable_by_its_tenant(self):
        """Ce qui se ferme, c'est le droit d'AGIR, pas celui de relire.

        ⚠️ Aucune date dans le domaine de la règle d'accès : `ormcache` la
        gèlerait. Un bail terminé reste donc lisible, et c'est voulu — l'ancien
        locataire peut en avoir besoin pour une démarche.
        """
        ended = self.env["bf.rental.lease"].create(
            {
                "name": "BAIL-ANCIEN",
                "organisation_id": self.landlord.id,
                "building_id": self.building.id,
                "tenant_ids": [(6, 0, [self.tenant_partner.id])],
                "duration_kind": "fixed",
                "date_start": "2019-07-01",
                "date_end": "2020-06-30",
                "rent": 800.0,
            }
        )
        seen = self.env["bf.rental.lease"].with_user(self.tenant_user).search([])
        self.assertIn(ended, seen)


@tagged("post_install", "-at_install")
class TestRentalPortalRefusals(RentalPortalCommon):
    """Ce que le portail refuse : d'écrire, et de laisser lire un fondement."""

    def test_the_portal_cannot_write_a_lease(self):
        with self.assertRaises(AccessError):
            self.lease.with_user(self.tenant_user).write({"rent": 1.0})

    def test_the_portal_cannot_create_a_lease(self):
        with self.assertRaises(AccessError):
            self.env["bf.rental.lease"].with_user(self.tenant_user).create(
                {
                    "name": "BAIL-INVENTE",
                    "organisation_id": self.landlord.id,
                    "building_id": self.building.id,
                    "tenant_ids": [(6, 0, [self.tenant_partner.id])],
                    "duration_kind": "indeterminate",
                    "date_start": "2026-07-01",
                    "rent": 100.0,
                }
            )

    def test_the_portal_cannot_write_a_payment(self):
        """Un locataire ne s'inscrit pas un versement. Le reçu de l'art. 1908
        se demande au locateur ; il ne se fabrique pas à l'écran."""
        term = self.env["bf.rental.term"].create(
            {"lease_id": self.lease.id, "date_due": "2026-08-01",
             "amount_due": 1200.0}
        )
        with self.assertRaises(AccessError):
            self.env["bf.rental.payment"].with_user(self.tenant_user).create(
                {"term_id": term.id, "date": "2026-08-01", "amount": 1200.0}
            )

    def test_the_1974_1_ground_is_out_of_reach(self):
        """🔴 Le fondement ne se lit pas depuis le portail.

        Violence conjugale, agression à caractère sexuel, aîné en perte
        d'autonomie. Le champ porte `groups=` : même si la résiliation
        redevenait lisible au portail, le fondement resterait absent du
        recordset, et c'est ce qui est éprouvé ici, sous l'identité du bureau.
        """
        notice = self.env["bf.rental.notice"].create(
            {
                "lease_id": self.lease.id,
                "kind": "tenant_resiliation",
                "date_given": "2026-08-01",
                "resiliation_ground": "violence",
                "attestation_received": True,
                "state": "given",
            }
        )
        reader = self.env["res.users"].create({
            "name": "Lecteur interne", "login": "lecteur_resiliation",
            "groups_id": [(6, 0, [self.env.ref("base.group_user").id,
                                  self.env.ref("bf_property_core.group_bf_property_user").id])],
        })
        fetched = notice.with_user(reader).read()[0]
        for restricted in ("resiliation_ground", "attestation_authority",
                           "is_sensitive_ground"):
            self.assertNotIn(restricted, fetched, restricted)

    def test_a_cotenant_does_not_see_the_resiliation(self):
        """🔴 Les colocataires partagent le bail, pas la résiliation.

        Une résiliation de l'art. 1974.1 est donnée par une victime, et le
        colocataire peut être l'agresseur. Ce qui lui sert n'est pas le motif
        mais « elle part, et quand » : la résiliation sort du portail, pour
        tous les locataires du bail, et le brouillon du bureau aussi.
        """
        resiliation = self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "tenant_resiliation",
             "date_given": "2026-08-01", "resiliation_ground": "violence",
             "attestation_received": True, "state": "given"}
        )
        draft = self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "modification",
             "date_given": "2026-08-01", "state": "draft"}
        )
        given = self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "modification",
             "date_given": "2026-08-01", "state": "given"}
        )
        seen = self.env["bf.rental.notice"].with_user(self.tenant_user).search([])
        self.assertIn(given, seen)
        self.assertNotIn(resiliation, seen)
        self.assertNotIn(draft, seen)
        for hidden in (resiliation, draft):
            with self.assertRaises(AccessError):
                hidden.with_user(self.tenant_user).read(["kind", "date_given"])

    def test_a_notice_on_someone_elses_lease_is_unreachable(self):
        notice = self.env["bf.rental.notice"].create(
            {
                "lease_id": self.other_lease.id,
                "kind": "modification",
                "date_given": "2026-08-01",
                "state": "given",
            }
        )
        seen = self.env["bf.rental.notice"].with_user(self.tenant_user).search([])
        self.assertNotIn(notice, seen)

    # ── 🔴 Le registre reste fermé ──

    REGISTER_MODELS = (
        "bf.property.organisation",
        "bf.property.building",
        "bf.property.unit",
        "bf.property.ownership",
        # ⚠️ Et les pièces jointes, pour la même raison : un droit donné « pour
        # afficher un nom de fichier » porte sur TOUTE la base, dont les pièces
        # des avis de résiliation et les dossiers d'autrui.
        "ir.attachment",
    )

    def test_the_module_opens_nothing_on_the_register(self):
        """🔴 Le défaut qui a fait sortir la page en 504, et sa vraie réparation.

        Le premier jet affichait `lease.organisation_id.name` au gabarit. Le
        groupe Portail n'a aucune ACL sur ces modèles, la page est tombée, et le
        réflexe est d'en ajouter trois. C'est le mauvais geste : ces modèles ne
        portent pas que des noms. `bf.property.organisation` porte
        `overdue_amount` et `self_insurance_balance` ; `bf.property.unit` porte
        `owner_ids`, `quote_part` et `overdue_days`. Une ACL donnée « pour
        afficher un nom » donne le modèle entier.

        ⚠️ Le test ne demande PAS si le portail lit ces modèles : sur une base
        où `bf_property_portal` est aussi installé, il les lit légitimement,
        pour une autre raison et sous d'autres règles. Ce qui est éprouvé, c'est
        ce que CE module déclare — le seul énoncé qui reste vrai quelle que soit
        la base.
        """
        data = self.env["ir.model.data"].search(
            [("module", "=", "bf_rental_portal"),
             ("model", "in", ("ir.model.access", "ir.rule"))]
        )
        opened = set()
        for entry in data:
            record = self.env[entry.model].browse(entry.res_id)
            if record.model_id.model in self.REGISTER_MODELS:
                opened.add("%s → %s" % (entry.complete_name, record.model_id.model))
        self.assertFalse(
            opened,
            "bf_rental_portal ouvre le registre : %s" % ", ".join(sorted(opened)),
        )

    def test_the_portal_has_no_grip_on_attachments(self):
        """🔴 La page liste le formulaire signé sans que le lecteur y ait droit.

        C'est le contrôleur qui construit la liste, en `sudo()`, après avoir
        cherché les baux sous les droits du lecteur. Si un jour quelqu'un donne
        `ir.attachment` au groupe Portail « pour simplifier le gabarit », ce
        test rougit — et c'est le seul avertissement qu'il y aura.
        """
        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(self.tenant_user).search([])

    # 🔴 **La liste BLANCHE des textes libres, pas une liste noire.**
    #
    # Un champ `Text` ou `Html` est libre par construction : personne ne peut
    # dire ce qu'il contiendra. Une sonde adversariale a trouvé
    # `note` sur le bail ET sur l'avis, tous deux lus en clair par le locataire
    # — le gestionnaire y avait écrit « mauvais payeur, ne pas renouveler ».
    #
    # ⚠️ Un contrôle qui NOMMERAIT `note` ne rattraperait pas le prochain. Le
    # test énumère donc ce que le portail lit vraiment et refuse tout texte
    # libre non inscrit ici. Y ajouter un nom est une décision, pas un oubli.
    PORTAL_FREE_TEXT_ALLOWED = frozenset()

    FREE_TEXT_TYPES = ("text", "html")

    def test_no_free_text_field_reaches_the_portal(self):
        models = {
            "bf.rental.lease": self.lease,
            "bf.rental.notice": self.env["bf.rental.notice"].create(
                {"lease_id": self.lease.id, "kind": "modification",
                 "date_given": "2026-08-01", "state": "given"}
            ),
            "bf.rental.term": self.env["bf.rental.term"].create(
                {"lease_id": self.lease.id, "date_due": "2026-08-01",
                 "amount_due": 1200.0}
            ),
        }
        found = []
        for name, record in models.items():
            mine = record.with_user(self.tenant_user)
            mine.invalidate_recordset()
            fields_ = self.env[name]._fields
            for field in mine.read()[0]:
                f = fields_.get(field)
                if (f is not None and f.type in self.FREE_TEXT_TYPES
                        and field not in self.PORTAL_FREE_TEXT_ALLOWED):
                    found.append("%s.%s" % (name, field))
        self.assertFalse(
            found,
            "texte libre lu par le portail : %s. Un champ dont on ne peut pas "
            "dire ce qu'il contient ne s'ouvre pas à quelqu'un d'autre que "
            "celui qui l'écrit." % ", ".join(sorted(found)),
        )

    def test_the_management_note_is_out_of_reach(self):
        """🔴 Le cas nommé, en plus de l'énumération.

        L'énumération ci-dessus attrape le PROCHAIN champ ; celui-ci tient
        celui qu'on a trouvé, avec son contenu, pour que l'échec dise ce qui
        fuit et pas seulement qu'il fuit.
        """
        self.lease.sudo().note = "Mauvais payeur, ne pas renouveler."
        with self.assertRaises(AccessError):
            self.lease.with_user(self.tenant_user).read(["note"])
        notice = self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "modification",
             "date_given": "2026-08-01", "state": "given",
             "note": "Appeler l'intervenante avant de reloger."}
        )
        with self.assertRaises(AccessError):
            notice.with_user(self.tenant_user).read(["note"])

    def test_the_notice_chatter_stays_internal(self):
        """`bf.rental.notice` hérite `mail.thread`. Le geste qu'un gestionnaire
        fera est d'écrire une note au fil ; elle ne doit pas atterrir sur
        l'écran du locataire."""
        notice = self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "modification",
             "date_given": "2026-08-01", "state": "given"}
        )
        notice.message_post(body="Dossier délicat, ne pas relancer.")
        mine = notice.with_user(self.tenant_user)
        mine.invalidate_recordset()
        bodies = " ".join(m.body or "" for m in mine.message_ids)
        self.assertNotIn("ne pas relancer", bodies)

    def test_what_the_lease_says_of_itself_is_stored(self):
        """⚠️ `store=True` n'est pas une optimisation, c'est la garde.

        Un related NON stocké lit à travers, à l'affichage, avec les droits du
        LECTEUR : la page retomberait sur la même erreur, et seulement pour les
        baux qui portent une fraction — donc pas sur le bail de chambre, donc
        pas forcément dans le premier essai venu.
        """
        fields_ = self.env["bf.rental.lease"]._fields
        for name in ("portal_landlord_name", "portal_building_name",
                     "portal_unit_name"):
            self.assertTrue(fields_[name].store, name)
            self.assertTrue(fields_[name].readonly, name)

    def test_the_tenant_reads_those_three_on_their_own_lease(self):
        mine = self.lease.with_user(self.tenant_user)
        self.assertEqual(mine.portal_landlord_name, "Immeubles Papineau")
        self.assertEqual(mine.portal_building_name, "4400 Papineau")
        self.assertEqual(mine.portal_unit_name, "402")
        # Le bail de chambre ne correspond à aucune fraction, et le champ est
        # vide sans que rien ne casse.
        self.assertFalse(
            self.room_lease.with_user(self.tenant_user).portal_unit_name
        )

    def test_a_term_on_someone_elses_lease_is_unreachable(self):
        term = self.env["bf.rental.term"].create(
            {"lease_id": self.other_lease.id, "date_due": "2026-08-01",
             "amount_due": 950.0}
        )
        seen = self.env["bf.rental.term"].with_user(self.tenant_user).search([])
        self.assertNotIn(term, seen)


@tagged("post_install", "-at_install")
class TestRentalPortalSelections(RentalPortalCommon):
    """Les deux sélections qui dépendent du JOUR, prises de face.

    ⚠️ Elles ne peuvent pas vivre dans une `ir.rule` — `ormcache` gèlerait la
    date — ni au contrôleur, où elles ne s'éprouveraient qu'en cherchant des
    chaînes dans du HTML. Elles vivent au modèle, et voici ce qu'elles tiennent.
    """

    def _notice(self, received, **kw):
        vals = {
            "lease_id": self.lease.id,
            "kind": "modification",
            "date_given": received,
            "date_received": received,
            "target_date": received + relativedelta(months=4),
            "state": "given",
        }
        vals.update(kw)
        return self.env["bf.rental.notice"].create(vals)

    def test_a_notice_still_within_its_month_is_pending(self):
        today = fields.Date.context_today(self.env.user)
        notice = self._notice(today)
        self.assertEqual(notice.response_deadline, today + relativedelta(months=1))
        self.assertIn(notice, notice._portal_pending(notice))

    def test_a_notice_whose_month_has_run_out_is_not(self):
        """🔴 Le compteur qui ne se vide jamais cesse d'être lu.

        Sans la borne, l'écran dirait « vous avez jusqu'au 3 mars » un
        12 septembre. Et le jour où il porte quelque chose de vrai, personne ne
        le regarde plus.
        """
        today = fields.Date.context_today(self.env.user)
        stale = self._notice(today - relativedelta(months=2))
        self.assertLess(stale.response_deadline, today)
        self.assertNotIn(stale, stale._portal_pending(stale))

    def test_a_notice_on_its_last_day_is_still_pending(self):
        """⚠️ La borne est inclusive : le dernier jour utile en est un."""
        today = fields.Date.context_today(self.env.user)
        last = self._notice(today - relativedelta(months=1))
        self.assertEqual(last.response_deadline, today)
        self.assertIn(last, last._portal_pending(last))

    def test_an_answered_notice_is_no_longer_pending(self):
        today = fields.Date.context_today(self.env.user)
        answered = self._notice(today, state="accepted")
        self.assertNotIn(answered, answered._portal_pending(answered))

    def test_a_notice_without_a_reception_date_shows_no_deadline(self):
        """Le mois court de la RÉCEPTION. Sans elle, rien à compter — et une
        échéance calculée sur une date supposée est pire qu'une échéance
        absente."""
        today = fields.Date.context_today(self.env.user)
        unknown = self._notice(today, date_received=False)
        self.assertFalse(unknown.response_deadline)
        self.assertNotIn(unknown, unknown._portal_pending(unknown))

    # ── Ce qui reste dû ──

    def _term(self, due, amount, **kw):
        vals = {"lease_id": self.lease.id, "date_due": due,
                "amount_due": amount}
        vals.update(kw)
        return self.env["bf.rental.term"].create(vals)

    def test_the_total_carries_only_what_is_already_due(self):
        """🔴 Aucun total du bail (art. 1905).

        Personne n'aurait écrit la clause de déchéance du terme : il aurait
        suffi d'additionner tous les termes, et l'écran aurait réclamé ce qu'on
        ne peut pas réclamer en droit.
        """
        today = fields.Date.context_today(self.env.user)
        late = self._term(today - relativedelta(months=1), 1200.0)
        future = self._term(today + relativedelta(months=1), 1200.0)
        terms = late | future
        self.assertEqual(late.state, "late")
        self.assertEqual(future.state, "pending")
        self.assertEqual(terms._portal_outstanding(terms), 1200.0)

    def test_a_term_deposited_at_court_is_not_owed(self):
        """⚠️ L'art. 1907 : le locataire a payé, ailleurs. Le compter comme un
        arrérage accuserait quelqu'un d'avoir fait ce que la loi lui permet."""
        today = fields.Date.context_today(self.env.user)
        deposited = self._term(today - relativedelta(months=1), 1200.0,
                               deposited_at_court=True)
        self.assertEqual(deposited.state, "deposited")
        self.assertEqual(deposited._portal_outstanding(deposited), 0.0)

    def test_a_partial_payment_leaves_only_the_balance(self):
        today = fields.Date.context_today(self.env.user)
        term = self._term(today - relativedelta(months=1), 1200.0)
        self.env["bf.rental.payment"].create(
            {"term_id": term.id, "date": today, "amount": 500.0}
        )
        self.assertEqual(term.state, "partial")
        self.assertEqual(term._portal_outstanding(term), 700.0)


@tagged("post_install", "-at_install")
class TestRentalPortalPages(HttpCase, RentalPortalCommon):
    """Les pages se rendent-elles, et la route tient-elle le cloisonnement ?

    ⚠️ Un gabarit qui compile n'est pas un gabarit qui rend. Et une règle
    d'accès éprouvée par l'ORM ne dit rien de la route qui sert le fichier.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attachment = cls.env["ir.attachment"].create(
            {"name": "bail-signe.pdf", "raw": b"%PDF-1.4 bail-du-locataire",
             "mimetype": "application/pdf"}
        )
        cls.lease.lease_attachment_ids = [(6, 0, [cls.attachment.id])]
        cls.other_attachment = cls.env["ir.attachment"].create(
            {"name": "bail-du-voisin.pdf", "raw": b"%PDF-1.4 bail-du-voisin",
             "mimetype": "application/pdf"}
        )
        cls.other_lease.lease_attachment_ids = [(6, 0, [cls.other_attachment.id])]

        # Un avis de modification reçu, dont le délai de réponse court encore.
        #
        # ⚠️ La date visée se calcule depuis AUJOURD'HUI, pas en dur. L'art. 1942
        # veut l'avis « au moins trois mois, mais pas plus de six » avant la date
        # visée, sur un bail de douze mois ou plus : une date figée au fichier
        # ferait passer la suite verte aujourd'hui et rouge dans trois mois, sans
        # que rien n'ait changé au code.
        today = fields.Date.context_today(cls.env.user)
        cls.notice = cls.env["bf.rental.notice"].create(
            {
                "lease_id": cls.lease.id,
                "kind": "modification",
                "date_given": today,
                "date_received": today,
                "target_date": today + relativedelta(months=4),
                "state": "given",
            }
        )
        cls.term = cls.env["bf.rental.term"].create(
            {"lease_id": cls.lease.id, "date_due": "2026-08-01",
             "amount_due": 1200.0}
        )

    def test_the_three_pages_render_for_a_tenant(self):
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        for url, needle in [
            ("/my/rental", "BAIL-PORTAIL-1"),
            ("/my/rental/notices", "Modification du bail"),
            ("/my/rental/rent", "Exigible le"),
        ]:
            response = self.url_open(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertIn(needle, flat(response.text), url)

    def test_the_notice_page_says_what_silence_produces(self):
        """🔴 L'information la plus chère du corpus.

        Le silence sur une modification vaut ACCEPTATION (art. 1945) : le bail
        est reconduit avec tout ce qui a été demandé. C'est la seule chose que
        ce portail existe vraiment pour dire.
        """
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        text = flat(self.url_open("/my/rental/notices").text)
        self.assertIn("art. 1945", text)
        self.assertIn("réputé accepté", text)
        self.assertIn("Répondre ne coûte rien ; ne pas répondre décide.", text)

    def test_the_rent_page_never_threatens(self):
        """🔴 Aucun écran ne conclut à l'éviction.

        Seul le tribunal résilie (art. 1971), le seuil de trois semaines ne
        touche qu'à sa marge de manœuvre (art. 1973), et payer avant jugement
        arrête tout (art. 1883). Un portail qui ferait peur dirait trois
        faussetés et n'aiderait personne.
        """
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        text = flat(self.url_open("/my/rental/rent").text)
        for word in ("éviction", "évincé", "expulsion", "résiliation",
                     "résilier"):
            self.assertNotIn(word, text, word)

    def test_the_pages_never_show_a_1974_1_ground(self):
        self.env["bf.rental.notice"].create(
            {"lease_id": self.lease.id, "kind": "modification",
             "date_given": fields.Date.context_today(self.env.user),
             "state": "given"}
        )
        self.env["bf.rental.notice"].create(
            {
                "lease_id": self.lease.id,
                "kind": "tenant_resiliation",
                "date_given": fields.Date.context_today(self.env.user),
                "resiliation_ground": "violence",
                "attestation_received": True,
                "state": "given",
            }
        )
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        text = flat(self.url_open("/my/rental/notices").text)
        # ⚠️ Le libellé du fondement ne contient PAS le mot « violence » : il dit
        # « Sécurité menacée (art. 1974.1) ». Chercher « violence » passerait au
        # vert sur une page qui affiche tout. Ce qui se cherche, c'est le
        # libellé réel et l'article.
        # 🔴 La résiliation elle-même ne paraît plus : un colocataire lirait
        # « elle part, et quand ». La page porte un autre avis, pour que
        # l'absence se constate sur une page qui affiche bien des avis.
        self.assertIn("Modification du bail", text)
        self.assertNotIn("Résiliation par le locataire", text)
        self.assertNotIn("Sécurité menacée", text)
        self.assertNotIn("ministre de la Justice", text)

    def test_the_form_route_serves_the_tenants_own_lease(self):
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        response = self.url_open(
            "/my/rental/lease/%d/form/%d" % (self.lease.id, self.attachment.id)
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"bail-du-locataire", response.content)

    def test_the_form_route_refuses_someone_elses_lease(self):
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        response = self.url_open(
            "/my/rental/lease/%d/form/%d"
            % (self.other_lease.id, self.other_attachment.id),
            allow_redirects=False,
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("bail-du-voisin", response.text or "")

    def test_the_form_route_refuses_an_attachment_from_another_lease(self):
        """🔴 Le deuxième contrôle, celui qu'on oublie.

        La règle d'accès dit que ce bail-ci est le mien. Elle ne dit RIEN de la
        pièce demandée. Sans la vérification d'appartenance, un identifiant
        deviné servirait n'importe quelle pièce jointe de la base à qui possède
        un bail quelconque.
        """
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        response = self.url_open(
            "/my/rental/lease/%d/form/%d"
            % (self.lease.id, self.other_attachment.id),
            allow_redirects=False,
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("bail-du-voisin", response.text or "")

    def test_a_file_slipped_into_the_relation_is_not_served(self):
        """🔴 L'appartenance à la relation ne suffit pas.

        Odoo ne contrôle aucun droit sur la pièce qu'on glisse dans un
        many2many. Une pièce d'ailleurs (ici, celle d'une fiche contact) mise
        dans la relation du bail ne sort ni à la liste ni au téléchargement :
        seules les pièces RATTACHÉES au bail sortent.
        """
        foreign = self.env["ir.attachment"].create(
            {"name": "piece-etrangere.pdf", "raw": b"%PDF-1.4 piece-etrangere",
             "mimetype": "application/pdf",
             "res_model": "res.partner", "res_id": self.env.user.partner_id.id}
        )
        self.lease.lease_attachment_ids = [(4, foreign.id)]
        self.assertEqual(foreign.res_model, "res.partner")
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        page = self.url_open("/my/rental")
        self.assertIn("bail-signe.pdf", page.text)
        self.assertNotIn("piece-etrangere", page.text)
        response = self.url_open(
            "/my/rental/lease/%d/form/%d" % (self.lease.id, foreign.id),
            allow_redirects=False,
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn("piece-etrangere", response.text or "")

    def test_the_registered_occupant_gets_an_empty_page(self):
        """🔴 La même preuve, mais sur la ROUTE."""
        self.authenticate("rt_registered", "rt_registered_pwd")
        response = self.url_open("/my/rental")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("BAIL-PORTAIL-1", response.text)

    def test_the_standard_portal_home_still_renders(self):
        """La carte ajoutée ne doit pas casser /my/home pour tout le monde.

        Le gabarit hérite de `portal.portal_my_home` : une greffe qui échoue y
        casserait la page d'accueil de TOUS les utilisateurs du portail, y
        compris ceux qui n'ont jamais loué quoi que ce soit.
        """
        self.authenticate("rt_tenant", "rt_tenant_pwd")
        response = self.url_open("/my/home")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Mon bail", response.text)


@tagged("post_install", "-at_install")
class TestRentalPortalBranches(HttpCase, RentalPortalCommon):
    """Les branches que le cas ordinaire ne rend jamais.

    🔴 **Un gabarit QWeb ne se compile pas, il se rend.** Une phrase fausse ou
    un champ inexistant dans une branche conditionnelle ne casse que le jour où
    cette branche est prise : la mention de l'art. 1955 sur un bail restreint,
    l'avertissement de l'art. 1945 al. 2 sur un logement visé, le badge du
    dépôt au greffe, le délai de l'art. 1947 al. 2 après un refus. Un test qui
    rend le cas ordinaire passe au vert en laissant quatre branches muettes.
    """

    def _login(self):
        self.authenticate("rt_tenant", "rt_tenant_pwd")

    def _page(self, url):
        response = self.url_open(url)
        self.assertEqual(response.status_code, 200, url)
        return flat(response.text)

    # ── Art. 1955 : la restriction au droit à la fixation ──

    def test_a_restricted_lease_says_so_and_says_where_to_read_it(self):
        """Art. 1955 al. 3 : la restriction ne vaut que si elle est AU BAIL.

        Le locataire a intérêt à savoir qu'elle est là, et à quelle section de
        son formulaire la relire — c'est ce qui lui permet de vérifier qu'elle
        y est vraiment.
        """
        self.lease.write({
            "fixation_restricted": True,
            "restriction_kind": "new",
            "ready_date": "2026-01-15",
            "max_rent_5y": 1500.0,
        })
        self._login()
        text = self._page("/my/rental")
        self.assertIn("1955", text)
        self.assertIn("cinq premières années", text)
        # La lettre de section n'est pas la même d'un formulaire à l'autre :
        # ce qui s'affiche est celle du formulaire employé, pas un « F » codé.
        self.assertEqual(self.lease.restriction_section, "F")
        self.assertIn("section", text)

    def test_an_unrestricted_lease_says_nothing_of_the_kind(self):
        """Le pendant : sans quoi le test ci-dessus passerait au vert sur une
        page qui affiche l'avertissement à tout le monde."""
        self.assertFalse(self.lease.fixation_restricted)
        self._login()
        self.assertNotIn("1955", self._page("/my/rental"))

    # ── Art. 1945 al. 2 : refuser n'est pas rester ──

    def test_when_refusing_means_leaving_the_page_says_it(self):
        """⚠️ Exception de l'art. 1945 al. 2 : sur un logement visé à
        l'art. 1955, le locataire qui refuse la modification doit quitter à la
        fin du bail. C'est le contraire de l'intuition, et c'est la branche la
        plus chère de la page.
        """
        self.lease.write({
            "fixation_restricted": True,
            "restriction_kind": "new",
            "ready_date": "2026-01-15",
            "max_rent_5y": 1500.0,
        })
        today = fields.Date.context_today(self.env.user)
        notice = self.env["bf.rental.notice"].create({
            "lease_id": self.lease.id,
            "kind": "modification",
            "date_given": today,
            "date_received": today,
            "target_date": today + relativedelta(months=4),
            "state": "given",
        })
        self.assertTrue(notice.tenant_must_leave_if_refusing)
        self._login()
        text = self._page("/my/rental/notices")
        self.assertIn("1945 al. 2", text)
        self.assertIn("quitter à la fin du bail", text)

    # ── 🔴 L'article ne se préfixe pas deux fois ──

    def test_the_article_is_never_written_twice(self):
        """🔴 `silence_article` vaut « art. 1945 », pas « 1945 ».

        Le gabarit le préfixait de « art. » : la page portait « art. art. 1945 »
        à trois endroits. Le test d'origine ne le voyait pas — il cherchait
        « 1945 », qui s'y trouve dans les deux cas. Ce qui se cherche ici, c'est
        la faute elle-même.
        """
        today = fields.Date.context_today(self.env.user)
        self.env["bf.rental.notice"].create({
            "lease_id": self.lease.id,
            "kind": "modification",
            "date_given": today,
            "date_received": today,
            "target_date": today + relativedelta(months=4),
            "state": "given",
        })
        self._login()
        text = self._page("/my/rental/notices")
        self.assertIn("art. 1945", text)
        self.assertNotIn("art. art.", text)

    # ── Art. 1947 al. 2 : le seul délai qui joue contre le locateur ──

    def test_after_a_refusal_the_page_gives_the_landlords_own_deadline(self):
        today = fields.Date.context_today(self.env.user)
        notice = self.env["bf.rental.notice"].create({
            "lease_id": self.lease.id,
            "kind": "modification",
            "date_given": today - relativedelta(months=2),
            "date_received": today - relativedelta(months=2),
            "target_date": today + relativedelta(months=2),
            "state": "given",
        })
        notice.write({"date_refused": today - relativedelta(days=5),
                      "state": "refused"})
        self.assertTrue(notice.landlord_tribunal_deadline)
        self._login()
        text = self._page("/my/rental/notices")
        self.assertIn("1947 al. 2", text)
        self.assertIn("conditions antérieures", text)

    # ── Art. 1907 : déposé au greffe n'est pas impayé ──

    def test_a_term_deposited_at_court_shows_as_deposited_not_late(self):
        today = fields.Date.context_today(self.env.user)
        self.env["bf.rental.term"].create({
            "lease_id": self.lease.id,
            "date_due": today - relativedelta(months=1),
            "amount_due": 1200.0,
            "deposited_at_court": True,
        })
        self._login()
        text = self._page("/my/rental/rent")
        self.assertIn("Déposé au greffe", text)
        self.assertNotIn("En retard", text)
        # Et il ne gonfle pas le solde : la loi le permet, l'écran ne doit pas
        # le compter comme une dette.
        self.assertNotIn("dû sur les termes déjà exigibles", text)

    # ── Art. 1895 : le formulaire remis dans les dix jours ──

    def test_a_lease_without_its_form_says_what_the_landlord_owes(self):
        """Un locataire qui n'a jamais reçu sa copie ne sait pas qu'il y a
        droit. La page le lui dit plutôt que d'afficher une liste vide."""
        self.assertFalse(self.lease.lease_attachment_ids)
        self._login()
        text = self._page("/my/rental")
        self.assertIn("1895", text)
        self.assertIn("dix jours", text)

    # ── Le bail sans fraction ──

    def test_a_room_lease_renders_without_a_unit(self):
        self.assertFalse(self.room_lease.unit_id)
        self.assertFalse(self.room_lease.portal_unit_name)
        self._login()
        text = self._page("/my/rental")
        self.assertIn("BAIL-CHAMBRE", text)
        self.assertIn("durée indéterminée", text)

    # ── Une page vide reste une page ──

    def test_the_pages_render_for_someone_with_no_lease_at_all(self):
        """🔴 Le cas que personne ne joue et que tout le monde rencontre.

        Un utilisateur du portail qui n'a aucun bail — ici l'occupant inscrit
        à la fraction, qui n'est partie à rien — doit obtenir une page, pas une
        erreur. Trois `search([])` vides traversent tout le gabarit, y compris
        le `currency_id` pris sur le premier terme et le `min()` des échéances,
        qui sont deux façons classiques de tomber sur une liste vide.
        """
        self.authenticate("rt_registered", "rt_registered_pwd")
        for url in ("/my/rental", "/my/rental/notices", "/my/rental/rent"):
            response = self.url_open(url)
            self.assertEqual(response.status_code, 200, url)
            self.assertNotIn("BAIL-PORTAIL-1", response.text, url)


@tagged("post_install", "-at_install")
class TestLeaseOfficeMessages(TransactionCase):
    """🔴 Sans ceci, le courriel du bureau sur un bail
    arrivait nu (« BAIL/2026/0001 », aucun bouton), et inviter les locataires
    exigeait l'administrateur. Joué avec un vrai compte de gestionnaire."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        landlord = cls.env["bf.property.organisation"].create(
            {"name": "Locations du bureau inc.", "kind": "landlord"})
        building = cls.env["bf.property.building"].create(
            {"name": "12 rue du Bureau", "organisation_id": landlord.id})
        cls.tenant = cls.env["res.partner"].create(
            {"name": "Locataire invitée", "email": "locataire-invite@example.invalid"})
        cls.lease = cls.env["bf.rental.lease"].create({
            "organisation_id": landlord.id, "building_id": building.id,
            "tenant_ids": [(6, 0, cls.tenant.ids)], "duration_kind": "fixed",
            "date_start": "2026-07-01", "date_end": "2027-06-30", "rent": 1200.0,
        })
        cls.manager = cls.env["res.users"].create({
            "name": "Gestionnaire du bail", "login": "gest_bail",
            "email": "gest-bail@example.invalid",
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref("bf_property_core.group_bf_property_manager").id])],
        })

    def test_a_manager_invites_the_tenants(self):
        self.lease.with_user(self.manager).action_bf_invite_portal()
        user = self.tenant.with_context(active_test=False).user_ids
        self.assertEqual(len(user), 1)
        self.assertTrue(user.has_group("base.group_portal"))

    def test_the_invitation_is_the_lessors(self):
        from unittest.mock import patch
        sent = []
        Template = type(self.env["mail.template"])
        original = Template.send_mail

        def spy(template_self, res_id, *args, **kwargs):
            sent.append(template_self)
            return original(template_self, res_id, *args, **kwargs)

        with patch.object(Template, "send_mail", autospec=True, side_effect=spy):
            self.lease.with_user(self.manager).action_bf_invite_portal()
        self.assertEqual(sent, [self.env.ref("bf_rental_portal.mail_template_lessee_invitation")])

    def test_the_subject_names_the_landlord_on_a_real_post(self):
        lease = self.lease.with_user(self.manager)
        lease.message_post(body="Inspection des détecteurs le 5 octobre.",
                           message_type="comment", subtype_xmlid="mail.mt_comment",
                           partner_ids=self.tenant.ids)
        self.assertIn("Locations du bureau inc.", lease._message_compute_subject())

    def test_a_portal_tenant_gets_a_button_to_their_home(self):
        groups = self.lease._notify_get_recipients_groups(
            self.env["mail.message"], "Bail")
        portal = next(data for name, _f, data in groups if name == "portal")
        self.assertTrue(portal["button_access"]["url"].endswith("/my/rental"))
