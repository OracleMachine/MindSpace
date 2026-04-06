# Project Specification: Hierarchical Knowledge Agent (SPEC.MD)

## 1. Project Goal & Philosophy
Create an AI agent (Gemini CLI) that acts as a cognitive partner across three primary functions: **Thought Recording**, **Knowledge Base Management**, and **Research**. 

**Philosophy:** "Discord as the Input Stream, Filesystem as the Source of Truth."
The system uses **OpenViking** for context mapping and **PageIndex** for deep document reasoning, resulting in a human-readable, self-organizing filesystem backed by **Git** for full auditability.

---

## 2. Technical Stack
- **Engine:** Gemini CLI (Invoked in a conversational loop per message)
- **Language:** Python 3.12+
- **Knowledge Framework:** [OpenViking](https://github.com/volcengine/OpenViking) (Context mapping)
- **Indexing Framework:** [PageIndex](https://github.com/VectifyAI/PageIndex) (Deep document parsing)
- **Front-end:** Discord API (discord.py)
- **Version Control:** Git (One repository per Discord Server)

---

## 3. Architecture & Filesystem Mapping

### 3.1 The "One Server = One Repo" Rule
Each Discord Server maps to a unique root directory on the filesystem, which is initialized as a Git repository.
- **Root Directory:** `/{Server_Name}/` (e.g., `/Project_Alpha/`)

### 3.2 Hierarchy Logic (Macro-to-Micro)
The directory structure is divided into two zones:

#### The Human-Controlled Zone (Rigid)
- **Discord Channels:** Map 1:1 to top-level "Channel" folders (e.g., `#machine-learning` -> `/Project_Alpha/Machine_Learning/`).
- **Core Files:** Every Channel folder MUST contain:
    - `INDEX.MD`: The high-level context map for OpenViking.
    - `STREAM_OF_CONSCIOUS.MD`: The running log of distilled thoughts.

#### The AI-Controlled "Autonomous Zone" (Fluid)
- Inside each Channel folder, the Gemini CLI has absolute freedom to create deeply nested sub-folders based on semantic meaning (e.g., `/Machine_Learning/Neural_Networks/Transformers/`).

---

## 4. Functional Pillars

### 4.1 Thought Recorder (Active Dialogue)
- **Input:** Any regular text message in a Discord channel.
- **Action:** Gemini CLI acts as a conversational partner, replying to every message to keep the discussion flowing.
- **Extraction:** Simultaneously, the agent evaluates the dialogue, identifies critical insights, and **secretly appends** them to the channel's `STREAM_OF_CONSCIOUS.MD`.

### 4.2 Knowledge Base Manager (Ingestion)
- **Input:** Files (PDFs, images, etc.) or **Internet URLs** dropped into Discord.
- **Process for Files:** Download -> PageIndex parse -> Gemini semantic placement -> Git commit.
- **Process for Links:** Fetch content -> Convert to Markdown snapshot (`WEBPAGE-slug.md`) -> Ingest as a file.
- **Manual Sync:** Files moved manually onto the disk are detected and indexed whenever the `!organize` command is triggered.

### 4.3 Research Engine
- **Input:** Specific user instructions (triggered by `!research`).
- **Action:** Gemini uses OpenViking/PageIndex to synthesize data across the entire KB.
- **Output:** Structured articles with lineage citations (Local Path, Discord Ref, Viking URI).

---

## 5. Framework Synergy: PageIndex & OpenViking

- **PageIndex (The Reader):** Used for **content-level depth**. It parses documents into reasoning trees. Used during ingestion to "understand" files and during research to find "specific" facts.
- **OpenViking (The Librarian):** Used for **contextual mapping**. It manages the `viking://` URI space and provides "Abstract" (L0) and "Overview" (L1) layers so Gemini can navigate the tree without reading every file.

---

## 6. Core Workflows (Commands)

| Command | Action | Implementation Logic |
| :--- | :--- | :--- |
| **`!organize`** | Re-sync & Optimize | Scans for untracked files, performs semantic re-organization, and updates all `INDEX.MD` files. Triggers a Git commit. |
| **`!consolidate`** | Permanent Record | Synthesizes `STREAM_OF_CONSCIOUS.MD` into a permanent article, clears the stream, and files the article into the Autonomous Zone. Sends the file back to Discord. |
| **`!research [topic]`**| Knowledge Synthesis | Generates a deep-dive research paper based on the current KB context. Includes citations and sends the file back to Discord. |
| **`!global [query]`** | Cross-KB Search | Searches across all categories in the current server KB. |

---

## 7. Implementation Instructions (Prototyping Phase)

### 7.1 Simplicity & Isolation
- **Isolation:** Run one Python bot process per Discord Server.
- **Persistence:** Every "Active Command" (prefixed with `!`) MUST be followed by a `git commit` with an AI-generated message explaining the *intent* of the change.

### 7.2 Visibility & UX
- **No Noise:** The bot should be quiet in Discord. No ASCII maps or pinned messages needed.
- **Instant Access:** Whenever a new `.md` file is created, the bot MUST send the file back to the Discord channel as an attachment along with its local filesystem path.

### 7.3 Code Logic
- **The Loop:** Use `discord.py`'s `on_message`.
- **Gemini CLI:** Invoke via `subprocess.run(["gemini", ...])`.
- **Prompting:** Pass OpenViking context (L0/L1) to Gemini so it understands "where it is" in the knowledge tree before responding.
