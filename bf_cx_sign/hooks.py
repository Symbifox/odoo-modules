"""Install hook: per-language content for the post-signature rating email."""
from odoo.addons.bf_cx.i18n_content import apply_mail_templates_i18n

SUBJECTS_EN = {
    "mail_template_sign_rating": "How was your signing experience?",
}

TEXTS_EN = {
    "Votre avis": "Your feedback",
    "Bonjour": "Hello",
    "Vous venez de signer le document «":
        "You have just signed the document “",
    "». En un clic, comment évaluez-vous cette expérience de signature ?":
        "”. In one click, how would you rate this signing experience?",
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
        env, "bf_cx_sign", "mail_template_data.xml", SUBJECTS_EN, TEXTS_EN
    )
