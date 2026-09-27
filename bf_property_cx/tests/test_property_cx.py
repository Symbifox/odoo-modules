"""La demande d'avis, et surtout les moments où elle ne part pas.

Trois idées sont éprouvées ici, et ce sont trois refus :

1. **Un refus ne se mesure pas.** `action_refuse` dit que la demande sort de
   l'objet du syndicat (art. 1039 C.c.Q.). Demander « comment ça s'est passé ? »
   là-dessus mesurerait le refus, pas le service.
2. **L'interrupteur du syndicat passe avant tout.** À l'arrêt, rien ne part,
   quelle que soit la configuration de bf_cx.
3. **Rien ne peut faire échouer la fermeture.** Le geste du concierge — dire ce
   qui a été fait — ne se perd pas parce qu'un courriel n'est pas parti.
"""
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPropertyCx(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de l'avis", "fraction_base": 1000}
        )
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble de l'avis", "organisation_id": cls.syndicat.id}
        )
        cls.unit = cls.env["bf.property.unit"].create(
            {
                "name": "101",
                "building_id": cls.building.id,
                "quote_part": 1000.0,
            }
        )
        cls.resident = cls.env["res.partner"].create(
            {"name": "Résidente", "email": "residente@example.invalid"}
        )
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.resident.id}
        )

    def _request(self, **kw):
        vals = {
            "organisation_id": self.syndicat.id,
            "building_id": self.building.id,
            "unit_id": self.unit.id,
            "requester_partner_id": self.resident.id,
            "category": "plumbing",
            "description": "Fuite sous l'évier depuis mardi.",
        }
        vals.update(kw)
        return self.env["bf.property.request"].create(vals)

    def _close(self, request, resolution="Joint remplacé."):
        request.resolution = resolution
        request.action_done()
        return request

    # ── L'interrupteur ──

    def test_the_switch_is_off_when_a_syndicat_is_created(self):
        """Rien ne part vers un occupant sans que le syndicat l'ait ouvert."""
        self.assertFalse(self.syndicat.cx_feedback_enabled)
        self.assertFalse(self.syndicat._cx_feedback_open())

    def test_nothing_is_asked_while_the_switch_is_off(self):
        request = self._request()
        with patch.object(
            type(request), "rating_send_request", autospec=True
        ) as send:
            self._close(request)
        send.assert_not_called()
        self.assertFalse(request.cx_feedback_sent)

    def test_a_settled_request_asks_its_requester(self):
        self.syndicat.cx_feedback_enabled = True
        request = self._request()
        with patch.object(
            type(request), "rating_send_request", autospec=True
        ) as send:
            self._close(request)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(request.cx_feedback_sent)
        self.assertEqual(request._rating_get_partner(), self.resident)

    # ── Ce qui ne se mesure pas ──

    def test_a_refused_request_is_never_measured(self):
        """🔴 Art. 1039 C.c.Q. : refuser, c'est dire que ce n'est pas notre objet.

        Mesurer la satisfaction juste après mesurerait le refus. Le module se
        tait, et ce test existe parce que c'est le comportement qu'un raccourci
        ajouterait un jour « pour couvrir tous les cas ».
        """
        self.syndicat.cx_feedback_enabled = True
        request = self._request()
        request.resolution = "La fenêtre est une partie privative."
        with patch.object(
            type(request), "rating_send_request", autospec=True
        ) as send:
            request.action_refuse()
        send.assert_not_called()
        self.assertEqual(request.state, "refused")
        self.assertFalse(request.cx_feedback_sent)

    def test_a_requester_without_an_email_is_told_at_the_thread(self):
        self.syndicat.cx_feedback_enabled = True
        silent = self.env["res.partner"].create({"name": "Sans courriel"})
        request = self._request(requester_partner_id=silent.id)
        with patch.object(
            type(request), "rating_send_request", autospec=True
        ) as send:
            self._close(request)
        send.assert_not_called()
        self.assertFalse(request.cx_feedback_sent)
        self.assertIn(
            "adresse de courriel",
            "".join(request.message_ids.mapped("body")),
        )

    def test_the_same_request_is_never_asked_twice(self):
        """Rouvrir puis refermer ne sollicite pas une deuxième fois."""
        self.syndicat.cx_feedback_enabled = True
        request = self._request()
        with patch.object(
            type(request), "rating_send_request", autospec=True
        ) as send:
            self._close(request)
            request.action_reopen()
            self._close(request)
        self.assertEqual(send.call_count, 1)

    def test_a_recently_solicited_person_is_left_alone(self):
        """Le garde-fou de bf_cx compte par personne, pas par syndicat."""
        self.syndicat.cx_feedback_enabled = True
        self.resident._bf_cx_mark_solicited()
        request = self._request()
        with patch.object(
            type(request), "rating_send_request", autospec=True
        ) as send:
            self._close(request)
        send.assert_not_called()
        self.assertIn(
            "sursollicitation",
            "".join(request.message_ids.mapped("body")),
        )

    # ── Ce qui ne doit jamais casser ──

    def test_a_failed_request_for_feedback_still_closes_the_ticket(self):
        """🔴 Le geste du concierge ne se perd pas parce qu'un courriel échoue.

        La demande d'avis tient dans un point de reprise : ce qui a été fait
        reste écrit, l'état reste « réglée », et l'échec va au journal.
        """
        self.syndicat.cx_feedback_enabled = True
        request = self._request()
        with patch.object(
            type(request),
            "rating_send_request",
            autospec=True,
            side_effect=ValueError("gabarit introuvable"),
        ):
            self._close(request, resolution="Membrane recollée.")
        self.assertEqual(request.state, "done")
        self.assertEqual(request.resolution, "Membrane recollée.")
        self.assertFalse(request.cx_feedback_sent)

    def test_the_email_carries_no_editor_brand(self):
        """⚠️ Le courriel vient du syndicat, comme l'attestation de l'art. 1068.1.

        Ni logo, ni couleur de marque, ni nom d'outil : un syndicat qui demande
        l'avis de ses occupants ne fait pas la publicité de son logiciel.
        """
        template = self.env.ref("bf_property_cx.mail_template_request_rating")
        body = template.body_html or ""
        for mark in ("Blue Fox", "bluefox", "Symbifox", "symbifox", "29ABE1"):
            self.assertNotIn(mark, body, "marque d'éditeur au gabarit : %s" % mark)
        self.assertIn("organisation_id.display_name", body)


@tagged("post_install", "-at_install")
class TestPropertyCxInRole(TransactionCase):
    """🔴 Joué en ADMIN, tout passait.

    En gestionnaire de copropriété, le pont `bf_cx_privacy` lisait les
    préférences de contact sans en avoir le droit ; l'AccessError était avalée
    par la fermeture, et aucun avis ne partait jamais. Ces tests ferment la
    demande avec un vrai compte de gestionnaire.
    """

    _request = TestPropertyCx._request
    _close = TestPropertyCx._close

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.syndicat = cls.env["bf.property.organisation"].create(
            {"name": "Syndicat de l'avis en rôle", "fraction_base": 1000})
        cls.building = cls.env["bf.property.building"].create(
            {"name": "Immeuble en rôle", "organisation_id": cls.syndicat.id})
        cls.unit = cls.env["bf.property.unit"].create(
            {"name": "201", "building_id": cls.building.id, "quote_part": 1000.0})
        cls.resident = cls.env["res.partner"].create(
            {"name": "Résident en rôle", "email": "resident-role@example.invalid"})
        cls.env["bf.property.ownership"].create(
            {"unit_id": cls.unit.id, "partner_id": cls.resident.id})
        from odoo.tests.common import new_test_user
        cls.manager = new_test_user(
            cls.env, login="qa-gestionnaire-cx",
            groups="base.group_user,bf_property_core.group_bf_property_manager")
        cls.syndicat.cx_feedback_enabled = True

    def test_a_manager_closing_a_request_gets_the_feedback_asked(self):
        request = self._request().with_user(self.manager)
        self._close(request)
        self.assertEqual(request.state, "done")
        self.assertTrue(request.sudo().cx_feedback_sent)
        self.assertEqual(len(request.sudo().rating_ids), 1)

    def test_a_failure_is_written_on_the_thread(self):
        request = self._request().with_user(self.manager)
        target = type(self.env["bf.property.request"])
        with patch.object(target, "_cx_maybe_request_feedback",
                          side_effect=RuntimeError("serveur injoignable")):
            self._close(request)
        self.assertEqual(request.state, "done")
        self.assertIn("serveur injoignable", request.sudo().message_ids[:1].body)


@tagged("post_install", "-at_install")
class TestPropertyCxWearsTheBrand(TransactionCase):
    """🔴 La demande d'avis part dans la mise
    en page de la société (`bf_mail_layout`), plus dans la légère d'Odoo."""

    def test_the_rating_request_wears_the_brand(self):
        syndicat = self.env["bf.property.organisation"].create(
            {"name": "Syndicat de l'avis marqué", "fraction_base": 1000,
             "cx_feedback_enabled": True})
        building = self.env["bf.property.building"].create(
            {"name": "Immeuble marqué", "organisation_id": syndicat.id})
        unit = self.env["bf.property.unit"].create(
            {"name": "901", "building_id": building.id, "quote_part": 1000.0})
        resident = self.env["res.partner"].create(
            {"name": "Résidente marquée", "email": "marquee@example.invalid"})
        self.env["bf.property.ownership"].create({"unit_id": unit.id, "partner_id": resident.id})
        request = self.env["bf.property.request"].create({
            "organisation_id": syndicat.id, "building_id": building.id, "unit_id": unit.id,
            "requester_partner_id": resident.id, "category": "plumbing", "description": "x"})
        request.resolution = "Réglé."
        request.action_done()
        asked = request.message_ids.filtered(lambda m: m.rating_ids or "rate/" in (m.body or ""))
        self.assertTrue(asked)
        self.assertEqual(asked[:1].email_layout_xmlid, "bluefox_branding.bf_mail_layout")


@tagged("post_install", "-at_install")
class TestPropertyCxTemplateInBase(TransactionCase):
    """🔴 Le gabarit est `noupdate` : une montée qui ne le réécrit pas laisse
    l'ancien corps en base sans rien dire. On lit la BASE, pas le fichier."""

    def test_the_template_in_base_is_the_branded_one(self):
        body = self.env.ref("bf_property_cx.mail_template_request_rating").body_html
        self.assertIn("brand_primary", body)
        self.assertNotIn("Georgia", body)
