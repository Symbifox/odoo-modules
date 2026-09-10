"""Échange de comptes rendus entre locataires Symbifox.

Deux locataires Symbifox qui se rencontrent reçoivent aujourd'hui le compte
rendu sous forme de courriel et de PDF : lisible, mais à retaper. Ce module
ajoute une **copie lisible par la machine** au courriel du compte rendu, et
l'assistant qui la reprend de l'autre côté.

Trois règles tiennent tout le reste :

1. **La charge ne porte que ce que le PDF montre déjà.** L'échange n'est pas
   une divulgation nouvelle, c'est le même contenu sous une autre forme. Le
   verbatim, les notes de révision, l'état du raffinage et les pièces jointes
   restent chez l'émetteur.
2. **La charge ne porte aucun balisage.** Que du texte. Le contenu HTML des
   sujets est réduit en lignes à l'export et re-rendu par le gabarit échappé
   de `bf_meeting` à l'import : aucune balise venue d'un fichier n'atteint le
   navigateur de qui importe.
3. **La charge ne porte aucun identifiant réutilisable.** Les bases sont
   distinctes et leurs identifiants se recouvrent. Les personnes voyagent en nom et en
   courriel, et l'import
   n'apparie que ce qui existe déjà chez lui, et il ne crée jamais de contact.
"""

import json
import logging
import re
from html import unescape

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Nom de format et version. La version ne bouge que si la forme change d'une
# façon qu'un ancien lecteur ne saurait pas ignorer ; un ajout de clé, non.
EXCHANGE_FORMAT = 'symbifox.meeting.record'
EXCHANGE_VERSION = 1

# Plafonds. Mesurés sur un parc réel de 288 comptes rendus :
# résumé + notes structurées font 2,3 ko en médiane, 5,6 ko au 90e centile et
# 12,6 ko au maximum. Les plafonds ci-dessous laissent donc un facteur 40 sur
# le pire cas réel. Ils ne sont pas là pour cadrer l'usage, mais pour
# qu'un fichier hostile ne puisse pas faire travailler la base indéfiniment.
MAX_FILE_BYTES = 512 * 1024
MAX_ROSTER = 200
MAX_TOPICS = 100
MAX_POINTS = 200
MAX_DECISIONS = 300
MAX_ACTIONS = 300
MAX_QUESTIONS = 200
MAX_DELIVERABLES = 200
MAX_LINE = 2000
MAX_SUMMARY = 20000

_WS = re.compile(r'\s+')
# Deux passes, et l'ordre compte : une balise EN LIGNE se retire sans laisser
# d'espace (« au <b>31 août</b>. » doit rendre « au 31 août. », pas
# « au 31 août . »), tandis qu'une balise de BLOC sépare deux mots collés.
_INLINE_TAG = re.compile(r'</?(?:b|strong|i|em|u|s|span|a|small|sub|sup|code|font)\b[^>]*>', re.I)
_BLOCK_TAG = re.compile(r'</?(?:p|br|div|tr|td|th|h[1-6]|ul|ol|li|table|tbody)\b[^>]*>', re.I)
_ANY_TAG = re.compile(r'<[^>]+>')
# `<script>`/`<style>` : leur CONTENU part avec eux. Odoo assainit déjà les
# champs Html à l'écriture, mais `html_to_lines` sert aussi à du HTML qui n'est
# jamais passé par un champ.
_DROP_ELEMENT = re.compile(r'<(script|style)\b[^>]*>.*?</\1\s*>', re.S | re.I)
_LI = re.compile(r'<li[^>]*>(.*?)</li>', re.S | re.I)
_BLOCK_BREAK = re.compile(r'</?(?:p|br|div|tr)[^>]*>', re.I)


def html_to_lines(html_value):
    """Réduire un fragment HTML en lignes de texte.

    Les points d'un sujet sont stockés en `<ul><li>…</li></ul>` : sur les 400
    derniers sujets d'un parc réel, 400 portent un `<ul>` et 1691 un `<li>`. Les
    marques de style, elles, sont marginales : 9 `<em>`, 1 `<b>`, 1 `<i>` sur
    ces mêmes 1691 puces. Réduire en texte perd donc l'emphase d'une puce sur
    150, et fait disparaître toute la classe de problèmes qu'aurait posée du
    HTML arrivant par fichier.
    """
    if not html_value:
        return []
    raw = _DROP_ELEMENT.sub(' ', str(html_value))
    items = _LI.findall(raw)
    if not items:
        items = _BLOCK_BREAK.split(raw)
    lines = []
    for item in items:
        text = _INLINE_TAG.sub('', item)
        text = _BLOCK_TAG.sub(' ', text)
        text = _ANY_TAG.sub(' ', text)
        text = _WS.sub(' ', unescape(text)).strip()
        if text:
            lines.append(text[:MAX_LINE])
    return lines


class MeetingExchange(models.AbstractModel):
    """Construction, validation et reprise d'une copie portable."""

    _name = 'meeting.exchange'
    _description = 'Échange de comptes rendus entre locataires'

    # ── Émission ──────────────────────────────────────────────────────────────

    @api.model
    def build_payload(self, record):
        """Charge portable d'un compte rendu.

        Source des sujets : `topic_ids` quand il y en a, sinon les sujets des
        notes structurées. Ce n'est pas un détail de commodité : c'est la
        seule des deux représentations qu'une personne puisse corriger depuis
        la fiche (`structured_notes_json` y est en lecture seule). Voir la note
        mesuré : 20 des 183 comptes rendus qui portent les deux les
        ont divergentes, et deux de ces écarts sont des retraits volontaires
        de détails personnels que la représentation JSON avait gardés.
        """
        record.ensure_one()
        # 🔴 Les LIBELLÉS se lisent en sudo, et seulement eux. La charge cite le
        # nom d'un élément de matrice, d'un projet, d'une personne : tout cela
        # est déjà dans le PDF que le client reçoit. Sans sudo, un gestionnaire
        # de rencontres qui n'est pas dans le groupe de la matrice de
        # connaissances fait lever `AccessError` sur
        # `project.knowledge.item` à l'export. Mesuré sur un parc réel, où
        # 132 des 288 comptes rendus portent un élément de matrice et 870 des
        # 959 décisions un décideur ; sur une base de démonstration,
        # qui n'en porte aucun, la branche n'est jamais atteinte.
        # L'accès au compte rendu lui-même, lui, a déjà été vérifié : c'est
        # l'appelant qui nous le passe.
        record = record.sudo()
        data = {}
        if record.structured_notes_json:
            try:
                loaded = json.loads(record.structured_notes_json)
                if isinstance(loaded, dict):
                    data = loaded
            except (json.JSONDecodeError, TypeError, ValueError):
                data = {}

        topics = []
        if record.topic_ids:
            for topic in record.topic_ids.sorted(lambda t: (t.sequence, t.id)):
                topics.append({
                    'title': (topic.name or '')[:MAX_LINE],
                    'points': html_to_lines(topic.points_html)[:MAX_POINTS],
                })
        else:
            for topic in (data.get('topics') or [])[:MAX_TOPICS]:
                if not isinstance(topic, dict):
                    continue
                points = topic.get('points')
                points = points if isinstance(points, list) else []
                topics.append({
                    'title': str(topic.get('title') or '')[:MAX_LINE],
                    'points': [str(p)[:MAX_LINE] for p in points[:MAX_POINTS]],
                })

        roster = []
        for att in record.attendance_ids:
            roster.append({
                'name': (att.partner_id.name or '')[:MAX_LINE],
                'email': (att.partner_id.email or '')[:MAX_LINE],
                'status': att.status or 'present',
                'role': (att.role or '')[:MAX_LINE],
            })
        if not roster:
            for partner in record.participant_ids:
                roster.append({
                    'name': (partner.name or '')[:MAX_LINE],
                    'email': (partner.email or '')[:MAX_LINE],
                    'status': 'present',
                    'role': '',
                })

        decisions = []
        for dec in record.decision_ids.sorted(lambda d: (d.sequence, d.id)):
            decisions.append({
                'sequence': dec.sequence or 0,
                'name': (dec.name or '')[:MAX_LINE],
                'maker': (dec.decision_maker_id.name or '')[:MAX_LINE],
                'maker_email': (dec.decision_maker_id.email or '')[:MAX_LINE],
                'knowledge_item': (dec.knowledge_item_id.name or '')[:MAX_LINE],
            })

        actions = []
        for task in record.task_ids:
            actions.append({
                'name': (task.name or '')[:MAX_LINE],
                'assignees': ', '.join(task.user_ids.mapped('name'))[:MAX_LINE],
                'deadline': task.date_deadline.strftime('%Y-%m-%d')
                            if task.date_deadline else '',
            })

        def _flatten(values, key, limit):
            out = []
            for value in (values or [])[:limit]:
                if isinstance(value, dict):
                    value = value.get(key) or ''
                out.append(str(value)[:MAX_LINE])
            return [v for v in out if v]

        return {
            'format': EXCHANGE_FORMAT,
            'version': EXCHANGE_VERSION,
            'generated_at': fields.Datetime.to_string(fields.Datetime.now()),
            'source': {
                'db': self.env.cr.dbname,
                'company': record.company_id.name or self.env.company.name or '',
                'record_id': record.id,
                'sent_by': self.env.user.name or '',
            },
            'meeting': {
                'name': (record.name or '')[:MAX_LINE],
                'room_name': (record.room_name or '')[:MAX_LINE],
                'date': fields.Datetime.to_string(record.date) if record.date else '',
                'duration_minutes': record.duration_minutes or 0,
                'location': (record.location or '')[:MAX_LINE],
                'series_name': (record.series_name or '')[:MAX_LINE],
                'lang': record.lang or '',
                'project': (record.project_id.name or '')[:MAX_LINE],
                'summary': (record.summary or '')[:MAX_SUMMARY],
                'roster': roster[:MAX_ROSTER],
                'topics': topics[:MAX_TOPICS],
                'decisions': decisions[:MAX_DECISIONS],
                'action_items': actions[:MAX_ACTIONS],
                'open_questions': _flatten(
                    data.get('open_questions'), 'question', MAX_QUESTIONS),
                'deliverables': _flatten(
                    data.get('deliverables'), 'description', MAX_DELIVERABLES),
            },
        }

    # ── Réception ─────────────────────────────────────────────────────────────

    @api.model
    def parse_payload(self, raw):
        """Lire et valider un fichier d'échange. Lève `UserError` si douteux.

        Refus plutôt que réparation : un fichier mal formé signale un émetteur
        cassé, et un import qui rafistole en silence laisse le défaut vivre.
        Seuls les dépassements de plafond sont tronqués, et l'assistant le
        dit.
        """
        if isinstance(raw, str):
            raw = raw.encode('utf-8', 'replace')
        if not raw:
            raise UserError(_("Le fichier est vide."))
        if len(raw) > MAX_FILE_BYTES:
            raise UserError(_(
                "Le fichier fait %(size)s ko ; la limite est de %(max)s ko. "
                "Un compte rendu d'échange en fait 3 en moyenne : "
                "ce fichier n'en est probablement pas un.",
                size=len(raw) // 1024, max=MAX_FILE_BYTES // 1024,
            ))
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
            raise UserError(_(
                "Ce fichier n'est pas du JSON lisible : %s", err)) from err
        if not isinstance(payload, dict):
            raise UserError(_("Le fichier ne contient pas un objet JSON."))
        if payload.get('format') != EXCHANGE_FORMAT:
            raise UserError(_(
                "Ce fichier n'est pas une copie de compte rendu Symbifox "
                "(champ « format » : %s).", payload.get('format') or '(absent)'))
        version = payload.get('version')
        if not isinstance(version, int) or version > EXCHANGE_VERSION:
            raise UserError(_(
                "Version de format non prise en charge : %(got)s "
                "(cette instance lit jusqu'à la version %(max)s).",
                got=version, max=EXCHANGE_VERSION))

        meeting = payload.get('meeting')
        if not isinstance(meeting, dict):
            raise UserError(_("Le fichier ne contient pas de section « meeting »."))
        source = payload.get('source')
        source = source if isinstance(source, dict) else {}

        warnings = []
        env = self.env

        def _text(container, key, limit=MAX_LINE, path=''):
            value = container.get(key, '')
            if value is None:
                return ''
            if isinstance(value, (dict, list)):
                raise UserError(env._(
                    "Champ « %s » : du texte était attendu, un %s a été trouvé.",
                    path or key, type(value).__name__))
            value = str(value)
            if len(value) > limit:
                warnings.append(env._("« %s » a été tronqué.", path or key))
            return value[:limit]

        def _list(container, key):
            value = container.get(key, [])
            if value in (None, '', False):
                return []
            if not isinstance(value, list):
                raise UserError(env._(
                    "Champ « %s » : une liste était attendue, un %s a été "
                    "trouvé. Le fichier vient d'un émetteur défectueux.",
                    key, type(value).__name__))
            return value

        def _cap(items, limit, key):
            if len(items) > limit:
                warnings.append(env._(
                    "« %(key)s » comptait %(n)s entrées ; seules les "
                    "%(limit)s premières ont été reprises.",
                    key=key, n=len(items), limit=limit))
            return items[:limit]

        def _strings(container, key, limit, inner_key):
            out = []
            for entry in _cap(_list(container, key), limit, key):
                if isinstance(entry, dict):
                    entry = entry.get(inner_key) or ''
                elif isinstance(entry, list):
                    raise UserError(env._(
                        "Champ « %s » : une entrée est une liste imbriquée.", key))
                entry = str(entry).strip()
                if entry:
                    out.append(entry[:MAX_LINE])
            return out

        roster = []
        for entry in _cap(_list(meeting, 'roster'), MAX_ROSTER, 'roster'):
            if not isinstance(entry, dict):
                raise UserError(env._("« roster » : une entrée n'est pas un objet."))
            status = _text(entry, 'status', 20, 'roster.status') or 'present'
            roster.append({
                'name': _text(entry, 'name', MAX_LINE, 'roster.name').strip(),
                'email': _text(entry, 'email', MAX_LINE, 'roster.email').strip().lower(),
                'status': status if status in ('present', 'absent', 'excused') else 'present',
                'role': _text(entry, 'role', MAX_LINE, 'roster.role').strip(),
            })

        topics = []
        for entry in _cap(_list(meeting, 'topics'), MAX_TOPICS, 'topics'):
            if not isinstance(entry, dict):
                raise UserError(env._("« topics » : une entrée n'est pas un objet."))
            points = []
            for point in _cap(_list(entry, 'points'), MAX_POINTS, 'topics.points'):
                if isinstance(point, (dict, list)):
                    raise UserError(env._(
                        "« topics.points » : une puce n'est pas du texte."))
                point = str(point).strip()
                if point:
                    points.append(point[:MAX_LINE])
            topics.append({
                'title': _text(entry, 'title', MAX_LINE, 'topics.title').strip(),
                'points': points,
            })

        decisions = []
        for entry in _cap(_list(meeting, 'decisions'), MAX_DECISIONS, 'decisions'):
            if not isinstance(entry, dict):
                raise UserError(env._("« decisions » : une entrée n'est pas un objet."))
            sequence = entry.get('sequence', 0)
            decisions.append({
                'sequence': sequence if isinstance(sequence, int) else 0,
                'name': _text(entry, 'name', MAX_LINE, 'decisions.name').strip(),
                'maker': _text(entry, 'maker', MAX_LINE, 'decisions.maker').strip(),
                'maker_email': _text(
                    entry, 'maker_email', MAX_LINE, 'decisions.maker_email'
                ).strip().lower(),
                'knowledge_item': _text(
                    entry, 'knowledge_item', MAX_LINE, 'decisions.knowledge_item'
                ).strip(),
            })

        actions = []
        for entry in _cap(_list(meeting, 'action_items'), MAX_ACTIONS, 'action_items'):
            if not isinstance(entry, dict):
                raise UserError(env._("« action_items » : une entrée n'est pas un objet."))
            actions.append({
                'name': _text(entry, 'name', MAX_LINE, 'action_items.name').strip(),
                'assignees': _text(
                    entry, 'assignees', MAX_LINE, 'action_items.assignees').strip(),
                'deadline': _text(
                    entry, 'deadline', 40, 'action_items.deadline').strip(),
            })

        duration = meeting.get('duration_minutes', 0)
        if not isinstance(duration, int) or duration < 0 or duration > 100000:
            duration = 0

        return {
            'warnings': warnings,
            'source': {
                'db': _text(source, 'db', 200, 'source.db').strip(),
                'company': _text(source, 'company', MAX_LINE, 'source.company').strip(),
                'record_id': source.get('record_id')
                             if isinstance(source.get('record_id'), int) else 0,
                'sent_by': _text(source, 'sent_by', MAX_LINE, 'source.sent_by').strip(),
            },
            'generated_at': _text(payload, 'generated_at', 40, 'generated_at').strip(),
            'meeting': {
                'name': _text(meeting, 'name', MAX_LINE, 'meeting.name').strip(),
                'room_name': _text(meeting, 'room_name', MAX_LINE, 'meeting.room_name').strip(),
                'date': _text(meeting, 'date', 40, 'meeting.date').strip(),
                'duration_minutes': duration,
                'location': _text(meeting, 'location', MAX_LINE, 'meeting.location').strip(),
                'series_name': _text(meeting, 'series_name', MAX_LINE, 'meeting.series_name').strip(),
                'lang': _text(meeting, 'lang', 20, 'meeting.lang').strip(),
                'project': _text(meeting, 'project', MAX_LINE, 'meeting.project').strip(),
                'summary': _text(meeting, 'summary', MAX_SUMMARY, 'meeting.summary'),
                'roster': roster,
                'topics': topics,
                'decisions': decisions,
                'action_items': actions,
                'open_questions': _strings(
                    meeting, 'open_questions', MAX_QUESTIONS, 'question'),
                'deliverables': _strings(
                    meeting, 'deliverables', MAX_DELIVERABLES, 'description'),
            },
        }

    @api.model
    def source_ref(self, parsed):
        """Clé de provenance, stable et lisible : `base:identifiant`."""
        src = parsed.get('source') or {}
        db = src.get('db') or 'inconnu'
        rid = src.get('record_id') or 0
        return f"{db}:{rid}"

    # ── Reprise ───────────────────────────────────────────────────────────────

    # Contexte qui coupe toute notification. Un import rejoue des faits ; il ne
    # les annonce pas. Sans ça, créer un `meeting.record` (qui hérite de
    # `mail.thread`) abonne, trace et poste, et la reprise d'un compte rendu
    # d'il y a trois mois arriverait comme une nouveauté chez tout le monde.
    SILENT_CONTEXT = {
        'tracking_disable': True,
        'mail_create_nolog': True,
        'mail_create_nosubscribe': True,
        'mail_auto_subscribe_no_notify': True,
        'mail_notrack': True,
        'mail_activity_automation_skip': True,
        'mail_notify_force_send': False,
    }

    @api.model
    def _match_partners(self, emails):
        """Apparier des adresses à des contacts EXISTANTS. Ne crée rien.

        Créer les contacts manquants serait le geste évident et le mauvais :
        il verse dans le carnet d'adresses de qui importe des personnes qu'il
        n'a jamais rencontrées, à partir d'un fichier reçu par courriel. Ce
        qui ne s'apparie pas est nommé en toutes lettres dans le panneau
        « Copie reçue » : rien n'est perdu, rien n'est inventé.
        """
        emails = [e for e in {(e or '').strip().lower() for e in emails} if e]
        if not emails:
            return {}
        partners = self.env['res.partner'].search([
            ('email_normalized', 'in', emails),
        ])
        matched = {}
        for partner in partners:
            key = (partner.email_normalized or '').lower()
            # Premier trouvé : deux fiches peuvent porter la même adresse et
            # rien ici ne permet de trancher laquelle est la bonne.
            matched.setdefault(key, partner)
        return matched

    @api.model
    def _split_roster(self, roster):
        """Répartir un tour de table en présences appariées et laissés-pour-compte.

        Partagé entre l'aperçu de l'assistant et la reprise elle-même : deux
        boucles qui se ressemblent finissent toujours par diverger, et c'est
        l'aperçu qui aurait menti.

        Une même personne citée deux fois ne compte qu'une présence, et sa
        deuxième mention n'est pas pour autant « non appariée ».
        """
        matched = self._match_partners(
            [entry['email'] for entry in roster if entry['email']])
        attendance_vals, unmatched, seen = [], [], set()
        for entry in roster:
            partner = matched.get(entry['email']) if entry['email'] else None
            if partner is None:
                unmatched.append(entry)
                continue
            if partner.id in seen:
                continue
            seen.add(partner.id)
            attendance_vals.append((0, 0, {
                'partner_id': partner.id,
                'status': entry['status'],
                'role': entry['role'],
            }))
        return attendance_vals, unmatched, matched

    @api.model
    def _received_panel(self, parsed, unmatched, actions):
        """Panneau « Copie reçue » : provenance, et tout ce qui n'a pas de
        place dans le modèle cible. Construit en `Markup` à partir de valeurs
        échappées une à une. Aucun fragment du fichier n'y entre tel quel."""
        src = parsed.get('source') or {}
        rows = [Markup(
            '<p><strong>Copie reçue d\'un autre locataire Symbifox.</strong> '
            'Ce compte rendu a été rédigé ailleurs ; il est repris ici tel '
            'qu\'il a été transmis.</p>'
        )]
        provenance = [
            ('Base d\'origine', src.get('db') or '(non déclarée)'),
            ('Organisation', src.get('company') or '(non déclarée)'),
            ('Identifiant à la source', str(src.get('record_id') or '(non déclaré)')),
            ('Transmis par', src.get('sent_by') or '(non déclaré)'),
            ('Exporté le', parsed.get('generated_at') or '(non déclaré)'),
        ]
        rows.append(Markup('<ul>%s</ul>') % Markup('').join(
            Markup('<li><strong>%s :</strong> %s</li>') % (label, value)
            for label, value in provenance
        ))
        if actions:
            rows.append(Markup('<h3>Éléments d\'action à la source</h3>'))
            rows.append(Markup(
                '<p>Repris comme texte : aucune tâche n\'a été créée ici.</p>'))
            rows.append(Markup('<ul>%s</ul>') % Markup('').join(
                Markup('<li>%s%s%s</li>') % (
                    action['name'],
                    Markup(', %s') % action['assignees'] if action['assignees'] else '',
                    Markup(' (échéance %s)') % action['deadline'] if action['deadline'] else '',
                ) for action in actions
            ))
        if unmatched:
            rows.append(Markup('<h3>Participants non appariés</h3>'))
            rows.append(Markup(
                '<p>Ces personnes étaient à la rencontre mais n\'ont pas de '
                'fiche contact ici. Aucune fiche n\'a été créée.</p>'))
            rows.append(Markup('<ul>%s</ul>') % Markup('').join(
                Markup('<li>%s%s%s</li>') % (
                    entry['name'] or entry['email'] or '(sans nom)',
                    Markup(' &lt;%s&gt;') % entry['email'] if entry['email'] else '',
                    Markup(', %s') % entry['role'] if entry['role'] else '',
                ) for entry in unmatched
            ))
        return Markup('').join(rows)

    @api.model
    def apply_payload(self, parsed, project=None):
        """Créer le compte rendu correspondant à une charge déjà validée.

        🔴 La garde de groupe est ICI, pas seulement sur l'assistant. Le modèle
        est abstrait : il n'a pas de table, donc `ir.model.access` n'est jamais
        consulté pour lui, et une méthode publique d'un modèle abstrait reste
        appelable par `call_kw`. Sans cette ligne, la restriction « réservé au
        gestionnaire de rencontres » ne vivait que dans le formulaire, et la
        documentation promettait davantage que le code.
        """
        if not self.env.su and not self.env.user.has_group(
                'bf_meeting.group_meeting_manager'):
            raise AccessError(_(
                "Importer un compte rendu venu d'un autre locataire est "
                "réservé aux gestionnaires de rencontres."))
        meeting = parsed['meeting']
        env = self.env(context=dict(self.env.context, **self.SILENT_CONTEXT))

        emails = [d['maker_email'] for d in meeting['decisions'] if d['maker_email']]
        matched_makers = self._match_partners(emails)
        attendance_vals, unmatched, matched = self._split_roster(meeting['roster'])
        matched = dict(matched_makers, **matched)

        decision_vals = []
        for dec in meeting['decisions']:
            if not dec['name']:
                continue
            maker = matched.get(dec['maker_email']) if dec['maker_email'] else None
            values = {'sequence': dec['sequence'], 'name': dec['name']}
            if maker:
                values['decision_maker_id'] = maker.id
            details = []
            if not maker and dec['maker']:
                details.append(escape(_("Décideur à la source : %s", dec['maker'])))
            if dec['knowledge_item']:
                details.append(escape(
                    _("Élément de matrice à la source : %s", dec['knowledge_item'])))
            if details:
                values['description'] = Markup('<p>%s</p>') % Markup('<br/>').join(details)
            decision_vals.append((0, 0, values))

        topic_vals = []
        for index, topic in enumerate(meeting['topics']):
            if not topic['title'] and not topic['points']:
                continue
            points_html = Markup('<ul>%s</ul>') % Markup('').join(
                Markup('<li>%s</li>') % point for point in topic['points']
            ) if topic['points'] else False
            topic_vals.append((0, 0, {
                'sequence': (index + 1) * 10,
                'name': topic['title'] or _('Sujet sans titre'),
                'points_html': points_html,
            }))

        # Les notes structurées sont reconstruites depuis la charge validée, et
        # non recopiées d'un champ du fichier : `notes_html` est un calculé
        # stocké qui les traverse, et il n'a jamais été écrit pour de l'entrée
        # hostile (voir `_render_notes_html`).
        structured = {
            'summary': meeting['summary'],
            'participants': [
                entry['name'] for entry in meeting['roster'] if entry['name']
            ],
            'topics': [
                {'title': topic['title'], 'points': topic['points']}
                for topic in meeting['topics']
            ],
            'decisions': [dec['name'] for dec in meeting['decisions'] if dec['name']],
            'open_questions': meeting['open_questions'],
            'deliverables': meeting['deliverables'],
        }

        values = {
            'name': meeting['name'] or meeting['room_name'] or _('Compte rendu reçu'),
            'room_name': meeting['room_name'],
            'duration_minutes': meeting['duration_minutes'],
            'location': meeting['location'],
            'series_name': meeting['series_name'],
            'summary': meeting['summary'],
            'structured_notes_json': json.dumps(structured, ensure_ascii=False),
            'attendance_ids': attendance_vals,
            'decision_ids': decision_vals,
            'topic_ids': topic_vals,
            # L'état d'envoi ne se recopie pas. `bf_meeting_portal` ouvre un
            # compte rendu au portail dès que report_state == 'sent', que
            # report_sent_date est renseignée et que le partenaire figure aux
            # destinataires : une copie importée en « envoyé » s'afficherait
            # dans le portail des clients de qui l'a importée. Elle entre donc
            # en brouillon, sans destinataire et sans date d'envoi.
            'report_state': 'draft',
            'report_sent_date': False,
            'report_recipient_ids': [(5, 0, 0)],
            'exchange_source_ref': self.source_ref(parsed),
            'exchange_received_html': self._received_panel(
                parsed, unmatched, meeting['action_items']),
        }
        if meeting['date']:
            values['date'] = meeting['date']
        if project:
            values['project_id'] = project.id

        record = env['meeting.record'].create(values)
        _logger.info(
            "meeting.exchange: compte rendu %s repris depuis %s "
            "(%s présences appariées, %s non appariées)",
            record.id, values['exchange_source_ref'],
            len(attendance_vals), len(unmatched),
        )
        return record
