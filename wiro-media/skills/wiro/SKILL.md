---
name: wiro
description: "Make and edit images, video, speech, music, transcripts and 3D models with Wiro AI's model catalog."
version: 2.0.0
author: Wiro AI
license: MIT
metadata:
  hermes:
    tags: [image, video, speech, music, transcription, 3d, media, wiro]
    related_skills: []
---

# Wiro AI media

Wiro AI runs image, video, audio, speech, music, 3D and language models from one catalog,
paid per run from the user's Wiro AI wallet. The `wiro_*` tools reach all of it.

## Pick the tool

| The user wants | Tool |
|---|---|
| A new picture | `wiro_generate_image` |
| A change to a picture, a bigger or sharper one, a cut-out subject | `wiro_edit_image` (`edit`, `upscale`, `remove_background`) |
| A clip from a prompt or from a still | `wiro_generate_video` (pass `image` to animate it) |
| A voiceover or a spoken reply in a chosen voice | `wiro_text_to_speech` |
| A track or a song | `wiro_generate_music` (`lyrics` for vocals) |
| The words of a recording, a voice note or a video | `wiro_transcribe` |
| A 3D model of an object | `wiro_generate_3d` (from an image) |
| Anything else in the catalog (lip sync, try-on, voice cloning, video to video, LLMs) | `wiro_search_models`, `wiro_model_info`, then `wiro_run_model` |
| A saved multi-step pipeline | `wiro_workflows`, then `wiro_run_workflow` |

Each media tool has a sensible default model. Pass `model` only when the user names one, or
when the default cannot do the job (for example a 4K image, a 10-second clip, a voice the model
lacks).

## Choose a model and read its price

1. `wiro_search_models` with one to three key words or a `category` (`text-to-image`,
   `image-to-video`, `text-to-speech`, `upscale` ...). Each row has a one-line price.
2. `wiro_model_info` for the model you picked: its parameter ids, options, defaults, what is
   required, and the full price table. Prices often depend on settings such as resolution or
   duration, so quote from this table, not from the search row.
3. Run it. Settings a tool argument does not cover go in `params`, keyed by parameter id.

Tell the user the expected cost in one line before a paid run that is large or open-ended
(many images, long or high-resolution video, a workflow). Every result reports what the run
cost (`cost` and `cost_usd`); mention it when you report the result.

## Files in and out

- Inputs take a public URL or a local path: a file the user sent in the chat, a file saved by an
  earlier `wiro_*` call, or a file in the workspace. Local files are uploaded to the user's Wiro
  AI storage first. With a sandboxed terminal backend only files Hermes received or saved are read.
- Outputs are saved under Hermes' media cache (`cache/generated/images`, `videos`, `audio`,
  `3d`, `files`). The result lists each file's `path`, its Wiro AI `url`, and a `media_tag` line
  (`MEDIA:/absolute/path`). On messaging platforms and in the desktop app, put the `media_tag`
  lines in your reply to deliver the files. In a plain terminal, give the path instead.
- A result's `url` can be passed straight into the next tool, so a chain needs no re-upload.

## Long jobs

Video and 3D jobs can take minutes. A tool waits up to `wait_seconds` (240 by default). When the
result says `"status": "running"`, the task keeps going on Wiro AI: call `wiro_task` with the
returned `task_token` to wait again and save the files. Never call the original tool again for
the same job: that starts and bills a second run. `wiro_task` with `action: "list"` shows recent
tasks and their cost, and `action: "cancel"` stops one (a task that already started can still be
charged).

## When a run fails

A failed task is not charged. Read the `error`, then change one thing: a setting the model
offers (see `wiro_model_info`), a shorter prompt, a different input file, or another model from
`wiro_search_models`. A refused input names the parameter it needs.

## Recipes

- **Product shot to ad clip:** `wiro_edit_image` with `remove_background` on the photo, then
  `edit` to place it in a scene, then `wiro_generate_video` with the result's `url` as `image`.
- **Idea to 3D asset:** `wiro_generate_image` with one object on a plain background, then
  `wiro_generate_3d` on that image.
- **Voice note to spoken answer:** `wiro_transcribe` the voice note, write the answer, then
  `wiro_text_to_speech` and deliver the audio with its `media_tag`.
- **Podcast clip with music:** `wiro_text_to_speech` for the narration and
  `wiro_generate_music` with `instrumental: true` for the bed.
- **Find the cheapest fit:** `wiro_search_models` in the category, then `wiro_model_info` on two
  or three candidates and compare their price tables for the user's settings.

## Hermes' own media tools

When the user sets `image_gen.provider: wiro`, `video_gen.provider: wiro`, `tts.provider: wiro`
or `stt.provider: wiro`, Hermes' `image_generate`, `video_generate`, `text_to_speech` and voice
message transcription run on Wiro AI too, with the model from `image_gen.model`,
`video_gen.model`, `tts.model` and `stt.wiro.model`.
