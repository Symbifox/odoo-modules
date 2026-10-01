"""Les gabarits de rencontre pointent la route du socle, pas `/web/image`.

`/web/image/res.company/<id>/<champ>` ne sert la vraie image qu'aux sociétés que
l'usager anonyme a le droit de lire, soit la seule société du site web, et rend
l'image grise de remplacement avec un HTTP 200 pour toutes les autres. Le logo
d'une société secondaire disparaissait donc des courriels sans aucune erreur.

La route elle même, et ses variantes, sont éprouvées dans `bf_onboarding_base`,
qui la porte. Ici on garde la seule chose que ce module puisse perdre : un
appelant qui repasserait par `/web/image`.

Depuis 18.0.3.63.0, les courriels n'ont plus de logo à eux : la
mise en page commune porte le sien. Voir `test_mise_en_page_commune`.
"""

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMeetingTemplatesUseBrandRoute(TransactionCase):

    def test_les_courriels_ne_repassent_pas_par_web_image(self):
        for xmlid in ('bf_meeting.meeting_report_mail_template',
                      'bf_meeting.meeting_agenda_mail_template'):
            with self.subTest(gabarit=xmlid):
                corps = self.env.ref(xmlid).body_html or ''
                self.assertNotIn('/web/image/res.company', corps)

    def test_la_page_publique_de_contribution_aussi(self):
        """Elle avait le même défaut, et personne ne l'avait encore vu."""
        arch = self.env.ref('bf_meeting.agenda_contrib_page').arch_db or ''
        self.assertNotIn('/web/image/res.company', arch)
        self.assertIn('/brand/logo/', arch)
