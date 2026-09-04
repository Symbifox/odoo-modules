# Recruitment: privacy bridge (`bf_recruitment_privacy`)

The interview book knows who evaluated whom, against which scorecard, and what
was written. It does not know how long it is allowed to know it. This bridge
tells it, and makes sure the destruction you announce actually happens.

`auto_install`: the bridge installs by itself when both `bf_recruitment` and
`privacy_consent` are present, and the interview book works perfectly without
it.

## What it declares

**A purpose**, "Evaluating an application", which was missing. With no consent
to ask for: reviewing an application is the performance of the very step the
person took themselves. A consent that would stop the review if refused is not a
consent.

**A retention rule**, `RH-REC-1` "Unsuccessful applications": 3 active years,
2 semi-active, aligned on the employee-records schedule. One single duration
regime for the whole recruitment file, instead of two calendars to keep in
agreement. The rule stays distinct from the employee schedule despite the
identical duration, because a refused candidate is not an employee: neither the
purpose nor the legal basis is the same, and a campaign must be able to target
applications without touching employee records.

**Three classifiable models**: `hr.candidate`, `hr.applicant` and
`bf.interview`. Neither `bf.interview.rating` nor the scorecards are part of it,
and the reasoning is in `models/privacy_document_classification.py`.

**Classification at closing.** A rule that applies to nothing retains nothing:
the campaign sweeps `privacy.document.classification`, and nobody will ever
classify applications one by one. The bridge does it at the only moment that
makes sense, the closing, because that is where the duration starts running.

**The switch.** When the person is hired, the classification moves under the
employee-records schedule and follows the employee file. One regime at a time,
never two.

## The aggregate, written BEFORE destruction

`bf.interview.aggregate` keeps, per scorecard, per job and per year: the number
of sessions, of people evaluated, of raters and of ratings, the mean score and
its standard deviation, and the breakdown per criterion. **No name, neither
candidate nor rater.** It survives the destruction of the sessions.

Two figures carry the whole point of the model:

* `score_stddev` per criterion: close to zero, the criterion does not separate
  candidates. Everybody scores the same on it, so it measures nothing.
* `rater_spread_mean`: the gap between the highest and the lowest score within
  one session. High, and the criterion is not understood the same way by
  everyone; its wording needs review.

🔴 **The bridge imposes the order.** A campaign that tries to destroy an
application whose interview year has not yet been aggregated **raises**. It
destroys nothing and it registers nothing. Aggregate first, destroy second: once
the ratings are gone, the measurement cannot be reconstituted.

The aggregation cron ships **switched on**. It destroys nothing, it computes a
nameless measurement. Shipping it off would guarantee that one day a campaign
hits a wall — or worse, that somebody disables the guard to get past it.

## The three traps this bridge had to fix

### 🔴 The generic campaign would have ARCHIVED while certifying deletion

In `privacy_consent`,
`privacy.destruction.campaign.line._execute_destruction()` handles "Deletion"
like this: if the model carries an `active` field, it archives instead of
deleting. `hr.applicant` carries `active`, and so does `hr.candidate`. A
campaign would therefore have archived the application, CV intact, readable in
two clicks by anyone who knows how to tick "Archived", while `action_execute`
wrote an entry saying "Deletion" into the **immutable** register. The register
refuses `write` and `unlink`: a single campaign would have left a false and
permanent certification.

And failing does not stop the certification: the register entry is created after
the call, without re-reading the state that call has just written. **Raising is
the only way to prevent a certification.**

### 🔴 The person is not inside the application

Odoo 18 separated `hr.candidate` (the person) from `hr.applicant` (one
application). `partner_name`, `email_from`, `partner_phone` and
`linkedin_profile` are **related** fields carried by the person. Destroying the
application alone therefore destroys almost no personal information. The bridge
takes the person along with their **last** application; as long as another one
remains, the person survives.

⚠️ `hr_applicant.candidate_id` is `ON DELETE RESTRICT`: the database refuses to
delete a person who still carries an application. That is why destroying a
person goes through their applications, one at a time.

### 🔴 The SQL cascade skips the ORM and leaves the files behind

`bf_interview.applicant_id` is `ON DELETE CASCADE` in the database: deleting the
application wipes the sessions and the ratings **without** `unlink()` ever being
called. Yet `unlink()` is what sweeps
`ir_attachment WHERE res_model=… AND res_id IN …` and the `mail.message` records.
Panel notes and message threads would have stayed in the database and in the
filestore, orphaned and unfindable, while the register attested to their
destruction. The bridge deletes the sessions **through the ORM first**, then the
application, then the person.

## The chain of bridges

⚠️ Several bridges override `_execute_destruction`. This only composes because
each one **relays to `super()`** for the models it does not own. A bridge that
forgot to relay would silence the other guards in complete silence, and no test
that loads a single bridge would ever see it. `test_override_relays_to_super`
exists for that.

## What the bridge does not do

* It does not anonymise. The name is in the CV, in the message thread and in the
  interview comments, written out in full. Removing the identity fields would
  leave all the rest.
* It does not touch the file of a person who was hired. That belongs to the
  employee-records schedule and is destroyed with the employee file, not here. A
  campaign targeting an application whose person was hired **raises**.

## Test evidence

* **Green suite** on a database reinstalled from empty, with and without demo
  data.
* **Declared columns against `information_schema`: 0 missing.**
* **Views loaded under real accounts**, recruiter and panel member, not uid 1,
  which holds no group. The "Scorecard measurement" menu is visible to the
  recruiter and hidden from the panel member.
* **`base.TestInvisibleField`** from core: no unjustified always-invisible field.
* **Two mutations** proving the guards discriminate:
  * override neutralised (everything relayed to `super()`): **7 tests out of 19
    fall**, including `test_campaign_destroys_for_real` with the application
    ARCHIVED and a register entry certifying its deletion;
  * `interviews.unlink()` removed, SQL cascade left to run:
    `test_attachments_and_messages_go_too` falls on an orphaned attachment left
    on `bf.interview`.

## The contact left behind

🔴 **After a destruction CERTIFIED in the register, the `res.partner` created by
the recruitment flow stayed ACTIVE**, with the person's name and email address,
with nothing left to attach it to. The destruction therefore attested to more
than it had done.

⚠️ **The rule adopted**: the contact is **destroyed if nothing references it**,
**archived otherwise**. A contact that serves elsewhere (a customer, a supplier,
the parent of another contact) is not ours to remove.

### Counting references in the CATALOG, not in the ORM registry

The module reads `pg_constraint`: every foreign key that points at
`res_partner`, then one existence probe per column. This bridge has already paid
once for the divergence between the database and the models
(`bf_interview.applicant_id` is `ON DELETE CASCADE`, which no field declares). A
"nothing references it" computed from declared fields would be wrong in exactly
the same way, and it would be wrong **while attesting**.

⚠️ The contact's own row is excluded: `res_partner.commercial_partner_id` equals
its own id for a contact with no company. Without that exclusion no contact
would ever be an orphan, and a mutation proves it.

### 🔴 The module was holding itself back

`privacy.document.classification.subject_partner_id` points at the person, and
the classification **survives** destruction: it is only deactivated. The contact
was therefore systematically "referenced", therefore systematically archived,
and **never destroyed**. The guard would have looked like it was working.

A classification whose record no longer exists now releases its subject. The
attestation loses nothing: `privacy.destruction.register` carries `res_name` and
`subject_count`, text and a number, and **no key to `res.partner`**.

⚠️ **Accepted limit.** `hr.candidate` gets its contact through
`res.partner.find_or_create(email)`: a contact that already existed and that
nothing else references will be destroyed, even though the recruitment flow did
not create it. That is the rule as adopted, and a contact with no reference at
all carries nothing but a name and an address there is no longer any reason to
keep.

⚠️ If the framework refuses the deletion for a reason written in Python (an
`@api.ondelete` from another module), the bridge falls back to archiving and
says so in the log, rather than taking the whole campaign down with it.
