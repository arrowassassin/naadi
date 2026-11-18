# Naadi — Claude Code handoff bundle (15 Nov 2026)

Open this folder in Claude Code and paste the contents of KICKOFF_PROMPT.md as your first message.

- HANDOFF.md ............ the build spec (architecture, free stack, data model, AI, content system, screens, deploy, milestones)
- KICKOFF_PROMPT.md ..... paste into Claude Code to start
- CLAUDE.md ............. copy to the new repo root (working rules)
- claude-setup/.claude/agents/ … implementer-sonnet, implementer-opus, reviewer-opus, ui-verifier subagents
- design/ ............... 27 Claude Design boards + tokens sheet (visual reference)
- db/schema.sql ......... Postgres schema (pgvector halfvec, RLS, budget tables)
- data/ ................. seed content: chapters, 685 questions, 95 SVG figures, taxonomy + question map,
                          60-day plan, pathway/ (16 stages, 186 tasks, 17 guides incl. landing in the USA)
- config/ ............... models.yaml (free OpenRouter routing), sources.yaml, youtube_channels.yaml,
                          authoring_rules.md, requirements.api.txt
- deploy/free-space/ .... DEFAULT $0 Hugging Face Space (Gradio SDK + gradio.Server)
- deploy/space/ ......... Docker Space (if PRO);  deploy/alt-split/ … split alternative
- tools/ ................ export_seed.py, map_taxonomy.py, youtube_mapper.py, check_models.py, pathway_build/
- research/ ............. the cited research behind every decision
