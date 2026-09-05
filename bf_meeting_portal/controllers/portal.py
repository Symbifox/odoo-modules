from odoo import http
from odoo.http import request, content_disposition
from odoo.tools import format_datetime

from odoo.addons.portal.controllers.portal import CustomerPortal

# Statuts de présence -> libellé client (meeting.attendance.status)
ATTENDANCE_LABELS = {
    'present': 'Présent',
    'absent': 'Absent',
    'excused': 'Excusé',
}


def _partner_ids():
    """Partenaires que l'utilisateur portail courant « est », aux fins de visibilité.

    Lui-même et sa société : un document adressé à « Client inc. » reste visible
    pour ses contacts, mais un document adressé nommément à un collègue ne l'est
    pas.
    """
    partner = request.env.user.partner_id
    return list({partner.id, partner.commercial_partner_id.id})


def _record_domain():
    """Comptes rendus effectivement ENVOYÉS à ce partenaire.

    On ne se fie pas aux participants : l'envoi du CR n'a délibérément aucun
    repli sur eux (bf_meeting/models/meeting_record.py, action_send_report_direct
    — « No participant fallback: auto-filling caused accidental sends to
    clients »). Prendre « participant » comme critère de portail rouvrirait
    exactement le risque que cette décision a fermé.

    ⚠️ `report_state == 'sent'` ne suffit PAS. L'état est atteignable sans
    qu'aucun courriel ne parte : la vue kanban de bf_meeting est groupée sur
    ce champ sans `records_draggable="0"`, donc glisser une carte dans la
    colonne « Envoyé » émet un `write` nu ; les scripts d'import et les écritures
    XML-RPC le posent aussi directement. Sur la base BF, 118 des 202 comptes
    rendus « envoyés » n'ont ainsi aucune `report_sent_date`.

    Seul `action_send_report_direct` estampe `report_sent_date`, dans le même
    write que l'état. L'exiger rend l'invariant vrai au lieu de le promettre.

    ⚠️ Cela suppose que rien n'écrive `report_sent_date` à cru. Tout script ou
    outil externe touchant meeting.record doit passer par
    action_send_report_direct — le `readonly=True` du champ est une contrainte
    de vue, pas une protection ORM.
    """
    pids = _partner_ids()
    return [
        ('report_state', '=', 'sent'),
        ('report_sent_date', '!=', False),
        ('report_recipient_ids', 'in', pids),
    ]


# ------------------------------------------------------------------
# Ordres du jour : VOLONTAIREMENT NON EXPOSÉS.
#
# Aucun champ de bf_meeting ne prouve qu'un ordre du jour a été expédié :
#   - `sent_date` est estampé par action_send_agenda_wizard à la simple
#     OUVERTURE du composeur, et rien ne l'efface si on l'abandonne ;
#   - l'état `confirmed`/`done` est atteignable sans aucun courriel —
#     action_confirm() n'envoie que si `auto_send_on_confirm` (défaut False),
#     action_start_meeting() confirme un brouillon automatiquement, et
#     action_create_meeting_record() écrit `done` sans condition ;
#   - le fil de discussion ne tranche pas non plus : les `mail.mail` sont
#     purgés après envoi, et sur données réelles ni `message_type`, ni
#     `notification_ids`, ni `partner_ids` ne distinguaient l'envoi de
#     l'abandon (5 traces pour 23 envois avérés).
#
# Un ordre du jour interne jamais expédié se retrouvait ainsi visible d'un
# client participant. Tant que bf_meeting ne portera pas un marqueur d'envoi
# fiable, les ordres du jour restent hors du portail.
# ------------------------------------------------------------------


def _fmt_date(value):
    if not value:
        return ''
    return format_datetime(request.env, value, dt_format='d MMMM y')


def _flatten(items, key):
    """Aplatit une liste issue de structured_notes_json.

    Le gabarit PDF gère les deux formes (chaîne brute ou dict), on fait pareil
    pour rester à parité avec lui.
    """
    out = []
    for it in (items or []):
        if isinstance(it, str):
            text = it
        elif isinstance(it, dict):
            text = it.get(key) or ''
        else:
            text = str(it)
        if text:
            out.append(text)
    return out


def _date_chip(value):
    """Jour / mois / année séparés, pour la pastille de date des cartes."""
    if not value:
        return {'day': '', 'month': '', 'year': ''}
    return {
        'day': format_datetime(request.env, value, dt_format='d'),
        'month': format_datetime(request.env, value, dt_format='MMM'),
        'year': format_datetime(request.env, value, dt_format='y'),
    }


class PortalMeeting(CustomerPortal):

    # ------------------------------------------------------------------
    # Accueil du portail
    # ------------------------------------------------------------------

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if 'meeting_count' in counters:
            # sudo() : le groupe portail n'a aucun droit ORM sur ces modèles,
            # le domaine ci-dessus est ce qui borne le décompte.
            values['meeting_count'] = request.env['meeting.record'].sudo(
            ).search_count(_record_domain())
        return values

    # ------------------------------------------------------------------
    # Accès unitaire
    # ------------------------------------------------------------------

    def _get_record(self, record_id):
        """Renvoie le CR si — et seulement si — il a été envoyé à ce partenaire."""
        return request.env['meeting.record'].sudo().search(
            [('id', '=', record_id)] + _record_domain(), limit=1,
        )

    # ------------------------------------------------------------------
    # Listes blanches de rendu
    #
    # Les gabarits ne reçoivent JAMAIS l'enregistrement, seulement ces
    # dictionnaires. C'est ce qui rend verbatim / verbatim_html / review_notes /
    # structured_notes_json inatteignables depuis le portail, même si le
    # gabarit est modifié plus tard par distraction. Même patron que
    # MeetingContributionController._page_ctx dans bf_meeting.
    # ------------------------------------------------------------------

    def _record_ctx(self, rec):
        # Même source que le PDF : _get_report_data() est la méthode qui
        # alimente le rapport client. La page reprend donc exactement les
        # mêmes sections, au lieu de re-dériver les champs à la main et de
        # diverger du document que le client a reçu.
        data = rec._get_report_data()
        decisions = [
            {
                'name': d.name or '',
                'maker': d.decision_maker_id.name or '',
                'item': d.knowledge_item_id.name or '',
            }
            for d in rec.decision_ids if d.name
        ]
        attendance = self._attendance_ctx(rec)
        return {
            'id': rec.id,
            'kind': 'record',
            'name': rec.name or 'Compte rendu',
            'date': _fmt_date(rec.date),
            'chip': _date_chip(rec.date),
            'n_decisions': len(decisions),
            'n_attendance': len(attendance),
            'url': '/my/meetings/record/%d' % rec.id,
            'project': rec.project_id.name or '',
            'summary': rec.summary or '',
            'sent_date': _fmt_date(rec.report_sent_date),
            'decisions': decisions,
            'attendance': attendance,
            # Sections du PDF qui manquaient à la page : ce sont elles qui
            # portent le gros du contenu d'un compte rendu.
            'topics': [
                {
                    'title': t.get('title', ''),
                    'points': [str(p) for p in (t.get('points') or [])],
                }
                for t in (data.get('topics') or []) if isinstance(t, dict)
            ],
            'open_questions': _flatten(data.get('open_questions'), 'question'),
            'deliverables': _flatten(data.get('deliverables'), 'description'),
            'actions': [
                {
                    'name': t.name or '',
                    'who': ', '.join(u.name for u in t.user_ids),
                    'deadline': _fmt_date(t.date_deadline),
                    'done': t.state == '1_done',
                }
                for t in rec.task_ids if self._is_action_item(rec, t)
            ],
        }

    @staticmethod
    def _is_action_item(rec, task):
        """Ce qui vaut « élément d'action » aux yeux du client.

        Deux exclusions, constatées le 2026-09-04 sur l'aperçu du portail
        d'un client (compte rendu de statutaire) :
        - une tâche annulée n'est plus un engagement : le Meeting Processor en
          dépose parfois qui sont ensuite recadrées au verbatim et annulées, et le
          client les voyait encore listées comme actions à faire ;
        - la tâche qui porte le compte rendu lui-même (même nom que la rencontre,
          elle reçoit le temps de la séance) n'a jamais été une action.
        Les tâches terminées restent listées, marquées « fait » : elles disent ce
        qui a été livré depuis la rencontre.
        """
        if task.state == '1_canceled':
            return False
        if (task.name or '').strip() == (rec.name or '').strip():
            return False
        return True

    def _attendance_ctx(self, rec):
        """Présences telles qu'un client doit les lire.

        Deux corrections d'affichage :
        - `name` plutôt que `display_name`, sinon le nom est préfixé de la
          société (« Acme inc., Jane Doe ») ;
        - le partenaire racine (OdooBot) est écarté : il n'assiste à rien.

        On ne filtre PAS `is_company` : la donnée porte des personnes réelles
        cochées `is_company` par erreur, les exclure effacerait de vrais
        participants. On ne filtre pas non plus les partenaires internes :
        le client doit voir qui de Blue Fox était présent.
        """
        root = request.env.ref('base.partner_root', raise_if_not_found=False)
        root_id = root.id if root else 0
        return [
            {
                'name': a.partner_id.name or a.partner_id.display_name,
                'status': ATTENDANCE_LABELS.get(a.status, ''),
                'key': a.status or '',      # sert au style de la pastille
                'role': a.role or '',
            }
            for a in rec.attendance_ids
            if a.partner_id and a.partner_id.id != root_id
        ]

    # ------------------------------------------------------------------
    # Liste : /my/meetings
    # ------------------------------------------------------------------

    @http.route(['/my/meetings'], type='http', auth='user', website=True)
    def portal_my_meetings(self, **kw):
        records = request.env['meeting.record'].sudo().search(
            _record_domain(), order='date desc',
        )
        values = self._prepare_portal_layout_values()
        values.update({
            'records': [self._record_ctx(r) for r in records],
            'page_name': 'meetings',
            'default_url': '/my/meetings',
        })
        return request.render('bf_meeting_portal.portal_my_meetings', values)

    # ------------------------------------------------------------------
    # Détail
    # ------------------------------------------------------------------

    @http.route(['/my/meetings/record/<int:record_id>'],
                type='http', auth='user', website=True)
    def portal_meeting_record(self, record_id, **kw):
        rec = self._get_record(record_id)
        if not rec:
            return request.redirect('/my/meetings')
        values = self._prepare_portal_layout_values()
        values.update({
            'doc': self._record_ctx(rec),
            'page_name': 'meeting_record',
        })
        return request.render('bf_meeting_portal.portal_meeting_record', values)

    # ------------------------------------------------------------------
    # PDF — uniquement pour le COMPTE RENDU.
    #
    # Son gabarit courriel attache le rapport
    # (`report_template_ids` -> action_report_meeting_record), donc le PDF
    # servi ici est exactement celui que le client a déjà reçu.
    #
    # ⚠️ Le PDF est REGÉNÉRÉ à la lecture, pas repris de la pièce jointe
    # archivée : il reflète donc les corrections apportées au compte rendu
    # depuis l'envoi. C'est le même rapport, pas le même octet. Servir la
    # pièce jointe d'origine supposerait de la retrouver sur le message,
    # ce que cette version ne fait pas.
    # ------------------------------------------------------------------

    def _serve_pdf(self, report_ref, doc, filename):
        pdf, _dummy = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            report_ref, [doc.id],
        )
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', len(pdf)),
            ('Content-Disposition', content_disposition(filename)),
        ])

    @http.route(['/my/meetings/record/<int:record_id>/pdf'],
                type='http', auth='user', website=True)
    def portal_meeting_record_pdf(self, record_id, **kw):
        rec = self._get_record(record_id)
        if not rec:
            return request.redirect('/my/meetings')
        return self._serve_pdf(
            'bf_meeting.action_report_meeting_record', rec,
            'Compte_rendu_%s.pdf' % rec.id,
        )

