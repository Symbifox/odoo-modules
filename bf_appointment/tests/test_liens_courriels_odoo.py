"""Les liens « Ouvrir dans Odoo » des gabarits doivent mener quelque part.

La notification organisateur pointait vers /odoo/all-bookings/<id>
alors qu'aucune action ne portait ce chemin. Le client web 18.0 répondait
« L'action "all-bookings" n'existe pas. » — et rien ne le signalait, parce que
le gabarit est en noupdate="1" et qu'aucun test ne lisait ses URL.

Ce test relit tous les gabarits du module et exige que chaque segment
/odoo/<segment>/ se résolve comme le fait le routeur (odoo.addons.web.controllers.utils.get_action).
"""

import re

from odoo.tests import TransactionCase, tagged

# Ce que le routeur accepte après /odoo/ : un chemin d'action, "action-<id|xmlid>",
# "m-<modele>", ou un nom de modèle pointé.
LIEN_ODOO = re.compile(r"/odoo/([A-Za-z0-9_.-]+)")


@tagged("bf_appointment", "bf_appointment_liens")
class TestLiensCourrielsOdoo(TransactionCase):

    def _segments_du_module(self):
        """Rend {xmlid du gabarit: [segments /odoo/... trouvés]}."""
        donnees = self.env["ir.model.data"].search([
            ("module", "=", "bf_appointment"),
            ("model", "=", "mail.template"),
        ])
        trouves = {}
        for donnee in donnees:
            gabarit = self.env["mail.template"].browse(donnee.res_id).exists()
            if not gabarit:
                continue
            segments = LIEN_ODOO.findall(gabarit.body_html or "")
            if segments:
                trouves[f"{donnee.module}.{donnee.name}"] = segments
        return trouves

    def test_le_bouton_ouvrir_dans_odoo_existe(self):
        """Au moins un gabarit porte le lien, sinon le test ne garde rien."""
        self.assertTrue(
            self._segments_du_module(),
            "Aucun lien /odoo/ dans les gabarits : le garde-fou ne surveille plus rien.",
        )

    def test_chaque_lien_odoo_se_resout(self):
        for xmlid, segments in self._segments_du_module().items():
            for segment in segments:
                with self.subTest(gabarit=xmlid, segment=segment):
                    self.assertTrue(
                        self._resout(segment),
                        f"{xmlid} pointe vers /odoo/{segment}/ : le client web "
                        f"répondra « L'action “{segment}” n'existe pas. »",
                    )

    def _resout(self, segment):
        if segment.startswith("action-"):
            reste = segment.removeprefix("action-")
            if reste.isdigit():
                return bool(self.env["ir.actions.actions"].sudo().browse(int(reste)).exists())
            return bool(self.env.ref(reste, raise_if_not_found=False))
        if segment.startswith("m-") or "." in segment:
            modele = segment.removeprefix("m-")
            return modele in self.env and not self.env[modele]._abstract
        return bool(self.env["ir.actions.actions"].sudo().search_count([("path", "=", segment)]))

    def test_le_chemin_all_bookings_est_pose_sur_l_action_oca(self):
        """Le correctif lui-même : c'est bf_appointment qui pose le chemin."""
        action = self.env.ref("resource_booking.resource_booking_action")
        self.assertEqual(action.path, "all-bookings")
        self.assertIn("form", action.view_mode.split(","))
