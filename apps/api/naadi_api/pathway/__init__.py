"""Pathway (segment 17): stages and tasks with their verification state, the student's progress, the document
vault, the cost tracker and the weekly official-page check.

Module map (the vault modules must never import `llm`, `search` or the embeddings package: an import-graph test
holds that line, so a vault file can never reach a model, a search provider or an index):

    models.py   pydantic contract (OpenAPI -> zod)
    rules.py    pure rules: re-verify, due-rule parsing, cost totals, official hosts
    lww.py      last-writer-wins for the rows devices edit offline
    store.py    SQL for pathway, costs and admin
    routes.py   /api/pathway, /api/me/pathway-stage, /api/costs
    admin.py    /api/admin/pathway
    vault.py + vault_routes.py   /api/vault (private documents)
    verify.py   the weekly check (used by the `pathway_verify` graph)
"""
