# Recruitment: candidate emails (`bf_recruitment_mail`)

`auto_install` bridge between `bf_recruitment` and `bluefox_branding`. It
replaces the four email templates `hr_recruitment` sends to candidates. Nothing
else: no model, no field, no view.

## The findings, measured by actually receiving the messages

End-to-end QA, emails read as they arrived in a real mailbox.

| What was wrong | Effect on the candidate |
|---|---|
| **All four templates carry the SAME subject** ("Your Job Application: \<job\>") | In their inbox, the acknowledgement, the invitation and the refusal look alike. They do not know what they are opening |
| **The WITHDRAWAL message does not say what it is about** | Sent when the candidate withdraws, its entire body is "We would like to thank you for your interest and your time." It does not even acknowledge the withdrawal |
| **The refusal promises to keep the CV** "for future opportunities" | A promise that contradicts a retention schedule, and that few companies keep |
| **The acknowledgement opens with "Congratulations!"** for a mere submission | False hope, then an empty job description and a "What is the next step?" that answers something else |
| **The recruiter's internal address is published** to the candidate | A personal address goes out to every candidate for the job |
| **No sender is set** | Odoo falls back to `odoobot@example.com`, which relays reject (550, sender address rejected) |

## What the bridge changes

1. **One subject per intent.** Receipt, invitation, refusal and withdrawal no
   longer look alike in an inbox.
2. **One intent per message.** The withdrawal acknowledges the withdrawal. The
   refusal keeps what core did well, the decision stated early, and a test
   checks that it comes **before** the thanks. It drops the promise to keep the
   CV, which contradicts a retention schedule.
3. **The message follows the real journey.** Someone who sat an interview is not
   thanked like someone who did not: the bridge reads `held_interview_count`,
   which the interview book already maintains.
4. **Rights are named**, not implied: the right to see what was recorded,
   including interview appraisals, and the fact that the decision was taken by a
   person and not by an automated sort.
5. **The company's brand**, through `bluefox_branding.bf_mail_layout`: logo
   header, accent rule, footer with the contact details. Everything is read from
   `res.company`, so each tenant gets its own.
6. **A sender that passes**: the company's address, not `odoobot`.

⚠️ **The recruiter's address no longer goes out, their name does.** Writing to a
named person beats writing to an anonymous team. The return details are the
company's, and the reply comes back onto the application through the job's
alias: `reply_to` is untouched.

⚠️ **The recorded refusal reason is NOT sent unprompted.** The interview book
requires a written reason after an interview was held, drafted to be read by the
person evaluated. Sending it without their asking would be a different act: the
email tells them they can obtain it, and waits for the request.

⚠️ **The refusal template also serves the "Duplicate" and "Spam" reasons.** Its
text therefore stays neutral about the cause. A tenant who wants another tone
for those cases creates a refusal reason with its own template.

## 🔴 The trap that silently cancelled the whole module

On a tenant installed in French, the four templates carry an `fr_CA` value
translated from the original English. **Rewriting the field in a data file only
touches the SOURCE (`en_US`)**: the `fr_CA` value survives, and it is the one
that gets rendered.

The module installs, the XML ids are correctly reused, the logs say nothing, and
the candidate receives **exactly what they received before**. Observed in the
wild: `subject->>'en_US'` carried the new text, `subject->>'fr_CA'` the old one,
and the instance runs in `fr_CA`.

`hooks.py` therefore removes the other languages from `subject` and `body_html`,
at install and at upgrade. The fix is to **remove** translations, not to add
them: the text this module writes is French, and it is the source.

## 🔴 The second silent no-op, worse than the first

The `hr_recruitment` templates are declared `noupdate="1"` in their own module.
Odoo keeps that flag on `ir_model_data` and then **refuses any rewrite during an
upgrade**, including from another module's data file. This module therefore
wrote its templates **at install, once**, and every later correction was ignored
without a word.

A `pre-migrate` hook lifts the flag **before** the data file loads. Order
matters: a `post-migrate` would arrive after the rewrite was already ignored. A
test reads `ir_model_data.noupdate` and fails if the flag comes back.

⚠️ **Accepted trade-off**: upgrading `hr_recruitment` ALONE would reapply Odoo's
texts. Since this module loads after it in the graph, an upgrade that takes both
ends on our texts. Upgrade this module after any Odoo version bump.

## Localisation

⚠️ As long as no `.po` ships with the module, an English-speaking tenant
receives the French text. That is a translation to supply, not a defect to work
around here.

## Test evidence

The suite covers what actually failed:

* the four subjects are distinct, and each one names the job;
* the refusal contains the decision, and it precedes the thanks;
* the withdrawal acknowledges the withdrawal instead of thanking into the void;
* no body contains the recruiter's internal address, and all four leave from the
  company's address;
* the recruiter's name is there, their address is not;
* the refusal adapts to whether an interview took place;
* all four carry `email_layout_xmlid`, without which the body goes out bare;
* the retention and right-of-access paragraph is present;
* **no stale translation survives** on `subject` or `body_html`.

Also proven for real: messages sent, received and re-read, logo header and brand
footer rendered.
