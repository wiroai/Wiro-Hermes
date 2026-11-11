# Wiro AI for Hermes

Use the [Wiro AI](https://wiro.ai/llm) LLM gateway as a model provider in
[Hermes Agent](https://github.com/NousResearch/hermes-agent). One OpenAI-compatible API and one key
reach GPT, Claude, Gemini, DeepSeek, GLM, Kimi and Grok models. Requires Hermes **0.22.6 or later**
and a Wiro AI API key. The plugin has no dependencies.

## Connect

```sh
hermes plugins install wiro
hermes config set WIRO_API_KEY <your-key>
hermes model
```

Before the catalog listing lands, install from the repository instead:
`hermes plugins install "https://github.com/wiroai/hermes-wiro-provider#wiro"`.

Create the key under your project at [wiro.ai/panel/project](https://wiro.ai/panel/project). For a
Signature project, the value is `YOUR_API_KEY:YOUR_API_SECRET`. In `hermes model`, select
**Wiro AI** and pick a model. One-off use:

```sh
hermes chat --provider wiro -m claude/sonnet-5
```

## Models

`hermes model` lists the live catalog, `GET https://llm.wiro.ai/v1/models`. The models below are
its fallback list, with values from that catalog. Each one calls tools and takes a reasoning level.

| Model | Context window | Image input |
|---|---:|---|
| `openai/gpt-5-4-mini` (default) | 400,000 | Yes |
| `claude/sonnet-5` | 1,000,000 | Yes |
| `google/gemini-3-flash` | 1,048,576 | Yes |
| `deepseek/v4-flash` | 1,048,576 | No |
| `glm/5-3` | 1,000,000 | Yes |
| `moonshot/kimi-k3-1-pro` | 1,048,576 | Yes |
| `xai/grok-4-5` | 500,000 | Yes |
| `openai/gpt-6-sol` | 1,050,000 | Yes |
| `openai/gpt-5-4` | 1,050,000 | Yes |
| `claude/opus-5-5` | 1,000,000 | Yes |
| `google/gemini-3-pro` | 1,048,576 | Yes |
| `deepseek/v4-pro` | 1,048,576 | No |
| `openai/gpt-6-luna` (background tasks) | 1,050,000 | Yes |
| `openai/gpt-5-4-nano` | 400,000 | Yes |

`openai/gpt-6-luna` also describes images for Hermes when the chat model reads text only. Any other
model in the catalog works too, with its own default reasoning. Prices are on each model's page
in the [Wiro AI catalog](https://wiro.ai/models?categories=llm-tool-call) and in the `pricing` field
of `GET /v1/models`. Usage is paid as you go from your Wiro wallet.

## Reasoning

Hermes' reasoning level (`/reasoning`, `--reasoning` or `agent.reasoning_effort`) is sent as
`reasoning_effort` for the models listed above. With no level set nothing is sent, so the model's
default applies. A level a model does not offer is moved to the nearest one it does, and the
response names the change in its `x-wiro-adjusted-parameters` header. Hermes' `ultra` is sent as
`max`.

When reasoning is off, the OpenAI models and Kimi K3.1 Pro get `none`. For the other listed models
`none` is not one of their levels, so they get their lowest level instead: `low` for Claude,
Gemini 3 Pro and Grok 4.5, `minimal` for Gemini 3 Flash, DeepSeek and GLM.

## Configuration

| Variable | Purpose |
|---|---|
| `WIRO_API_KEY` | API key (required) |

The provider id is `wiro`, with the aliases `wiro-ai` and `wiroai`.

## Scope

Prompts, tool definitions and results, attached images and model-list requests go to
`https://llm.wiro.ai/v1` through Hermes' own client, with the key Hermes reads. Usage is paid from
your Wiro wallet. The plugin registers one model provider and nothing else: no tools, hooks,
background work, telemetry, file access or self-update. Update it with `hermes plugins update wiro`.

MIT license.
