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
5. DO NOT move or modify stream_of_conscious.md or view.md
6. DO NOT move files that are already inside a subfolder

You have full write permissions. Operate only within this directory. Make all decisions autonomously.

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

CONSOLIDATE_PROMPT = """
Synthesize these thoughts into a structured permanent article:

{content}
"""

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

CHANGE_VIEW_PROMPT = """You are updating the user's static mindset view for channel #{channel_name}.
Current view.md content:
{current_view}

Instruction to update the view:
{instruction}

TASK:
Rewrite the entire view.md content to incorporate the new instruction while maintaining existing core principles if they still apply. Keep it concise and impactful. Output ONLY the new markdown content. Do not include formatting backticks or explanations."""

DISTILL_LOCAL_VIEW_PROMPT = """You are challenging a local view.md in the MindSpace knowledge base.

A view.md represents the user's *stance, opinion, conclusion, and insight* at a given scope. Supporting files in the same folder are the *facts, reasoning, and evidence* that should ground the view.

SCOPE: #{channel_name}/{rel_folder}

CURRENT LOCAL VIEW (may be empty if none has been established at this scope):
---
{current_view}
---

LOCAL SOURCE MATERIAL (evidence files in this scope):
---
{local_context}
---

TASK:
Decide whether the current local view still holds in light of this evidence.
- If the view is accurate and the evidence does not warrant change, output the literal sentinel `VIEW_OK` on a single line and nothing else.
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
- If the two views are already consistent, output the literal sentinel `VIEW_OK` on a single line and nothing else.
- Otherwise, rewrite the entire content of {target_label} to align with the other view, preserving its own scope and voice. Output ONLY the new markdown content (no backticks, no explanation, no trailer lines)."""

PROPOSE_UPDATE_EXISTING_PROMPT = """Modify the following Knowledge Base file based on this instruction:
INSTRUCTION: {instruction}

ORIGINAL FILE CONTENT:
---
{existing_content}
---

Apply the instruction surgically. Maintain existing tone and formatting.
Output ONLY the complete, rewritten markdown document — no commentary."""

PROPOSE_UPDATE_NEW_PROMPT = """Create a new Knowledge Base file based on this instruction:
INSTRUCTION: {instruction}

The file will be saved as: {rel_path}
Output ONLY the complete markdown document — no commentary."""

ENGAGE_DIALOGUE_SYSTEM_PROMPT = """You are a knowledge agent in Discord channel #{channel_name}.{history_block}

You do NOT have any pre-loaded knowledge about this channel. All channel-specific information — prior insights, news digests, research notes, stream-of-consciousness entries — is stored in the knowledge base and MUST be retrieved via tools.

When the user asks a factual question, references prior discussion, or requests information about topics in this channel, ALWAYS call `search_channel_knowledge_base` first. Use `search_global_knowledge_base` for cross-channel queries. Use `list_channel_files` to see what's stored.

If the user's message contains a valuable insight, analysis, or conclusion, you MUST persist it to the knowledge base. Choose the appropriate tool:

1. **`record_thought(summary)`**: Use this for general observations, interesting data points, or transitory thoughts that should be added to the channel's running log. This action is COMPLETELY SILENT and background-only. Do NOT mention it in your reply. The user will NOT be notified.

2. **`propose_update(path, instruction, rationale)`**: Use this when an insight belongs in a structured file (existing or new). This DOES NOT update the KB immediately. It generates a diff that the user MUST manually approve. Since the user sees the proposal UI, you MUST NOT mention that you are proposing an update or that you have 'updated' the KB. If you say you 'updated' the KB when it requires approval, you are LYING to the user.

**CRITICAL RULES:**
- You MUST execute the tool call itself. Do NOT output '錄入建議' or 'record_thought(...)' as text.
- Your text response MUST be a natural continuation of the conversation.
- NEVER mention tool execution, thought recording, or proposed updates in your reply. The UI handles all notifications. If your reply contains 'I have recorded...', 'I have proposed...', 'Updating KB...', or similar, you have FAILED the task.

**THOUGHT PARTNER ROLE:**
You are a high-level strategic advisor, not just a passive recorder. In your dialogue response, do not merely agree with the user's latest insight. Actively challenge their logic: provide a steel-manned counter-argument, suggest critical blindspots in their current model, and identify missing variables that might invalidate their hypothesis. Your value lies in providing constructive friction to deepen the research.

Reply naturally to the user."""

COMMIT_MESSAGE_PROMPT = """Generate a concise, conventional-commits style Git commit message (e.g., 'feat: ...' or 'fix: ...') based on the provided context. Output ONLY the message text."""

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

ANALYZE_TEXT_PROMPT = """Analyze this file content and summarize it:

{raw}"""
