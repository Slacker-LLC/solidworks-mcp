# Contributing

English | [简体中文](CONTRIBUTING.zh-CN.md)

## What the tests can and cannot tell you

The offline suite — `python -m unittest discover -s tests -p 'test_*.py'`, 225
checks — runs anywhere: unit conversion, selection marks, geometry helpers, the
tool contracts of every module, and the import guardrails (every module imports
as part of the package, no absolute same-level imports, the five tools reported
in issue #3 are still registered). CI runs it on Windows across Python 3.10–3.13
and imports `solidworks_mcp.server` as its own step, because the unit tests only
reach `sw_core` and stayed green while a broken entry point shipped once.

It cannot tell you that SOLIDWORKS accepts a call. The COM behaviour that
matters here does not mock faithfully, so anything touching geometry has to be
checked against a live session:

```powershell
.\.venv\Scripts\python.exe tests\live_p0_regression.py
```

A change to a feature or sketch tool is not done until it has run against a real
SOLIDWORKS with the result checked numerically — read the geometry back and
compare it to a closed-form value, rather than trusting the tool's own success
report. SOLIDWORKS returns null far more often than it raises.

## Check the type library before writing a tool

The published API reference disagrees with the shipped type library often enough
to matter: `FeatureFillet3` takes 14 arguments here, not the documented 7. Read
the signature off your own installation first:

```bash
python tools/tlb_probe.py methods '^IFeatureManager$' '^FeatureFillet3$'
python tools/tlb_probe.py enums '^swMateType_e$'
```

## Adding a tool

One decorated function; the registry does the rest.

```python
@tool("my_tool", "What it does. Lengths are millimetres.",
      {"depth_mm": {"type": "number"}}, ["depth_mm"])
def my_tool(args: dict[str, Any]) -> dict[str, Any]:
    _, doc = require_part()
    ...
    return result(True, "Did the thing.")
```

House rules that are not negotiable, because breaking them is how this server
silently lies to a model:

- **Units at the boundary.** Every length parameter ends in `_mm`, every angle
  in `_deg`. Convert once, in `sw_core`. No tool takes metres.
- **Take geometry as a `selection` object**, never as raw COM selection state.
  Let `sw_core` set the marks.
- **Rebuild and report `GetWhatsWrong`** after anything that changes the model.
  A tool that returns success without checking is worse than one that fails.
- **Judge success by the model, not the return value.** `SketchTrim` returns
  `False` on success; `SketchAddConstraints` silently ignores a string it does
  not recognise.
- **Never open a modal dialog.** One modal box blocks the COM call that opened
  it and deadlocks the whole server.

## Traps that cost real time

**SOLIDWORKS members are inconsistently exposed to pywin32.** Most are declared
as `PROPGET` *with arguments*, and late-bound pywin32 may resolve such a member
on plain attribute access by calling it with **no** arguments: `IBody2.GetFaces`
then returns a bound method that looks like a one-element result, and calling
`ModelDocExtension.AddDimension` that way **crashes SOLIDWORKS outright**.
`sw_core.flag_methods()` calls `_FlagAsMethod` on every member the server invokes
with arguments, and `sw_core.value()` invokes zero-argument callables instead of
returning the method object.

**Modal dialogs deadlock the server.** `AddDimension2` pops the "Modify" box
whenever *Input dimension value* is enabled — the default — and a modal dialog
blocks the COM call that opened it, and with it the whole server, until somebody
clicks. Dimension tools turn the preference off and restore it afterwards. If a
tool hangs, look for a dialog behind the SOLIDWORKS window.

**Return values lie.** `SketchTrim` returns `False` on success, the `Create*`
sketch APIs return arrays whose truthiness is unreliable, and
`SketchAddConstraints` silently ignores a string it does not recognise. Judge by
the model: segment counts, `GetWhatsWrong`, relation counts.

**Multi-reference features want distinct marks.** `InsertRefPlane` reads its
first, second and third reference from *marks 0, 1 and 2*, not from selection
order; selecting everything with mark 0 silently builds nothing, which is why
`create_plane` gives each reference its own mark.

**Two unit traps.** `IPartDoc.GetPartBox(False)` returns *document* units, so it
reads 1000x large if you treat it like the rest of the API; pass `True`. And
`swSketchLINE` is `0`, so folding a segment type through `or` turns every line
into "unknown".

## Why `mcp` is capped below 2.0

`mcp` 2.0 removed the low-level `Server.list_tools()` / `Server.call_tool()`
decorator API this server is built on, and renamed `Tool.inputSchema` to
`input_schema` (keeping the old name only as a serialisation alias). Importing
`solidworks_mcp.server` against 2.0 raises
`AttributeError: 'Server' object has no attribute 'list_tools'`, so the
dependency is pinned `<2` until someone ports it.

CI imports the server module as its own step for this reason. The unit tests
only reach `sw_core`, so they stayed green while the entry point was broken —
which is how this was missed the first time.

## Scope

This server attaches to a session the user already has open and calls the
documented API. It does not launch SOLIDWORKS, register an add-in, execute
arbitrary code, or touch the network, and a pull request that adds any of those
will not be merged — a `run_macro`-shaped tool would hand an MCP client
arbitrary code execution inside the user's CAD session.

## Language

Repository documents are English-first with Simplified Chinese versions. Keep
the README, coverage, contributing, release and license-note pairs in sync;
`AGENTS.md` carries both languages. Keep code, identifiers, commands and error
text unchanged.

## Branch protection, and who merges

`main` is protected:

- every change goes through a pull request, and the four `offline` CI jobs
  (Windows, Python 3.10–3.13) are required status checks;
- force pushes and branch deletion are blocked, and the rules apply to
  administrators too;
- all conversations have to be resolved before the merge button unlocks.

Automation — an agent, a bot, a script — may create the branch, push it and open
the pull request, but **the repository owner merges it**. That means automation
never merges, never pushes to `main`, and never edits or disables the protection.
See `AGENTS.md` for the same rule stated as instructions to an agent.

One caveat is worth knowing. `required_approving_review_count` is currently `0`
because the repository has a single account, and GitHub will not let an author
approve their own pull request — requiring an approval today would deadlock every
merge. To make "only the owner merges" a technical gate instead of a convention,
add a second account as a collaborator (or run automation under its own account)
and then raise the count to `1`. The required checks, the ban on force pushes and
the admin enforcement already apply either way.
