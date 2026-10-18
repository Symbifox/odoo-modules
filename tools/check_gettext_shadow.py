#!/usr/bin/env python3
"""Fail when a function both calls ``_()`` and binds the name ``_``.

In Odoo ``_`` is the translation function, imported at module level. Python
decides a name is local to a function if the function assigns it *anywhere*,
including after the point of use. So this:

    def action_upload(self):
        if not self.file_data:
            raise UserError(_("Select a file."))     # line 4
        ...
        guessed, _ = mimetypes.guess_type(self.file_name)   # line 12

raises ``UnboundLocalError`` on line 4 instead of showing the message. The user
gets a traceback where a sentence was intended, and no test that exercises the
happy path will ever notice. Found once in this repository, in
``bf_document_nextcloud_sync``, by exactly this check.

Ruff's F823 catches the narrow case it can prove. This catches the family:
tuple unpacking, ``for _ in``, ``with ... as _``, walrus, augmented assignment,
``except ... as _``, ``import ... as _``, a local ``def _``, and a ``_()`` called
only from a closure or a lambda.

One binding is not an offence: ``_ = self.env._`` (any ``<expr>._``) written
as a statement of the function body itself, before ANY read of ``_`` in it,
nested functions, lambdas and their default values included (a closure called
before the binding raises ``NameError``, a default ``UnboundLocalError``).
That is the Odoo 18 way to translate into the language of a record's own
environment, and it has to keep the name ``_``: Odoo extracts the terms to
translate from ``_()`` calls only. Flagged once, in ``bf_meeting``, where it
held the lint job, and so every test run behind it, red for a whole day.

    python3 tools/check_gettext_shadow.py [path ...]
"""

import ast
import pathlib
import sys


def bound_names(target):
    """Names a single assignment target actually binds.

    Only Name / Tuple / List / Starred bind. Descending into a Subscript would
    read ``held[_("x")] += 1`` as a binding of ``_`` when it is a *call* — five
    false positives out of six on the first attempt at this check.
    """
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from bound_names(element)
    elif isinstance(target, ast.Starred):
        yield from bound_names(target.value)


def body_nodes(func):
    """Every node inside ``func``, stopping at nested functions and lambdas.

    A nested scope binding ``_`` does not shadow the enclosing one.
    """
    stack = list(func.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def is_translator_rebind(node, func):
    """``_ = <expr>._`` as a direct statement of ``func``'s body.

    Only at the top of the body: inside an ``if`` the binding may not run, and
    a later ``_()`` would then raise exactly what this check exists to catch.
    """
    return (node in func.body
            and isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_")


def first_read(func):
    """The first line that reads ``_`` in ``func``'s body, nested scopes included.

    Unlike ``body_nodes``, this descends into nested functions and lambdas: a
    closure and a default value read the enclosing ``_``. Conservative: a nested
    function defined before the binding and called after it is flagged too.
    """
    lines = [node.lineno for stmt in func.body for node in ast.walk(stmt)
             if isinstance(node, ast.Name) and node.id == "_"
             and isinstance(node.ctx, ast.Load)]
    return min(lines) if lines else None


def offences(path: pathlib.Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as exc:
        yield path, 0, f"unparseable: {exc}"
        return
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for func in functions:
        call = bind = None
        rebinds_only = True
        for node in body_nodes(func):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "_"):
                call = node.lineno if call is None else min(call, node.lineno)
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor)):
                targets = [node.target]
            elif isinstance(node, ast.withitem) and node.optional_vars:
                targets = [node.optional_vars]
            elif isinstance(node, ast.NamedExpr):
                targets = [node.target]
            elif isinstance(node, ast.ExceptHandler) and node.name == "_":
                targets = [ast.Name(id="_")]
            elif (isinstance(node, (ast.Import, ast.ImportFrom))
                  and any((alias.asname or alias.name) == "_" for alias in node.names)):
                targets = [ast.Name(id="_")]
            elif (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and node.name == "_"):
                targets = [ast.Name(id="_")]
            for target in targets:
                if "_" in set(bound_names(target)):
                    bind = node.lineno if bind is None else min(bind, node.lineno)
                    rebinds_only = rebinds_only and is_translator_rebind(node, func)
        if bind and call is None:
            # `_()` called only from a closure or a lambda reads the enclosing `_`,
            # which this binding makes local: NameError when it runs first.
            nested = [n.lineno for stmt in func.body for n in ast.walk(stmt)
                      if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Name) and n.func.id == "_"]
            call = min(nested) if nested else None
        if call and bind and rebinds_only and bind < first_read(func):
            continue
        if call and bind:
            yield (path, func.lineno,
                   f"{func.name}() calls _() at line {call} and binds _ at line {bind}")


def main(argv):
    roots = [pathlib.Path(a) for a in argv[1:]] or [pathlib.Path(".")]
    found = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if "__pycache__" in path.parts:
                continue
            found.extend(offences(path))
    for path, line, message in found:
        print(f"{path}:{line}: {message}")
    if found:
        print(f"\n{len(found)} function(s) would raise UnboundLocalError instead of "
              f"showing their message.")
        return 1
    print("gettext shadowing: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
