"""Install hook: per-language content for the meeting rating email."""
from odoo.addons.bf_cx.i18n_content import apply_mail_templates_i18n

SUBJECTS_EN = {
    "mail_template_meeting_rating": "How did our meeting go?",
}

TEXTS_EN = {
    "Votre avis": "Your feedback",
    "Bonjour": "Hello",
    "Nous venons de vous transmettre le compte rendu de notre rencontre. "
    "En un clic, comment évaluez-vous nos services jusqu'ici ?":
        "We have just sent you the report from our meeting. In one click, "
        "how would you rate our services so far?",
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
        env, "bf_cx_meeting", "mail_template_data.xml", SUBJECTS_EN, TEXTS_EN
    )
