# Floor Plans: Hosting (`bf_floorplan_hosting`)

Bridge between "Floor Plans" and "Hosting Management". An element on the plan
can stand for a fleet endpoint (`hosting.endpoint`) or a server
(`hosting.server`).

Odoo 18.0 Community. LGPL-3. Installs itself when both modules are present.

* Clicking the shape opens the device's form rather than the element's, when
  the user is allowed to read it. Without that right, the shape still shows
  the facts (name, state, operating system) like a notice board, and the click
  opens the element.
* The plan colours what needs attention: end-of-life operating system, device
  under repair or retired, decommissioned server (red); expired warranty,
  server under maintenance (amber).
* The tooltip says who has the device, its system and its state.
* From the endpoint's or the server's form, "On the plan" jumps to its place,
  or offers to choose one if it has none yet.
* A device is placed in one spot only (unique constraint).

The plan stores no fact about the device: it reads the form, and the form
stays the truth.

The user interface is in French; the strings are translatable.
