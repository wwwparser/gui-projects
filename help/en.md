# Help — Project Manager

## Overview
Project Manager is a manager for a large number of projects in one folder.
It scans the projects root folder, shows everything in a table, and lets you
launch the Claude Code CLI or Codex CLI inside any project with one click.

Key features:
• A table of all projects with sorting and search
• AI project analysis via DeepSeek (what it is and what state it's in)
• Launching Claude / Codex inside the selected project
• Opening several projects as Windows Terminal tabs (presets)
• Pinning important projects to the top
• A task tracker with reminders
• A multilingual UI (5 languages) that switches instantly

Tip: press F1 anytime to open this help.

## Projects list
The "Projects" tab shows every folder in the projects root.

• Search — the field at the top filters by name, description and stack.
• Sorting — click a column header.
• Double-click a project — launches Claude Code.
• Right-click — a context menu with every action.

The "🔄 Scan (forced)" button rescans the folder ignoring the cache.
The "📂 Folder" button opens the projects root in Explorer.
The "📁 Data" button opens the folder with settings and the database (%APPDATA%).

## Pinning and order
Pinned projects always appear at the top of the list, highlighted in yellow.

• Pin / unpin — the "📌 Pin" button or the context menu.
• Reorder — drag a pinned project up or down with the mouse,
  or use Alt+↑ / Alt+↓.

The order is saved automatically and survives a restart.

## Launching Claude and Codex
Select a project and press "▶ Claude" or "▶ Codex" — a new terminal window
opens with the agent already running inside the project folder.

Launching uses desktop scripts (Claude-BypassProxy, Codex-BypassProxy) that
set up the environment and bypass the proxy.

"✨ New" creates a new project folder and launches an agent inside it.

## Terminal tabs and presets
The "🖥 Open in tabs" button opens a dialog for launching several projects
at once — each in its own Windows Terminal tab with its own color and title.

Workflow:
1. Mark the projects you need (context menu → "Mark for terminal").
2. Open the dialog, reorder and rename tabs if needed.
3. Press "🚀 Open".

A preset is a saved set of projects. Save one with "💾 Save as…" and next
time open the whole set with a single click — handy for a morning
"open everything I'm working on" routine.

A tab title can be set by double-clicking in the list, or via the main
table's context menu ("Terminal tab title…"). The title is also shown in
the "Tab title" column of the main table.

## Restore titles
After a /resume command, Claude rewrites the terminal tab title.
The "🏷 Restore titles" button puts the titles back.

The program finds the open terminal tabs, detects which project is in each
of them, and renames the tabs back to their configured titles. No preset
binding is required — detection is dynamic.

## Task tracker
The "Task tracker" tab is a list of tasks, ideas and notes per project.

• A task can be tied to a project or be a "no project" task.
• A task has a type, status, priority, tags, a due date and a reminder.
• A reminder fires inside the program; you can also create a Windows system
  reminder (via Task Scheduler) that fires even when the program is closed.
• The "🚀 Test in Claude / Codex" buttons create an idea folder, write
  IDEA.md and launch an agent to work the idea out.

## DeepSeek analysis
The analysis describes what the project is, how it works and its stage.

• "🤖 DS analysis" — analyze the selected project.
• "🤖 DS: new" — analyze only projects without a description.
• "🤖 DS: all" — re-analyze all projects.
• "⏹ Stop" — abort the bulk analysis.

The result is cached and shown in the right panel and the table.

## Settings and language
• Font size — the A− / A+ buttons or Ctrl + mouse wheel.
• Language — the dropdown at the top. The UI switches instantly, with no
  restart. Russian, English, German, Spanish and Chinese are available.

All settings and data are stored in %APPDATA%\ProjectManager.
A daily backup is kept in Documents\ProjectManager-Backups.
