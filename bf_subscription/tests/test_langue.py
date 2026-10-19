"""Chaque texte écrit en base par le module est dans la langue de qui le lira.

La source du module est anglaise et le français vit dans `i18n/fr_CA.po`. En plus
des libellés, le code écrivait ses alertes, ses activités, son récapitulatif et
ses factures de refacturation dans la langue de la personne (ou de la tâche
planifiée) qui les déclenchait, pas dans celle du lecteur :

* l'alerte de hausse de prix (note au fil + activité) est lue par le responsable
  de l'abonnement ;
* l'activité de préavis de renouvellement, posée par une tâche planifiée sans
  langue, est lue par le responsable, à défaut dans la langue de la société ;
* le récapitulatif part à chaque destinataire dans sa langue (sujet, corps, pied
  de page, PDF et nom de la pièce jointe) ;
* la facture de refacturation (ligne et note) est lue par le client géré, alors
  que la trace au fil de l'abonnement reste dans la langue de l'acteur, mode
  compris.

Chaque essai déclenche le geste dans une langue (contexte épinglé) et lit le
texte écrit pour un lecteur de l'autre langue. Les langues sont épinglées
(fr_CA, en_CA), qu'ils activent : sur une base réelle, OdooBot et l'admin sont en
fr_CA. Rien ne sort : `mail.mail.send` est neutralisé, les courriels créés
suffisent.
"""

import html
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestLangue(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        for code in ('fr_CA', 'en_CA'):
            cls.env['res.lang']._activate_lang(code)
        cls.env['ir.module.module']._load_module_terms(
            ['bf_subscription'], ['fr_CA'], overwrite=True)
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.anglo = Users.create({
            'name': 'Ann Langue', 'login': 'ann_sub_essai', 'email': 'ann.sub@langue.invalid',
            'lang': 'en_CA', 'groups_id': [Command.set([cls.env.ref('base.group_user').id])],
        })
        cls.franco = Users.create({
            'name': 'Fanny Langue', 'login': 'fanny_sub_essai', 'email': 'fanny.sub@langue.invalid',
            'lang': 'fr_CA', 'groups_id': [Command.set([cls.env.ref('base.group_user').id])],
        })
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Fournisseur Langue L7', 'supplier_rank': 1,
        })
        cls.client_en = cls.env['res.partner'].create({
            'name': 'Client English L7', 'is_company': True, 'lang': 'en_CA',
        })
        cls.client_fr = cls.env['res.partner'].create({
            'name': 'Client Français L7', 'is_company': True, 'lang': 'fr_CA',
        })

    # ── Outils ──────────────────────────────────────────────────────

    def _abonnement(self, **vals):
        valeurs = {
            'name': 'Abonnement Langue L7', 'vendor_id': self.vendor.id,
            'cycle': 'annual', 'cycle_amount': 100.0, 'state': 'active',
            'start_date': fields.Date.context_today(self.env['subscription.subscription']),
        }
        valeurs.update(vals)
        return self.env['subscription.subscription'].create(valeurs)

    @staticmethod
    def _textes(messages):
        return html.unescape(' '.join(str(m.body) for m in messages))

    # ── L'alerte de hausse de prix ──────────────────────────────────

    def _alerte(self, responsable, lang_du_geste):
        sub = self._abonnement(responsible_id=responsable.id, price_alert_threshold_pct=15.0)
        avant = sub.message_ids
        self.env['account.move'].with_context(lang=lang_du_geste).create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id,
            'invoice_date': fields.Date.today(), 'subscription_id': sub.id,
            'invoice_line_ids': [Command.create({
                'name': 'Hausse L7', 'quantity': 1, 'price_unit': 200.0,
                'tax_ids': [Command.clear()],
            })],
        })
        return sub, self._textes(sub.message_ids - avant), sub.activity_ids

    def test_alerte_de_hausse_dans_la_langue_du_responsable_anglais(self):
        """Une facture liée par un francophone alerte un responsable anglais en anglais."""
        sub, fil, activites = self._alerte(self.anglo, 'fr_CA')
        self.assertIn('Price increase detected', fil)
        self.assertNotIn('Hausse de prix détectée', fil)
        self.assertIn('Check the price increase: %s' % sub.name, activites.mapped('summary'))

    def test_alerte_de_hausse_dans_la_langue_du_responsable_francais(self):
        """Une facture liée dans un contexte anglais alerte une responsable francophone en français."""
        sub, fil, activites = self._alerte(self.franco, 'en_CA')
        self.assertIn('Hausse de prix détectée', fil)
        self.assertNotIn('Price increase detected', fil)
        self.assertIn('Vérifier la hausse de prix — %s' % sub.name, activites.mapped('summary'))

    # ── Le préavis de renouvellement (tâche planifiée) ──────────────

    def _preavis(self, lang_de_la_tache, **vals):
        # Annuel, renouvelé dans 20 jours, préavis de 30 : la date butoir est déjà dans l'horizon.
        sub = self._abonnement(
            start_date=fields.Date.today() + relativedelta(days=20),
            notice_period_days=30, auto_renew=True, **vals)
        self.assertTrue(sub.next_billing_date)
        self.env['subscription.subscription'].with_context(
            lang=lang_de_la_tache)._cron_renewal_alerts()
        activite = sub.activity_ids
        self.assertEqual(len(activite), 1)
        return sub, activite.summary, html.unescape(str(activite.note))

    def test_preavis_dans_la_langue_du_responsable_anglais(self):
        """La tâche planifiée, même lancée en français, écrit au responsable anglais en anglais."""
        sub, sommaire, note = self._preavis('fr_CA', responsible_id=self.anglo.id)
        self.assertEqual(sommaire, 'Upcoming renewal: %s' % sub.name)
        self.assertIn('renews on', note)
        self.assertNotIn('se renouvelle le', note)

    def test_preavis_dans_la_langue_du_responsable_francais(self):
        """La tâche planifiée, même lancée en anglais, écrit à la responsable francophone en français."""
        sub, sommaire, note = self._preavis('en_CA', responsible_id=self.franco.id)
        self.assertEqual(sommaire, 'Renouvellement à venir — %s' % sub.name)
        self.assertIn('se renouvelle le', note)
        self.assertNotIn('renews on', note)

    def test_preavis_sans_responsable_prend_la_langue_de_la_societe(self):
        """Sans responsable, le préavis est écrit dans la langue de la société."""
        self.env.company.partner_id.lang = 'en_CA'
        sub, sommaire, note = self._preavis('fr_CA', responsible_id=False)
        self.assertEqual(sommaire, 'Upcoming renewal: %s' % sub.name)
        self.assertIn('renews on', note)

    # ── Le récapitulatif ────────────────────────────────────────────

    def _recapitulatif(self, lang_du_geste, **vals):
        self._abonnement()  # au moins un abonnement actif : le récapitulatif a du contenu
        digest = self.env['subscription.digest'].create(dict({
            'name': 'Récap L7', 'company_id': self.env.company.id,
            'user_ids': [Command.set([self.anglo.id, self.franco.id])],
        }, **vals))
        Mail = self.env['mail.mail']
        avant = Mail.search([])
        with patch.object(type(Mail), 'send', lambda self, *a, **k: True):
            digest.with_context(lang=lang_du_geste)._send_digest()
        envois = Mail.search([]) - avant
        return {
            'en': envois.filtered(lambda m: m.email_to == self.anglo.email),
            'fr': envois.filtered(lambda m: m.email_to == self.franco.email),
        }

    def _verifier_recapitulatif(self, envois):
        en, fr = envois['en'], envois['fr']
        self.assertEqual(len(en), 1)
        self.assertEqual(len(fr), 1)
        # Sujet
        self.assertTrue(en.subject.startswith('Subscription digest: '), en.subject)
        self.assertTrue(fr.subject.startswith('Récapitulatif des abonnements — '), fr.subject)
        # Corps et pied de page
        corps_en, corps_fr = html.unescape(str(en.body_html)), html.unescape(str(fr.body_html))
        self.assertIn('Active subscriptions', corps_en)
        self.assertIn('Automatic subscription digest.', corps_en)
        self.assertNotIn('Récapitulatif automatique des abonnements.', corps_en)
        self.assertIn('Abonnements actifs', corps_fr)
        self.assertIn('Récapitulatif automatique des abonnements.', corps_fr)
        self.assertNotIn('Automatic subscription digest.', corps_fr)
        # Pièce jointe : nom et contenu (en mode essai, le « PDF » est son HTML)
        self.assertEqual(len(en.attachment_ids), 1)
        self.assertEqual(len(fr.attachment_ids), 1)
        self.assertNotEqual(en.attachment_ids, fr.attachment_ids)
        self.assertTrue(en.attachment_ids.name.startswith('Subscription_report_'), en.attachment_ids.name)
        self.assertTrue(fr.attachment_ids.name.startswith('Rapport_abonnements_'), fr.attachment_ids.name)
        pdf_en = html.unescape(en.attachment_ids.raw.decode('utf-8', 'replace'))
        pdf_fr = html.unescape(fr.attachment_ids.raw.decode('utf-8', 'replace'))
        self.assertIn('Subscription portfolio', pdf_en)
        self.assertNotIn("Portefeuille d'abonnements", pdf_en)
        self.assertIn("Portefeuille d'abonnements", pdf_fr)
        self.assertNotIn('Subscription portfolio', pdf_fr)

    def test_recapitulatif_lance_en_francais(self):
        """Lancé en français, le récapitulatif part en anglais à l'anglophone, en français à la francophone."""
        self._verifier_recapitulatif(self._recapitulatif('fr_CA'))

    def test_recapitulatif_lance_en_anglais(self):
        """Lancé en anglais, la francophone le reçoit quand même en français."""
        self._verifier_recapitulatif(self._recapitulatif('en_CA'))

    def test_nom_par_defaut_du_recapitulatif_traduit_a_la_creation(self):
        """Le nom par défaut est traduit à la création (lambda), pas figé à l'import."""
        Digest = self.env['subscription.digest']
        fr = Digest.with_context(lang='fr_CA').create({'auto_send': False})
        en = Digest.with_context(lang='en_CA').create({'auto_send': False})
        self.assertEqual(fr.name, 'Récapitulatif des abonnements')
        self.assertEqual(en.name, 'Subscription digest')

    # ── La refacturation ────────────────────────────────────────────

    def _refacturer(self, client, lang_acteur):
        sub = self._abonnement(
            name='Refac L7', managed_for_id=client.id, rebilled=True, rebill_mode='at_cost')
        avant = sub.message_ids
        assistant = self.env['subscription.rebill.wizard'].with_context(
            lang=lang_acteur, default_subscription_id=sub.id).create({})
        description = assistant.line_description
        assistant.action_create_invoice()
        facture = sub.customer_invoice_ids
        self.assertEqual(len(facture), 1)
        return {
            'description': description,
            'ligne': facture.invoice_line_ids[:1].name,
            'note': html.unescape(str(facture.narration)),
            'fil': self._textes(sub.message_ids - avant),
        }

    def test_refacturation_client_anglais_par_un_francophone(self):
        """Le client anglais lit sa facture en anglais ; le fil garde le français de l'acteur."""
        r = self._refacturer(self.client_en, 'fr_CA')
        self.assertEqual(r['description'], 'Rebilling: Refac L7')
        self.assertEqual(r['ligne'], 'Rebilling: Refac L7')
        self.assertIn('Rebilling of subscription Refac L7', r['note'])
        self.assertNotIn("Refacturation de l'abonnement", r['note'])
        self.assertIn('Facture de refacturation créée', r['fil'])
        self.assertNotIn('Rebill invoice created', r['fil'])
        # Le mode, lu par l'acteur francophone, est son libellé traduit, pas la valeur anglaise brute.
        self.assertIn('(mode : Au coût)', r['fil'])
        self.assertNotIn('At cost', r['fil'])

    def test_refacturation_client_francais_par_un_anglophone(self):
        """Le client francophone lit sa facture en français ; le fil garde l'anglais de l'acteur."""
        r = self._refacturer(self.client_fr, 'en_CA')
        self.assertEqual(r['description'], 'Refacturation – Refac L7')
        self.assertEqual(r['ligne'], 'Refacturation – Refac L7')
        self.assertIn("Refacturation de l'abonnement Refac L7", r['note'])
        self.assertNotIn('Rebilling of subscription', r['note'])
        self.assertIn('Rebill invoice created', r['fil'])
        self.assertIn('(mode: At cost)', r['fil'])
        self.assertNotIn('Facture de refacturation créée', r['fil'])

    # ── Le HTML des traces au fil (Markup) ──────────────────────────

    def test_alerte_de_hausse_porte_du_html_et_echappe_le_nom_de_facture(self):
        """La note d'alerte est du HTML (balises rendues), mais le nom de la facture reste échappé.

        Un corps passé en `str` à `message_post` est échappé en entier : le fil
        montrait « <b>Price increase detected</b> » en toutes lettres. Le corps
        est maintenant un `Markup`, et les valeurs passées par `%` sont échappées,
        donc un nom de facture qui porte des balises ne s'injecte pas.
        """
        sub = self._abonnement(responsible_id=self.anglo.id, price_alert_threshold_pct=15.0)
        avant = sub.message_ids
        # Créée sans abonnement (200 contre 100 : aucune liaison automatique), nommée, puis liée :
        # c'est la liaison manuelle (`write`) qui déclenche l'alerte.
        facture = self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': 'Hausse L7', 'quantity': 1, 'price_unit': 200.0,
                'tax_ids': [Command.clear()],
            })],
        })
        self.assertFalse(facture.subscription_id)
        facture.name = '<i>x</i>'
        facture.subscription_id = sub
        alerte = (sub.message_ids - avant).filtered(lambda m: 'Price increase detected' in str(m.body))
        self.assertEqual(len(alerte), 1)
        brut = str(alerte.body)
        self.assertIn('<b>Price increase detected</b>', brut)
        self.assertIn('<a ', brut)
        self.assertNotIn('&lt;b&gt;', brut)
        self.assertNotIn('&lt;a', brut)
        self.assertIn('&lt;i&gt;x&lt;/i&gt;', brut)
        self.assertNotIn('<i>x</i>', brut)

    def test_trace_de_refacturation_porte_du_html(self):
        """La trace de refacturation au fil est du HTML : le lien vers la facture est une vraie balise."""
        sub = self._abonnement(
            name='Refac HTML L7', managed_for_id=self.client_en.id, rebilled=True,
            rebill_mode='at_cost')
        avant = sub.message_ids
        self.env['subscription.rebill.wizard'].with_context(
            lang='en_CA', default_subscription_id=sub.id).create({}).action_create_invoice()
        trace = (sub.message_ids - avant).filtered(lambda m: 'Rebill invoice created' in str(m.body))
        self.assertEqual(len(trace), 1)
        brut = str(trace.body)
        self.assertIn('<a ', brut)
        self.assertNotIn('&lt;a', brut)

    def test_alerte_sans_responsable_dans_la_langue_de_la_societe(self):
        """Sans responsable, l'alerte est écrite dans la langue de la société, pas dans celle de l'usager.

        Avant, le repli était l'usager courant : une facture liée par une
        francophone écrivait en français l'alerte d'une société anglaise.
        """
        self.env.company.partner_id.lang = 'en_CA'
        sub = self._abonnement(responsible_id=False, price_alert_threshold_pct=15.0)
        self.assertFalse(sub.responsible_id)
        avant = sub.message_ids
        # Une usagère fr_CA déclenche l'alerte (sudo garde l'usager : seuls les droits sont levés).
        self.env['account.move'].with_user(self.franco).with_context(lang='fr_CA').sudo().create({
            'move_type': 'in_invoice', 'partner_id': self.vendor.id,
            'invoice_date': fields.Date.today(), 'subscription_id': sub.id,
            'invoice_line_ids': [Command.create({
                'name': 'Hausse L7', 'quantity': 1, 'price_unit': 200.0,
                'tax_ids': [Command.clear()],
            })],
        })
        fil = self._textes(sub.message_ids - avant)
        self.assertIn('Price increase detected', fil)
        self.assertNotIn('Hausse de prix détectée', fil)
