"""The evidence the current dataset supports, shared by tools and workflows.

Schema validation lives in validation.py; this module describes interpretation.
It does not infer new fields or change stored records.
"""

import json

HISTORICAL_FIELDS = ("Status", "Assigned Sprint ID", "Blocker Note")
CURRENT_ONLY_FIELDS = (
    "Title",
    "Owner",
    "Priority",
    "Due Date",
    "Depends On Task IDs",
    "Planned Sprint ID",
    "Status Since",
)
NOT_RECORDED = (
    "Guaranteed blocker-resolution dates",
    "Effort or capacity measurements",
    "A calculated critical path",
    "Exact completion timestamps",
)
CURRENT_ATTRIBUTE_SOURCE = (
    "Current-only values displayed on historical tool rows are joined from the current "
    "Tasks record, not measured at each snapshot date. Repeated owner labels on dated "
    "rows are not historical ownership evidence. Report the current owner only; do not "
    "say that snapshots recorded the same owner across dates."
)
DATA_LIMITATIONS = (
    "Status, assigned sprint, and blocker notes are historical fields at recorded snapshot dates; their observed changes can be queried.",
    "Owner, priority, due date, title, dependencies, planned sprint, and Status Since are current attributes; their historical changes are unavailable.",
    CURRENT_ATTRIBUTE_SOURCE,
    "Snapshots establish observed states, not continuous status or inactivity between observations. A first observed Done date is not an exact completion timestamp.",
    "Evaluate overdue tasks against the reporting cutoff: a task is overdue only when incomplete and its current due date is strictly before that cutoff. A task due on the cutoff is due that day, not overdue.",
    "Assigned Sprint ID records historical membership; a blank assignment means backlog. Observation Sprint ID identifies the capture period, not task membership. Current Sprint ID is the current assignment, and Planned Sprint ID is the original plan. Do not interchange these counts.",
    "Only recorded dependency IDs establish task relationships. Similar blocker wording does not confirm a dependency, shared root cause, or provider responsibility. No critical-path schedule, effort/capacity measurements, or guaranteed resolution dates are recorded.",
    "Keep recorded facts separate from hypotheses and proposed actions. Label unverified interpretations and recommendations; do not present them as confirmed facts. Preserve the configured project identifier exactly.",
    "A field missing from one returned page does not mean it is not tracked. Respect the recorded coverage, filters, and pagination before declaring information unavailable.",
)
ANSWER_GUIDANCE = """ANSWER GROUNDING:
- Recorded facts must come from retrieved observations and their stated scope.
- If adding an interpretation, introduce it as "Possible explanation" or "Hypothesis"
  and state what is unconfirmed. Do not promote a hypothesis to a recorded fact.
- If suggesting an action, introduce it as "Suggested next step" or "Proposed action".
  Do not imply the action is assigned, executed, approved, or guaranteed to work.
An observed status and a tool-calculated overdue/risk flag are supported findings.
A forecast such as "at risk of becoming overdue" or "may delay launch" is an
interpretation, not a recorded status. Label it as a possibility, not an observed fact.
Keep simple factual answers short. Omit interpretation/action sections when not
needed; do not force every answer into a three-part template.
Illustrative wording only, not additional project evidence:
  Fact: "The receipt task is blocked pending provider configuration."
  Hypothesis: "The webhook issues may be related; the records do not confirm a shared cause."
  Proposed action: "Ask the owners to confirm whether the issues are connected."
Use depends_on_task_ids for recorded current dependencies. Shared wording or a
shared upstream task does not prove a direct dependency between two tasks, a shared
root cause, or that one fix will resolve both. Do not guarantee an action's effect.
"""


def data_contract() -> dict:
    """Return fresh serializable field lists for tool evidence and model requests."""
    return {
        "historical_fields": list(HISTORICAL_FIELDS),
        "current_only_fields": list(CURRENT_ONLY_FIELDS),
        "not_recorded": list(NOT_RECORDED),
        "current_attribute_source": CURRENT_ATTRIBUTE_SOURCE,
    }


def contract_instructions() -> str:
    """Render the same contract for Q&A and report narrative generation."""
    return (
        "DATA CONTRACT — authoritative description of the available records:\n"
        + json.dumps(data_contract())
        + "\n"
        + "\n".join(DATA_LIMITATIONS)
        + "\n"
        + ANSWER_GUIDANCE
    )
