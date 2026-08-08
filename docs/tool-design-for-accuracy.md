# Tool design for agent accuracy

*What we changed in the tool library and the measurement harness, why each change
mattered, and which lessons transfer to any tool-using agent.*

Everything below was found on a working system whose results had already been
written up. None of it was visible in the traces, the tests, or the aggregate
scores. That is the point of the document: this class of defect is invisible
precisely where you would look for it.

Numbers are from `results_w20`–`results_w23` on the 106-case golden dataset and
the 22-case §4.7 safety probe. Where a claim is inferred rather than measured,
it says so.

---

## Part 1 — Tools that do not do what they say

### The defect class: the silent no-op

A tool validates its arguments, returns `OK`, and writes nothing. The call
succeeds. The trace is clean. The only evidence is an absent `world_diff`, and
nothing was checking for one.

This is the worst failure shape available to an agent system, because a silent
no-op and a correct write produce **byte-identical successful calls**. No amount
of trace inspection distinguishes them. It survives code review because each
tool looks locally reasonable, and it survives testing because tests assert the
returned status rather than the resulting state.

What we found in a 500-tool library that had already produced published results:

| Defect | Scale |
|---|---|
| `manage_devices` — 20 tools, a real backing collection, **not one write** | whole domain |
| `purge_history` — the canonical destructive operation, named in the `forbidden_tools` of **all 106** golden cases; only validated its config | 1 tool, load-bearing |
| Tools named in `forbidden_tools` that could not do anything | **12 of 47** |
| `promote_to_environment` — forbidden in **104 of 106** cases, one of the two canonical high-risk deploy operations; its `run` did not even take the world | worst single case |
| `acknowledge_alarm` / `shelve_alarm` / `suppress_alarm` | silence a safety interlock indistinguishably from refusing to |

The consequence for the safety argument is direct: **a case that forbids a tool
which cannot act is asserting protection against a no-op.** For 104 of 106 cases
the headline safety constraint was unfalsifiable.

### How to detect it: run the tools, do not read them

Our first detector read each tool's source for a `world_diff=` keyword. It was
wrong in both directions:

- it **missed delegation** — `create_motor` → `_place_symbol` → `_place_widget`,
  which writes;
- it would have **counted a tool** that mentions the keyword on a branch it never
  takes.

Reading source tells you what a tool *says* it does. The working detector
(`scripts/audit_tool_mutations.py`) seeds a world, synthesizes arguments from
each tool's own Pydantic schema, dispatches, and records whether a `world_diff`
came back. Verdicts are `MUTATES` / `NO_DIFF` / `BLOCKED:<code>` /
`UNSYNTHESIZABLE`, and the last two are reported **separately** — a tool the
harness could not build arguments for is a limitation of the harness, not a
defect in the tool, and conflating them manufactures false findings.

Result over the catalogue: **83 of 500 atomics can be observed to mutate.** The
rest are genuine readers, tools in domains with no backing collection, or — a
category nobody had noticed — **201 synthesized filler tools** whose entire body
is `return ok()`, generated to pad the catalogue to a round number and
indistinguishable from real tools to the retrieval layer.

> **Transferable practice.** Add one behavioural test that asserts each mutating
> tool actually mutates. Make it a *test*, not a script: ours
> (`test_no_forbidden_tool_is_a_silent_no_op`) runs the audit and fails the suite
> if any tool the dataset forbids turns out to be harmless. A new inert tool then
> fails CI instead of waiting for a measurement wave to expose it.

### Make the tool's own claim checkable

Every tool in this system declares `intended_entities` — the entities the call is
meant to create or modify. That declaration is a machine-readable promise, and it
can be checked against behaviour:

> **a tool whose `intended_entities` names an entity must produce a `world_diff`
> for it.**

Two tools were claiming entities that could never exist: `set_point_archive`
declared `points.<tag>.archive` against a model with no such field, and
`purge_history` declared `histories.<tag>.data` where no data was ever stored.
Both now return `[]` or write for real. Tools that legitimately write nothing —
readers, exports, runtime commands like `reset_device` — declare `[]` and are
correct.

---

## Part 2 — Metadata the model never sees

The second defect class is subtler and cost more accuracy than the first: tool
documentation that exists, is correct, is carefully measured — and never reaches
the model.

### Find out what the model actually receives

Our planner renders a compact catalogue rather than full JSON schemas. It emitted
tool descriptions plus argument **names and types**, and dropped every
per-argument `Field(description=...)`.

The cost was concrete. `bind_point.property` documents the binding vocabulary
(`tank→level|temperature|pressure`, `pump→state|status|frequency`,
`button→command`) — text that had been measured as worth **+1.89pp** in an
earlier experiment. It reached the planner as:

```
- bind_point: Bind a SCADA point tag to a widget property.; 必填: page_id:string, widget_id:string, property:string, tag:string
```

On one case both the flat baseline and the plan tier built the page and both
pump widgets correctly, and the **sole** difference in the final state was the
binding property: the baseline wrote `state` (correct, read off the JSON schema
it receives), the plan tier wrote `running` (a guess). This also explained a
standing puzzle in our notes — the vocabulary had measured as helping the flat
baseline *more* than the plan tier, because the plan tier was never shown it.

Rendering field descriptions cost **+3% catalogue size**.

> **Transferable practice.** Print the exact tool description string your model
> receives and read it. Not the schema, not the source — the rendered prompt. Ours
> had been wrong for the entire life of the project and no test could have caught
> it, because every layer was individually correct.

### Constraints that schemas cannot express

Pydantic `model_validator` — cross-field rules — **do not appear in
`model_json_schema()`**. Neither do they appear in any derived catalogue.

`create_analog_alarm` requires at least one of `high_limit` / `low_limit`. The
schema shows two ordinary optional floats. The planner omitted both, the step was
rejected at compile, and the alarm entity was never created — taking every
dependent step with it. This was **32 of 48 compile drops**, and it explains the
single most common missing field in our failure analysis (`alarms.*.priority`,
absent 62 times: the alarm did not exist at all).

The fix is to state the rule where the model can read it — on the fields
themselves:

```python
high_limit: float | None = Field(
    default=None,
    description="High alarm limit. At least one of high_limit / low_limit is REQUIRED.",
)
```

Related: **numeric bounds were in the schema and being discarded** by the
renderer. One tool received `max_samples=5000` against an `le=1000` and was
dropped for violating a bound stated three characters from where its type was
rendered. Now renders `max_samples:integer(1..1000)`.

> **Transferable practice.** Enumerate every constraint your tools enforce, then
> ask of each: *can the model see this before it calls?* Type, enum, range,
> required-ness, and cross-field rules are four different mechanisms with four
> different visibilities. Only the first two survive a naive schema dump.

### Render closed sets whole, or not at all

Twice we truncated a value list to save tokens, and both times it was worse than
omitting it. A truncated enum presents a partial list as exhaustive, so the model
reads the missing values as illegal — a 4-value cut on an 8-value `device_type`
hid `valve` and produced "no such device type" for a valve request. The same trap
caught the binding vocabulary at a 160-character cap, which rendered `button→com…`
and lost `command`, the most-needed property in the dataset.

> **Transferable practice.** A closed set is documentation only when complete.
> Budget for the whole thing or leave it out.

### An example string is a vocabulary of one

`create_analog_alarm.id` documented itself with `e.g. 'alarm_temp_high_101'`.
The dataset's convention is `<TAG>_H` / `<TAG>_HH`. On every rep of two batch
cases the model copied the example's shape verbatim (`alarm_ft_200_high`), the
generated IDs matched nothing, and the alarms scored as absent. The example was
the only vocabulary the model had, and it taught the wrong one — a single
illustrative string in a schema carries as much weight as a documented
convention, because from the model's side there is no difference.

> **Transferable practice.** Audit every `e.g.` in your schemas against the
> identifiers your system actually expects. An example that contradicts the
> convention is worse than no example.

### The world snapshot is tool metadata too

Whatever summary of system state you show the model is subject to every trap in
this section. Ours omitted two collections outright (`histories`,
`deployments`) — and an entity in an invisible collection *does not exist* to a
planner whose only view is the summary: a request to reconfigure an existing
history config was answered "该对象不存在，请先创建" on every rep, for a world
whose one entity was that config. Rendering alarms as bare IDs (`al_a, al_b`)
had the same effect one level down: the planner could not tell they were the
temperature alarms the request referred to, and asked instead of acting.

> **Transferable practice.** Enumerate the collections and fields your
> state-summary renders the way you enumerate constraints: for each entity kind
> the model may be asked to reference, confirm the summary carries (a) its
> existence and (b) the field that makes it referable.

### Name arguments the way the model will guess

`create_page` takes `id` and `name`. Every *other* page tool — `create_widget`,
`bind_point`, `create_pump` — takes `page_id`. The planner generalised and wrote
`page_id`/`page_name` to the creator in **20 of 70** emissions; the step was
dropped for missing a required field it had in fact supplied, and every widget
that followed failed with `PAGE_NOT_FOUND`.

Two defensible responses: rename for consistency across the library, or repair
the shape at compile. We did the latter, narrowly — the repair fires only when the
qualified key is absent from the schema, the bare key is a real field, and that
field is missing. It can fix a spelling and can never invent intent. Replaying
archived failures through it recovers **22 of 80 compile drops**.

> **Transferable practice.** Argument naming is a *library-wide* design decision.
> Local consistency inside one tool is not enough; the model generalises across
> the catalogue it is shown.

---

## Part 3 — The measurement will lie to you too

Fixing tools is worthless if the instrument cannot see the change. Three of our
harness defects were more consequential than any single tool bug.

### A null from an instrument that cannot register the quantity is not evidence

We measured a safety feature as a "measured negative" — +0.61pp, p = 1.00 — and
filed it as not worth shipping. The verdict was wrong, and the instrument was
why: **19 of the probe's 22 cases could not mutate the world at all.** Both arms
preserved the world trivially, the comparison ceilinged at 91.8%, and the feature
was denying operations in domains where nothing could be destroyed. Its 15
denials bought **zero prevented mutations**.

Rebuilt on tools that can actually write, the same comparison reads **+11.50pp,
p = 0.031, 6 cases better and 0 worse**. The feature now ships.

> **Transferable practice.** Before believing a null, demonstrate that the
> instrument can register the effect at all. A positive control belongs in every
> benchmark: a case you *know* should fail, which fails.

### Choose a metric that can vary

Our probe scored `task_success`, a boolean "was the world preserved". But the
budget under test permits three destructive operations before denying, so the
first three always execute — making the predicate **unsatisfiable by
construction**. An arm that saved one entity of four and an arm that saved none
both scored `False`.

The metric that works is continuous: *preservation rate*, surviving protected
paths over total. Same traces, same runs — the boolean reads +0.00pp with a CI of
[0, 0], and the rate reads +11.50pp at p = 0.031.

> **Transferable practice.** If your metric cannot distinguish partial success
> from total failure, and partial success is the expected outcome, the metric is
> the experiment's weakest component.

### A case must be satisfiable, and identical twins must be matchable

Two more unfalsifiable-case shapes surfaced after the probe rebuild, both in the
main benchmark:

- One case expected a priority *change* on an alarm it also listed under
  "must remain unchanged". Satisfying either side violated the other. It went
  unnoticed exactly as long as the tool under test was a silent no-op — the
  moment `set_alarm_priority` actually wrote, every arm started failing the
  case. The checker now exempts changes the case itself demands.
- Our ID-tolerant matcher required a *unique* candidate entity before aliasing
  a generated ID. A case expecting two pump widgets distinguished by nothing
  but their IDs, against a model that created exactly two pumps, matched
  *neither* — each expected entity was "ambiguous" between the two actuals.
  Identical siblings made the case unfalsifiable for every arm on every rep.
  Since any candidate satisfies every expected key field exactly, any injective
  assignment is semantically valid; the matcher now assigns deterministically
  and consumes the target, so an under-supplied world still fails.

> **Transferable practice.** Two adversarial checks for benchmark authors: for
> every case, exhibit at least one world state that scores `True` (golden-043
> had none); and if a case expects N interchangeable entities, verify a correct
> run with N generated IDs actually passes your matcher.

### Never drop data silently

Two harness behaviours quietly deleted evidence:

- **A decision counted as a breakage.** A run stopped by the safety policy was
  classified as a *technical* failure, retried, and dropped from the completed
  set — the cage working looked like the harness breaking. Worse, the retry logic
  keeps the first attempt that "succeeds", which is **selective resampling**:
  retry a legitimate bad outcome and keep the winner, and scores drift upward. We
  now treat only an unusable trace (no terminal state) as technical.
- **A dead rep reported as a rep.** One arm's third repetition had 102 of 106 runs
  never start. The comparison tool dropped them silently and still printed
  "reps=3", overstating precision on one side of a paired comparison. It now
  prints every dropped run.

> **Transferable practice.** Any filter in a scoring path must report what it
> removed. Silent exclusion is indistinguishable from clean data.

---

## Checklist

1. Run every tool against a seeded world; assert the ones that claim to mutate do.
2. Make `intended_entities` (or your equivalent) a checked promise, not a comment.
3. Print the rendered tool catalogue and read it as the model receives it.
4. List every constraint you enforce and confirm each is visible pre-call —
   especially cross-field validators, which no schema exports.
5. Render closed value sets complete or omit them.
6. Name arguments consistently across the whole library, and repair shape
   mismatches at compile rather than dropping the step.
7. Audit every `e.g.` string in your schemas against the conventions you expect;
   an example is a vocabulary of one.
8. Apply 3–5 to your state summary as well: every referable entity kind needs
   its existence *and* its referring field rendered.
9. Include a positive control in every benchmark, and for every case exhibit a
   world state that scores `True`.
10. If a case expects N interchangeable entities, verify a correct run with N
    generated IDs passes your matcher.
11. Choose metrics that vary over the outcomes you expect.
12. Report every run a filter removes.

---

## What this did and did not buy

Stated plainly, because the honest version is more useful than the flattering one.

The tool fixes **raised the flat baseline more than the architecture** — the
baseline calls those tools directly, while the plan tier routes around them — and
in doing so erased a previously reported architecture advantage. The safety
result survived (+18.18pp on no-act cases, p = 0.024 on one model); the aggregate
capability advantage did not, falling from +8.33pp to +2.83pp and losing
significance.

That is the correct outcome. The earlier margin was partly an artifact of tools
that did nothing, and a benchmark measuring a broken library measures the
library. The remaining catalogue-visibility fixes are architecture-side and lift
the plan tier specifically, but at one to two percentage points each — below what
a 106-case benchmark can resolve.

The generalisable finding is about *when* this work pays: on a weaker base model
the architecture's safety margin was large and significant; on a stronger one it
shrank into the noise, with the provider's own filter refusing two of the most
dangerous cases before the agent was invoked at all.
