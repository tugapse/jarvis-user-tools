# jarvis-user-tools

These are my user tools for [JARVIS](https://github.com/tugapse/ai), kept here so every machine
I use has the same ones.

A JARVIS user tool is a Python file in `~/Ai/user/tools/` with `@tool` functions grouped in a
`ToolCategory`. The function's docstring is what the model reads, so it is written as
instructions for the model.

## Install

```bash
git clone https://github.com/tugapse/jarvis-user-tools ~/Code/jarvis-user-tools
mkdir -p ~/Ai/user/tools
ln -s ~/Code/jarvis-user-tools/tools/*.py ~/Ai/user/tools/
```

With symlinks, `git pull` updates the tools. Copy the files instead if you want to change them
locally.

JARVIS only gives an agent the tools its pipeline lists. To use these tools, copy the pipeline
(e.g. `default.json`) to `~/Ai/user/pipelines/` and add the tool names to the agent's `tools`
list. To make a tool ask for approval before it runs, add its name to `HIL_TOOLS` in
`~/Ai/config.json`.

## Tools

### Project Hub (`tools/project_hub.py`)

Manages the Kanban boards of [Project Hub](https://github.com/tugapse/project-hub): projects,
tasks, checklists, columns and project status.

| Tool | What it does |
|---|---|
| `project_hub_login` | Logs in with a device code: the first call returns a URL and a code to open in the browser, the second call finishes. Pass `url=` the first time to say where your hub runs. |
| `list_projects` | Lists projects with their status and the number of tasks per column. |
| `get_project` | Shows one project's columns, tasks and checklists. |
| `create_project` | Creates a project with the default columns. |
| `add_task` | Adds a task (title, description, icon, color, checklist) to a column. |
| `update_task` | Changes a task's title, description, notes, icon or color, or adds checklist items. |
| `move_task` | Moves a task to another column or position. |
| `set_checklist_item` | Ticks or unticks a checklist item. |
| `delete_task` | Deletes a task. |
| `set_project_status` | Sets a project to `not_started`, `active` or `archived`. |

Projects can be named by id, by exact name or by part of the name (case-insensitive).

Config:

- Hub address: the first time, call `project_hub_login` with `url=` (e.g. `http://localhost:8000/`).
  It is saved in `~/.config/project-hub/url`. `PROJECT_HUB_URL` overrides it. Giving a new
  address logs you out of the old one.
- Token: `PROJECT_HUB_TOKEN`, or the file `~/.config/project-hub/token` that
  `project_hub_login` writes.

Every change reads all projects, changes them and saves the whole list. If the board is open in
a browser, reload it before editing there, or the browser's next save will undo the tool's
change.

Suggested `HIL_TOOLS`: `delete_task`, `set_project_status`.
