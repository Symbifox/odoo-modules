"""Install hook: per-language content for the maintenance rating email."""
from odoo.addons.bf_cx.i18n_content import apply_mail_templates_i18n

SUBJECTS_EN = {
    "mail_template_hosting_maintenance_rating":
        "How did your service maintenance go?",
}

TEXTS_EN = {
    "Votre avis": "Your feedback",
    "Bonjour": "Hello",
    "Nous venons de compléter une maintenance planifiée sur votre service":
        "We have just completed a scheduled maintenance on your service",
    "En un clic, comment évaluez-vous nos services d'hébergement ?":
        "In one click, how would you rate our hosting services?",
    "Un commentaire libre peut être ajouté après le clic. Merci !":
        "You can add a free-form comment after clicking. Thank you!",
    "Satisfait": "Satisfied",
    "Correct": "Okay",
    "Insatisfait": "Dissatisfied",
    "Vous préférez ne plus recevoir de demandes d'avis ?":
        "Prefer not to receive feedback requests?",
    "Me désabonner": "Unsubscribe",
}


def post_init_hook(env):
    apply_mail_templates_i18n(
        env, "bf_cx_hosting", "mail_template_data.xml", SUBJECTS_EN, TEXTS_EN
    )
