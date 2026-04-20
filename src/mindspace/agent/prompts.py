"""
Centralized prompt templates for MindSpace Agent and Services.
"""

ORGANIZE_PROMPT = """
You are organizing the MindSpace knowledge base channel folder for #{channel_name}.
Your current working directory IS this channel folder — all paths are relative to it.

UNTRACKED FILES TO ORGANIZE:
{untracked_files}

TASK:
1. Read each file's name and content to understand its topic
2. Determine the most semantically appropriate subfolder within this channel (e.g., Research/, Notes/, Articles/)
3. Create subfolders as needed
4. Move each file to its determined location

CONSTRAINTS:
- Never move or modify stream_of_conscious.md or view.md
- Never move files that are already inside a subfolder
- Operate only within this directory; do not reach into parent or sibling paths

You have full write permissions. Make all decisions autonomously.

WHEN DONE, output ONLY this markdown report (no other prose):
## Organize Report
**Summary:** [one sentence]

**Moves:**
- `<from>` → `<subfolder>/<file>`

**Created Folders:**
- `<folder>/`

**Skipped:**
- `<file>`: <reason>
"""

CONSOLIDATE_PROMPT = """Synthesize the raw stream-of-consciousness entries below into a permanent, structured article.

RAW ENTRIES:
{content}

TASK:
- Group related thoughts by theme; drop timestamps and conversational framing.
- Preserve concrete claims, numbers, and references verbatim where possible.
- Elevate recurring or cross-referenced ideas into their own sections.
- Resolve internal contradictions by noting both positions rather than silently picking one.
- No need for external citations — source material is the stream itself.

OUTPUT FORMAT (markdown, no extra prose outside this structure):
# <Topic-derived title>
*Consolidated from stream of consciousness.*

## Executive Summary
<3-5 sentences capturing the dominant thesis that emerged>

## <Theme 1>
<distilled content>

## <Theme 2>
<distilled content>

## Open Tensions
<any unresolved contradictions or questions surfaced by the stream>

Output ONLY the markdown document — no commentary, no code fences."""

RESEARCH_PROMPT = """You are performing deep research on the topic: "{topic}"

You have TWO sources of information:
1. The local knowledge base context below (extracted from channel #{channel_name}).
2. The web — use your search tool freely to find recent/authoritative sources.

LOCAL KB CONTEXT:
{combined_context}

TASK:
- Synthesize a thorough research report on the topic.
- Cross-reference local KB findings with fresh web sources.
- Cite every non-trivial claim: inline `[source: <url or KB path>]`.
- If KB context conflicts with web sources, flag the discrepancy explicitly.

OUTPUT FORMAT (markdown, no extra prose outside this structure):
# Research: {topic}

## Executive Summary
<3-5 sentence summary>

## Key Findings
- <finding 1> [source: ...]
- <finding 2> [source: ...]

## Detailed Analysis
<multi-paragraph analysis with inline citations>

## Conflicts & Open Questions
<any contradictions between KB and web, or unresolved questions>

## Sources
- <url or KB path>: <one-line description>
"""

OMNI_PROMPT = """
You are performing a cross-channel synthesis across the ENTIRE MindSpace knowledge base.

QUERY: "{query}"

GLOBAL KB CONTEXT (semantic search across all channels):
{global_context}

TASK:
- Answer the query comprehensively, drawing from ALL relevant channels.
- Use web search to supplement gaps or verify recency.
- Cite every claim: inline `[source: <channel/file> or <url>]`.
- Highlight cross-channel connections and themes.
- If sources from different channels or the web contradict each other, flag the discrepancy explicitly rather than silently picking one side.

OUTPUT FORMAT (markdown):
# Omni: {query}

## Summary
<concise answer, 3-5 sentences>

## Findings by Channel
### #<channel-name>
- <finding> [source: ...]

## Cross-Channel Connections
<how findings from different channels relate>

## Sources
- <path or url>: <one-line description>
"""

CHANGE_VIEW_PROMPT = """You are helping the user evolve their static mindset view for channel #{channel_name}. This is a thought-partner task, not a compliance rewrite. Your job is to make the view STRONGER — which usually means admitting where it was thin, silent, or over-confident, and letting counter-arguments actually land.

CURRENT VIEW:
{current_view}

USER INSTRUCTION:
{instruction}

REASONING (internal — do NOT include in output):
1. Where is the current view silent on something the instruction implies matters? That silence is a blind spot. Name it plainly.
2. If the instruction carries a counter-argument, steel-man it. If the current view cannot genuinely defeat it, the view must shift — do not hand-wave the tension.
3. The instruction is often a symptom of a deeper gap the user hasn't yet articulated. Try to surface that gap.

OUTPUT:
Rewrite the entire view.md so the new stance:
- Honestly absorbs the instruction and any counter-argument it carries.
- Explicitly notes the blind spot or missed dimension that was exposed, so future reasoning starts from a more complete map.
- Keeps prior principles only where they still survive scrutiny.
- Stays concise and impactful — this is a stance, not a literature review.

Output ONLY the new markdown content (no backticks, no commentary, no trailer lines)."""

DISTILL_LOCAL_VIEW_PROMPT = """You are challenging a local view.md in the MindSpace knowledge base.

A view.md represents the user's *stance, opinion, conclusion, and insight* at a given scope. Supporting files in the same folder are the *facts, reasoning, and evidence* that should ground the view.

SCOPE: {scope_label}

CURRENT LOCAL VIEW (may be empty if none has been established at this scope):
---
{current_view}
---

LOCAL SOURCE MATERIAL (evidence files in this scope):
---
{local_context}
---

TASK:
Decide whether the current local view still holds in light of this evidence. **Default to keeping the view.** Only rewrite when the evidence either directly contradicts a specific claim in the current view, or reveals a dimension the view is silent on that materially changes the stance. Stylistic phrasing differences, new supporting examples for an existing claim, or evidence that merely elaborates on a stance already held are NOT reasons to rewrite.

- If the view is accurate and the evidence does not meet the bar above, output the literal sentinel `VIEW_OK` on a single line and nothing else.
- Otherwise, rewrite the entire view.md content so the stance is consistent with the evidence. Keep it concise — it is an opinion, not a summary of the sources. Output ONLY the new markdown content (no backticks, no explanation, no trailer lines)."""

DETECT_VIEW_CONFLICT_PROMPT = """You are checking consistency between two view.md files in the MindSpace view hierarchy. A view expresses the user's stance at its scope. A parent view should remain consistent with each child view and vice versa.

PARENT SCOPE: {parent_scope}
PARENT VIEW:
---
{parent_view}
---

CHILD SCOPE: {child_scope}
CHILD VIEW:
---
{child_view}
---

You are being asked whether {target_label} should be updated.

TASK:
**Default: the views are consistent.** A child view legitimately specializes on its scope and will naturally say things the parent does not — that is not a conflict. Only flag an actual conflict when both views make contradictory claims about the SAME dimension (e.g., parent says "X is primary driver", child says "X is a minor factor").

- If the views are consistent (including legitimate specialization), output the literal sentinel `VIEW_OK` on a single line and nothing else.
- Otherwise, rewrite the entire content of {target_label} to resolve the specific contradiction, preserving its own scope and voice. Do not import content from the other view that is outside {target_label}'s scope. Output ONLY the new markdown content (no backticks, no explanation, no trailer lines)."""

PROPOSE_UPDATE_EXISTING_PROMPT = """Modify the following Knowledge Base file based on this instruction:
INSTRUCTION: {instruction}

ORIGINAL FILE CONTENT:
---
{existing_content}
---

Apply the instruction surgically:
- Preserve the existing heading structure, section order, and tone unless the instruction explicitly requires restructuring.
- Touch only the sections the instruction implicates; leave unrelated content verbatim.
- Do not re-summarize or re-title the document.

Output ONLY the complete, rewritten markdown document — no commentary."""

PROPOSE_UPDATE_NEW_PROMPT = """Create a new Knowledge Base file based on this instruction:
INSTRUCTION: {instruction}

The file will be saved as: {rel_path}

STRUCTURE:
- Start with a single H1 title derived from the instruction's topic (not the filename).
- No YAML front-matter, no code fences wrapping the document.
- Use H2/H3 sections where content warrants; otherwise plain prose is fine for short notes.
- Match scope to instruction — do not pad short asks into long articles.

Output ONLY the complete markdown document — no commentary."""

ENGAGE_DIALOGUE_SYSTEM_PROMPT = """You are a knowledge agent in Discord channel #{channel_name}.{history_block}

You do NOT have any pre-loaded knowledge about this channel. All channel-specific information — prior insights, news digests, research notes, stream-of-consciousness entries — is stored in the knowledge base and MUST be retrieved via tools.

When the user asks a factual question, references prior discussion, or requests information about topics in this channel, ALWAYS call `search_channel_knowledge_base` first. Use `search_global_knowledge_base` for cross-channel queries. Use `list_channel_files` to see what's stored.

If the conversation — whether from the user's message OR from your own synthesis in reply — contains a valuable insight, analysis, or conclusion, persist it to the knowledge base via one of these tools:

1. **`record_thought(summary)`**: appends a one-line entry to the channel's running stream-of-consciousness log. Use for organic observations, data points, or transitory thoughts the user did not explicitly ask to file.

2. **`propose_update(path, instruction, rationale)`**: opens a reviewed-proposal UI (unified diff with Apply / Discard / Refine buttons) for a structured KB file (existing or new). The user must approve before the file is written.

**ROUTING PRIORITY:**
- If the user's message contains an explicit instruction to save, store, integrate, file, archive, or otherwise persist content into the KB — common phrasings include "save this", "add to KB", "make a file", "write to <path>", "整合进知识库", "保存", "存入", "归档", "添加到知识库" and equivalents — call `propose_update`. This holds even when the content to save is your own prior reply (interpret "your last response", "上一条回复", etc., as the most recent AI message in history).
- Otherwise, after composing your reply re-read it: if it contains a synthesis, counter-argument, new framing, or strategic claim worth recovering in a future session and not already captured in the channel view or stream-of-consciousness, call `record_thought`.

**TOOL-INVOCATION HONESTY:**
- You MUST execute the tool call itself — do NOT print `record_thought(...)` or "錄入建議" as plain text in your reply.
- Do NOT claim a file has been updated when only `propose_update` was called (the change requires user approval first). Phrasings like "I've queued a proposal for X" or "I'll add this to the KB pending your review" are accurate; "I updated X" is not.
- Otherwise, briefly acknowledging what you did (e.g. "noted that as a thought" or "drafted a proposal for `Power_Sector/...`") is fine — it gives the user useful context.

**THOUGHT PARTNER ROLE:**
You are a high-level strategic advisor, not just a passive recorder. In your dialogue response, do not merely agree with the user's latest insight. Actively challenge their logic: provide a steel-manned counter-argument, suggest critical blindspots in their current model, and identify missing variables that might invalidate their hypothesis. Your value lies in providing constructive friction to deepen the research.

Reply naturally to the user."""

COMMIT_MESSAGE_PROMPT = """Generate a one-line Git commit message for a knowledge-base change (not a code change). The context below describes what files/folders were created, modified, or moved inside a Discord channel's KB folder.

FORMAT: `<type>(#<channel>): <concise summary>` where type is one of add|update|organize|consolidate|research|omni|view|ingest. Keep the whole line under 72 characters. Use present tense, imperative mood.

Output ONLY the message text — no body, no code fences, no trailing period."""

ROUTE_FILE_PROMPT = """You are organizing a file dropped into the Discord channel #{channel_name} of a knowledge base. Pick the correct subfolder within the channel folder and a content-based filename.

{advice_block}ORIGINAL FILENAME: {filename}
FILE EXTENSION: {ext}

CHANNEL FOLDER CONTENTS (relative paths, '/' = directory):
{tree_listing}

FILE CONTENT PREVIEW (may be empty for binary files):
---
{snippet}
---

RULES:
- subfolder is RELATIVE to the channel folder. Use '' (empty) for channel root.
- Prefer reusing an existing subfolder from the listing over inventing a new one.
- filename MUST preserve the original extension ({ext}).
- filename MUST NOT be 'view.md'.
- Follow the TYPE-DATE-SUBJECT convention where possible. Use kebab-case. Date format: YYYY-MM-DD. Example: NOTE-2026-04-14-grpo-vs-ppo.md
- Do NOT include path separators in the filename.
- If USER ADVICE is present, let it override content-based guesses.

Respond with STRICT JSON only — no prose, no code fences:
{{"subfolder": "...", "filename": "..."}}"""

PLAN_FILE_PROPOSAL_PROMPT = """A user dropped a markdown draft into Discord channel #{channel_name} and asked for a reviewed save. Decide whether to MERGE the draft into an existing KB file, or save it as a NEW file.

{advice_block}DROPPED DRAFT (first section):
---
{draft_content}
---

CHANNEL KB SEMANTIC CONTEXT (what's already indexed):
{kb_context}

CHANNEL FOLDER LAYOUT:
{tree_listing}

RULES:
- target_rel_path MUST NOT be 'view.md'.
- Pick mode='update' ONLY if there is a clearly-matching existing file whose topic overlaps the draft and where merging makes editorial sense.
- Otherwise pick mode='new'.
- target_rel_path is relative to the channel folder, must end in .md, and for 'new' should follow TYPE-DATE-SUBJECT kebab-case (e.g. NOTE-2026-04-14-topic.md) placed in an appropriate subfolder from the layout (or channel root if none fit).
- rationale is one short sentence, shown to the user.

Respond with STRICT JSON only — no prose, no code fences:
{{"mode": "new|update", "target_rel_path": "...", "rationale": "..."}}"""

MERGE_FILE_UPDATE_PROMPT = """MERGE TASK: A user dropped a markdown draft to update an existing KB file in channel #{channel_name}.

TARGET FILE: {target_rel_path}
USER ADVICE: {advice}

EXISTING CONTENT:
---
{existing_content}
---

DROPPED DRAFT:
---
{draft_content}
---

RULES:
- Merge all relevant information from the draft into the existing file.
- Maintain the existing file's tone and structure.
- If the target file is a poor fit for this draft, output ONLY the sentinel string 'NEW_FILE_INSTEAD' and nothing else.
- Otherwise, output ONLY the complete, merged markdown content."""

MERGE_FILE_NEW_PROMPT = """NEW FILE TASK: A user dropped a markdown draft into channel #{channel_name}.

TARGET PATH: {target_rel_path}
USER ADVICE: {advice}

DROPPED DRAFT:
---
{draft_content}
---

RULES:
- Polish the draft into a high-quality KB article.
- Ensure proper H1 title and structure.
- Output ONLY the complete markdown content."""

ANALYZE_PDF_PROMPT = """This PDF has been indexed. Its document tree structure:

{tree}

Provide a one-sentence description of the document's content and purpose."""

ANALYZE_TEXT_PROMPT = """Provide a 1-2 sentence description of this file's content and purpose, suitable as context for downstream reasoning. Plain prose only — no preamble, no bullet points, no "This file...".

FILE CONTENT:
{raw}"""


# --- Language-matching directive, auto-appended to every prompt above ---
# Single source of truth so the wording stays consistent across the whole
# prompt pack. The directive is applied via string concatenation at module
# load (below) — `.format()` calls on the prompts still work because the
# directive itself contains no format placeholders.
_LANGUAGE_MATCH_DIRECTIVE = """

**Output language:** Match the dominant language of the user's input and the surrounding content. If the majority is Chinese, output in Chinese; if primarily English, output in English. Fixed technical tokens required by this prompt — literal sentinels (e.g. `VIEW_OK`, `NEW_FILE_INSTEAD`), JSON keys, controlled-vocabulary values such as commit-type tags — are never translated."""

_CONTENT_PROMPTS = (
    "ORGANIZE_PROMPT", "CONSOLIDATE_PROMPT", "RESEARCH_PROMPT", "OMNI_PROMPT",
    "CHANGE_VIEW_PROMPT", "DISTILL_LOCAL_VIEW_PROMPT", "DETECT_VIEW_CONFLICT_PROMPT",
    "PROPOSE_UPDATE_EXISTING_PROMPT", "PROPOSE_UPDATE_NEW_PROMPT",
    "ENGAGE_DIALOGUE_SYSTEM_PROMPT", "COMMIT_MESSAGE_PROMPT",
    "ROUTE_FILE_PROMPT", "PLAN_FILE_PROPOSAL_PROMPT",
    "MERGE_FILE_UPDATE_PROMPT", "MERGE_FILE_NEW_PROMPT",
    "ANALYZE_PDF_PROMPT", "ANALYZE_TEXT_PROMPT",
)
for _name in _CONTENT_PROMPTS:
    globals()[_name] = globals()[_name] + _LANGUAGE_MATCH_DIRECTIVE
del _name
