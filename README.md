# hermes-wiro-provider

[Wiro AI](https://wiro.ai/llm) as a model provider for
[Hermes Agent](https://github.com/NousResearch/hermes-agent): one OpenAI-compatible API and one key
for GPT, Claude, Gemini, DeepSeek, GLM, Kimi and Grok models.

The plugin lives in [`wiro/`](wiro/); its [README](wiro/README.md) covers setup, models and
reasoning levels.

```sh
hermes plugins install wiro
hermes model   # select Wiro AI, paste WIRO_API_KEY
```

Create a key at [wiro.ai/panel/project](https://wiro.ai/panel/project). API docs:
[wiro.ai/docs/completions-api](https://wiro.ai/docs/completions-api).

## Development

Tests run against a Hermes source checkout (`HERMES_SOURCE`, a sibling `hermes-agent`
directory, or `PYTHONPATH`):

```sh
python -m pytest tests -q
hermes plugins validate wiro --install-deps
```

With `WIRO_API_KEY` set, `WIRO_LIVE_CATALOG=1 python -m pytest tests -q` also compares the model
table against the live catalog.

MIT license.
