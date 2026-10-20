# Execution Guidelines & Autonomy

- **Autonomous Tool Execution**: Execute all tool calls (MCP tools including `sqlite`, `git`, `fetch`, `chrome-devtools`, `context7`, `filesystem`, `playwright`, and terminal commands) autonomously without prompting the user for approval unless a destructive or irreversible action is explicitly required.
- **Continuous Progress**: Do not pause between tool invocations to ask for confirmation when executing planned tasks. Complete the necessary steps proactively.
