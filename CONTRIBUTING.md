# Contributing

Read [README.md](README.md) for setup, entry points, and project-specific limitations.

1. Create a focused branch and describe the behavior you want to change.
2. Keep formatting consistent with nearby code. Reuse existing helpers within this project and avoid unrelated rewrites.
3. Add regression coverage for changed parsing, matching, authorization, or persistence behavior. Use synthetic fixtures and mocked external services.
4. Run the relevant tests and frontend build documented in the README. Report the commands and results in your pull request.
5. Update documentation when configuration, input schemas, commands, or outputs change.

Do not commit credentials, private workbooks, uploaded documents, provider caches, virtual environments, or generated build directories. Do not make paid API calls or send real emails from automated tests.

A build passing does not prove a live integration works. Record provider, deployment, and integration checks separately. Preserve existing licensing and attribution; files without a license do not acquire one through a contribution.
