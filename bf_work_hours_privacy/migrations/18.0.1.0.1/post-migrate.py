"""18.0.1.0.1: the two purposes say who sees the hours (the person, and the colleagues they
name) and the indicators one reads in French. The records are noupdate: rewrite them here,
only where they still hold a text this module installed, in English AND in French (an edit
made since, in either language, is kept).
"""
import json

# Every text this module ever installed (the last of each list: a text from before the first release).
OLD = {
    "bf_work_hours": {
        "en_US": ['<p>Odoo reconstructs your working time from your own traces (browser activity, calendar, and the sources installed) and shows it to you only. Off until you turn it on; turning it off destroys your traces and your days.</p>'],
        "fr_CA": ["<p>Odoo reconstitue votre temps de travail à partir de vos propres traces (activité au navigateur, calendrier et sources installées) et ne le montre qu'à vous. Éteint tant que vous ne l'activez pas ; l'éteindre détruit vos traces et vos journées.</p>"],
    },
    "bf_work_insights": {
        "en_US": ['<p>From your reconstructed working time: the span of your day, your longest stretch, your meetings, your time outside working hours. For you only unless you share your days, no score, nothing decided from it.</p>',
                  '<p>From your reconstructed working time: the span of your day, your longest stretch, your meetings, your time outside working hours. For you only, no score, nothing decided from it.</p>'],
        "fr_CA": ["<p>À partir de votre temps de travail reconstitué : l'amplitude de votre journée, votre plus longue plage, vos rencontres, votre temps hors horaire. Pour vous uniquement, sans score, rien n'en est décidé.</p>"],
    },
}
NEW = {
    "bf_work_hours": {
        "en_US": '<p>Odoo reconstructs your working time from your own traces (browser activity, calendar, and the sources installed) and shows it to you, and to the colleagues you name if you share your days. Off until you turn it on; turning it off destroys your traces and your days, and ends your shares.</p>',
        "fr_CA": "<p>Odoo reconstitue votre temps de travail à partir de vos propres traces (activité au navigateur, calendrier et sources installées) et ne le montre qu'à vous et, si vous partagez vos journées, aux collègues que vous nommez. Éteint tant que vous ne l'activez pas ; l'éteindre détruit vos traces et vos journées, et met fin à vos partages.</p>",
    },
    "bf_work_insights": {
        "en_US": '<p>From your reconstructed working time: the span of your day, your longest stretch, your meetings, your time outside working hours. For you, and for the colleagues you name if you share your days. No score, nothing decided from it.</p>',
        "fr_CA": "<p>À partir de votre temps de travail reconstitué : l'amplitude de votre journée, votre plus longue plage, vos rencontres, votre temps hors horaire. Pour vous, et pour les collègues que vous nommez si vous partagez vos journées. Sans score, rien n'en est décidé.</p>",
    },
}


def migrate(cr, version):
    for code, old in OLD.items():
        # French: missing, still the English text (never translated), or a text of ours.
        cr.execute(
            """UPDATE privacy_purpose
                  SET plain_language_summary = plain_language_summary || %s::jsonb
                WHERE code = %s
                  AND plain_language_summary->>'en_US' = ANY(%s)
                  AND (plain_language_summary->>'fr_CA' IS NULL
                       OR plain_language_summary->>'fr_CA' = plain_language_summary->>'en_US'
                       OR plain_language_summary->>'fr_CA' = ANY(%s))""",
            [json.dumps(NEW[code]), code, old["en_US"], old["fr_CA"]])
