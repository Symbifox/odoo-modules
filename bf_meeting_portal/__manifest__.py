{
    'name': 'Rencontres - Portail client',
    'version': '18.0.2.0.1',
    'category': 'Services/Project',
    'summary': "Accès portail aux comptes rendus de rencontre déjà envoyés",
    'description': """
Rencontres - Portail client
===========================

Donne au client, dans son portail, un accès en lecture aux comptes rendus de
rencontre **qui lui ont déjà été envoyés par courriel**, sous /my/meetings.

Principe de sûreté
------------------
Le portail n'est pas une nouvelle divulgation : c'est une archive
reconsultable. Un compte rendu n'apparaît que si ``report_state == 'sent'`` et
que ``report_sent_date`` est renseignée et que le partenaire figure dans
``report_recipient_ids``.

⚠️ L'état seul ne prouve rien : la vue kanban de bf_meeting est groupée dessus
sans ``records_draggable="0"``, et les imports comme les écritures XML-RPC le
posent directement (118 des 202 CR « envoyés » n'avaient aucune date d'envoi).
Seul ``action_send_report_direct`` estampe ``report_sent_date``, en exigeant des
destinataires et en mettant un courriel en file.

⚠️ À l'intégration : l'invariant tient tant que rien n'écrit ``report_sent_date``
à cru. Faites appeler ``action_send_report_direct`` par vos scripts et outils
externes plutôt que d'écrire les champs directement — le ``readonly=True`` du
champ est une contrainte de vue, pas une protection ORM.

On ne se fie PAS aux participants : l'envoi du compte rendu n'a délibérément
aucun repli sur eux (« No participant fallback: auto-filling caused accidental
sends to clients »).

Ordres du jour : volontairement NON exposés
-------------------------------------------
Aucun champ de ``bf_meeting`` ne prouve qu'un ordre du jour a été expédié.
``sent_date`` est estampé à la simple ouverture du composeur d'envoi et rien ne
l'efface ; l'état ``confirmed``/``done`` est atteignable sans aucun courriel
(``auto_send_on_confirm`` vaut False par défaut, et démarrer une rencontre ou
créer un compte rendu change l'état tout seul) ; et le fil de discussion ne
tranche pas, les ``mail.mail`` étant purgés après envoi. Un ordre du jour
interne jamais expédié devenait ainsi visible d'un client participant. Ils
reviendront quand un marqueur d'envoi fiable existera.

Étanchéité
----------
Aucun droit ORM n'est accordé au groupe portail (ni ``ir.model.access``, ni
``ir.rule``) : ce serait ouvrir ``/web/dataset/call_kw`` et laisser lire
``verbatim_html`` par RPC. Le contrôleur est la seule porte : domaine strict
puis ``sudo()``, et les gabarits ne reçoivent que des dictionnaires en liste
blanche. ``verbatim``, ``verbatim_html``, ``review_notes`` et les notes en
direct ne les atteignent jamais. ``report_state`` / ``report_sent_date`` /
``report_recipient_ids`` passent en ``copy=False`` : dupliquer un compte rendu
envoyé produisait sinon une copie « envoyée » jamais expédiée.

Le PDF réutilise le rapport attaché au courriel du compte rendu. Il est
regénéré à la lecture, donc reflète les corrections faites depuis l'envoi :
c'est le même rapport, pas le même octet.
    """,
    'author': 'Blue Fox Inc.',
    'website': 'https://bluefoxconsultant.com',
    'license': 'LGPL-3',
    'depends': ['bf_meeting', 'portal'],
    'data': [
        'views/meeting_portal_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
