"""1.12.0: a second "yes" on the NPS testimonial question.

"Oui, vous pouvez me citer avec mon nom, ma fonction et mon organisation,
sans me recontacter" is itself the consent: the testimonial is created as
consented, the survey answer standing as proof.

The default survey and program are noupdate=1, and the new answer is
forcecreate="0": a -u creates nothing by itself. This script creates the
answer and wires it, and only where nobody has changed the default - the
program must still point at the module's own "contact me" answer, on a
single-choice question. Elsewhere the answer is not created at all, since a
respondent would see a choice that does nothing. Only the new answer is
translated; templates and the rest of the survey keep whatever the database
has made of them.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.bf_cx.i18n_content import SURVEY_TERMS

    contact_me = env.ref(
        "bf_cx.answer_nps_testimonial_yes", raise_if_not_found=False
    )
    if not contact_me:
        return
    question = contact_me.question_id
    if question.question_type != "simple_choice":
        return
    programs = env["bf.cx.program"].with_context(active_test=False).search(
        [
            ("testimonial_question_id", "=", question.id),
            ("testimonial_answer_id", "=", contact_me.id),
            ("testimonial_direct_answer_id", "=", False),
        ]
    )
    if not programs:
        return

    value_fr, value_en = SURVEY_TERMS["answer_nps_testimonial_direct"]["value"]
    answer = env.ref(
        "bf_cx.answer_nps_testimonial_direct", raise_if_not_found=False
    )
    if not answer:
        answer = env["survey.question.answer"].create(
            {
                "question_id": question.id,
                "sequence": 5,
                "value": value_fr,
            }
        )
        env["ir.model.data"].create(
            {
                "module": "bf_cx",
                "name": "answer_nps_testimonial_direct",
                "model": "survey.question.answer",
                "res_id": answer.id,
                "noupdate": True,
            }
        )
    codes = [code for code, _name in env["res.lang"].get_installed()]
    for code in codes:
        if code.startswith("fr"):
            answer.with_context(lang=code).write({"value": value_fr})
    if "en_CA" in codes:
        answer.with_context(lang="en_CA").write({"value": value_en})

    programs.write({"testimonial_direct_answer_id": answer.id})
