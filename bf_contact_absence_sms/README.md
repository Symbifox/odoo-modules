# Contact absences in the SMS messenger (`bf_contact_absence_sms`)

The absence banner from the core module, in the header of an SMS conversation,
as soon as it is opened and before anything has been typed.

Nothing is blocked: texting someone on holiday is sometimes exactly what you
want.

## Why a separate module

The banner's template **inherits** the messenger's template. An OWL template
inheritance cannot be conditional: if the inherited module is absent, the whole
asset bundle falls over. So the dependency decides the split, not a preference
of form.

## Technical notes

* ⚠️ The messenger component is **not exported** by its module: the patch picks
  it up from the client-actions registry, where it registers itself.
* No new server method: the messenger calls
  `bf.partner.absence.hint_for_thread`, the core module's single public entry
  point.
* The banner reads `activeThread.partner_id`, which the thread payload already
  carries. A test guards that contract.
