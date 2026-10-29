---
name: status
description: Report that the pipeline plugin is loaded and where the knowledge base lives. Use when asked for pipeline status.
disable-model-invocation: true
---

Do not call any tools. Reply with exactly two lines:

1. `pipeline plugin: loaded`
2. `kb: ~/agent-kb (default; the KB_DIR environment variable overrides it)`
