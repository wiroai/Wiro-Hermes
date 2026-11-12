"""The JSON schemas of the wiro_* tools: what the model reads to decide when and how to call them."""

from __future__ import annotations

from .catalog import DEFAULT_MODELS, MAX_WAIT_SECONDS, SEARCH_CATEGORIES

PAID = (" Paid from your Wiro AI wallet per run; the result shows the cost in USD. Output files are saved in "
        "Hermes' media cache: the result lists their paths and a media_tag line (MEDIA:<path>) for each, for "
        "surfaces that deliver files that way, plus the Wiro AI URLs.")


def _model(key: str, kind: str) -> dict:
    return {
        "type": "string",
        "description": (f"Wiro AI model ID (owner/model). Leave it out for the default, {DEFAULT_MODELS[key]}. "
                        f"wiro_search_models finds other {kind} models; wiro_model_info shows their settings."),
    }


PARAMS = {
    "type": "object",
    "description": ("Extra model settings by parameter id, as wiro_model_info lists them, e.g. "
                    '{"resolution": "2K"}. They override the other arguments.'),
    "additionalProperties": True,
}
WAIT = {
    "type": "integer",
    "minimum": 0,
    "maximum": MAX_WAIT_SECONDS,
    "description": (f"Seconds to wait for the result (0 to {MAX_WAIT_SECONDS}, default 240). A task still running "
                    "then keeps going: collect it with wiro_task and the returned task_token. 0 returns at once."),
}
FILE_REF = "a URL, or a local file path (a file sent in the chat, a saved result, or a file in the workspace)"


def _files(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


GENERATE_IMAGE = {
    "name": "wiro_generate_image",
    "description": ("Create images from a text prompt with a Wiro AI image model. Pass reference_images to guide the "
                    "result with existing pictures (for changing one picture, wiro_edit_image is the better fit)." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "What the image shows, in detail: subject, style, light, text to render."},
            "aspect_ratio": {"type": "string", "description": 'Shape, e.g. "1:1", "16:9", "9:16", "4:3", or landscape, square, portrait.'},
            "reference_images": _files(f"Images that guide the result: {FILE_REF}. A list for several."),
            "resolution": {"type": "string", "description": 'Output size when the model offers it, e.g. "1K", "2K", "4K".'},
            "count": {"type": "integer", "minimum": 1, "maximum": 8, "description": "Number of images, when the model can make several."},
            "negative_prompt": {"type": "string", "description": "What to keep out, when the model takes it."},
            "seed": {"type": "integer", "description": "Seed for a repeatable result, when the model takes one."},
            "model": _model("image_model", "text-to-image"),
            "params": PARAMS,
            "wait_seconds": WAIT,
        },
        "required": ["prompt"],
    },
}

EDIT_IMAGE = {
    "name": "wiro_edit_image",
    "description": ("Change an existing image with a Wiro AI model. operation edit: change it as the prompt says "
                    "(objects, style, background, text, combine several images). upscale: enlarge and sharpen it. "
                    "remove_background: cut out the subject on a transparent background." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": f"The image to change: {FILE_REF}."},
            "more_images": _files("For edit: further images to combine with the first, e.g. a product and a scene."),
            "operation": {"type": "string", "enum": ["edit", "upscale", "remove_background"],
                          "description": "edit (default), upscale or remove_background."},
            "prompt": {"type": "string", "description": "For edit: the change to make. Required for edit."},
            "scale": {"type": "number", "description": "For upscale: the factor, e.g. 2 or 4."},
            "aspect_ratio": {"type": "string", "description": "For edit: the output shape, when the model offers one."},
            "resolution": {"type": "string", "description": 'For edit: output size, e.g. "1K", "2K".'},
            "model": {"type": "string", "description": (
                f"Wiro AI model ID. Defaults: edit {DEFAULT_MODELS['edit_model']}, upscale "
                f"{DEFAULT_MODELS['upscale_model']}, remove_background {DEFAULT_MODELS['background_model']}. "
                "wiro_search_models (categories image-editing, upscale, background-removal) finds others.")},
            "params": PARAMS,
            "wait_seconds": WAIT,
        },
        "required": ["image"],
    },
}

GENERATE_VIDEO = {
    "name": "wiro_generate_video",
    "description": ("Make a short video with a Wiro AI video model, from a text prompt, or from a start image "
                    "(image to video). Videos usually take 30 seconds to a few minutes." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "The scene and the motion: subject, action, camera, mood."},
            "image": {"type": "string", "description": f"Start frame to animate: {FILE_REF}."},
            "end_image": {"type": "string", "description": "Last frame, when the model takes one."},
            "duration_seconds": {"type": "number", "description": "Length in seconds; the nearest length the model offers is used."},
            "aspect_ratio": {"type": "string", "description": 'e.g. "16:9", "9:16", "1:1".'},
            "resolution": {"type": "string", "description": 'e.g. "480p", "720p", "1080p". Higher costs more.'},
            "audio": {"type": "boolean", "description": "Generate sound too, when the model can."},
            "negative_prompt": {"type": "string", "description": "What to keep out, when the model takes it."},
            "seed": {"type": "integer", "description": "Seed for a repeatable result, when the model takes one."},
            "model": _model("video_model", "text-to-video or image-to-video"),
            "params": PARAMS,
            "wait_seconds": WAIT,
        },
        "required": ["prompt"],
    },
}

TEXT_TO_SPEECH = {
    "name": "wiro_text_to_speech",
    "description": ("Read text aloud with a Wiro AI voice model and save the audio file. Use it for voiceovers, "
                    "narration and spoken replies in a chosen voice or model." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The words to speak. Some models read style tags such as [whispers]."},
            "voice": {"type": "string", "description": "Voice name; wiro_model_info lists the voices of the model."},
            "language": {"type": "string", "description": 'Language code when the model takes one, e.g. "en", "tr".'},
            "model": _model("speech_model", "text-to-speech"),
            "params": PARAMS,
            "wait_seconds": WAIT,
        },
        "required": ["text"],
    },
}

GENERATE_MUSIC = {
    "name": "wiro_generate_music",
    "description": ("Compose music with a Wiro AI music model: instrumental tracks, or songs with lyrics." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Style, mood, instruments, tempo, e.g. \"warm lofi hip hop, soft drums, 85 bpm\"."},
            "lyrics": {"type": "string", "description": "Words to sing. Giving lyrics turns off instrumental unless set."},
            "instrumental": {"type": "boolean", "description": "No vocals, when the model has the setting."},
            "duration_seconds": {"type": "number", "description": "Length in seconds; the nearest length the model offers is used."},
            "model": _model("music_model", "text-to-music or text-to-song"),
            "params": PARAMS,
            "wait_seconds": WAIT,
        },
        "required": ["prompt"],
    },
}

TRANSCRIBE = {
    "name": "wiro_transcribe",
    "description": ("Turn speech into text with a Wiro AI speech-to-text model: voice notes, recordings, interviews, "
                    "or the sound of a video. Returns the transcript as text, and any subtitle or JSON files the "
                    "model makes. Paid from your Wiro AI wallet; the result shows the cost in USD."),
    "parameters": {
        "type": "object",
        "properties": {
            "audio": {"type": "string", "description": f"The recording: {FILE_REF}."},
            "language": {"type": "string", "description": "Spoken language, when the model takes it."},
            "model": _model("transcribe_model", "speech-to-text"),
            "params": PARAMS,
            "wait_seconds": WAIT,
        },
        "required": ["audio"],
    },
}

GENERATE_3D = {
    "name": "wiro_generate_3d",
    "description": ("Turn an image of an object into a 3D model (a GLB file) with a Wiro AI 3D model. For a 3D model "
                    "from a description, make the image first with wiro_generate_image (one object, plain "
                    "background). Takes one to five minutes." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": f"Picture of the object: {FILE_REF}."},
            "prompt": {"type": "string", "description": "Text guidance, for the models that take it."},
            "seed": {"type": "integer", "description": "Seed for a repeatable result."},
            "model": _model("model3d_model", "3d-generation"),
            "params": PARAMS,
            "wait_seconds": dict(WAIT, description=WAIT["description"].replace("default 240", "default 300")),
        },
        "required": ["image"],
    },
}

SEARCH_MODELS = {
    "name": "wiro_search_models",
    "description": ("Search the Wiro AI model catalog for image, video, audio, speech, music, 3D and language models. "
                    "Each row has the model ID, what it does and its price. Free."),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": 'One to three key words, e.g. "upscale", "lip sync", "flux".'},
            "category": {"type": "string", "description": "Filter by category: " + ", ".join(SEARCH_CATEGORIES) + "."},
            "owner": {"type": "string", "description": 'Only models of this owner, e.g. "google".'},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Rows to return (default 10)."},
            "start": {"type": "integer", "minimum": 0, "description": "Offset for the next page (next_start)."},
        },
    },
}

MODEL_INFO = {
    "name": "wiro_model_info",
    "description": ("Show one Wiro AI model's settings (parameter ids, types, options, defaults, what is required) "
                    "and its full price list, before running it or to answer a price question. Free."),
    "parameters": {
        "type": "object",
        "properties": {"model": {"type": "string", "description": "Model ID (owner/model) or its wiro.ai page URL."}},
        "required": ["model"],
    },
}

RUN_MODEL = {
    "name": "wiro_run_model",
    "description": ("Run any Wiro AI model with its own parameters: models the other wiro_* tools do not cover (lip "
                    "sync, try-on, voice cloning, video to video, LLMs and more). Read its parameters with "
                    "wiro_model_info first. File parameters take URLs or local paths. Language models return text." + PAID),
    "parameters": {
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "Model ID (owner/model)."},
            "params": {"type": "object", "additionalProperties": True,
                       "description": "Parameter id -> value, as wiro_model_info lists them."},
            "files": {"type": "object", "additionalProperties": True,
                      "description": f"File parameter id -> {FILE_REF}, or a list of them."},
            "wait_seconds": WAIT,
        },
        "required": ["model"],
    },
}

TASK = {
    "name": "wiro_task",
    "description": ("Follow a Wiro AI task: wait for one that was still running and save its files (wait), check it "
                    "(status), stop it (cancel), or list your recent tasks with their cost (list). Waiting and "
                    "checking are free."),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["wait", "status", "cancel", "list"], "description": "Default wait."},
            "task": {"type": "string", "description": "The task_token (or task_id) a wiro_* tool returned. Not needed for list."},
            "model": {"type": "string", "description": "For list: only tasks of this model."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "For list: how many (default 10)."},
            "wait_seconds": WAIT,
        },
    },
}

WORKFLOWS = {
    "name": "wiro_workflows",
    "description": ("Find Wiro AI workflows (saved multi-step pipelines of models) and read one before running it. "
                    "action list: your workflows (scope mine) or the public gallery (scope public). action get: one "
                    "workflow's inputs and outputs. Free."),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "get"], "description": "Default list."},
            "scope": {"type": "string", "enum": ["mine", "public"], "description": "For list. Default mine."},
            "search": {"type": "string", "description": "For list: words in the title, description or tags."},
            "workflow": {"type": "string", "description": "For get: the workflow slug or its wiro.ai page URL."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "For list (default 20)."},
            "start": {"type": "integer", "minimum": 0, "description": "For list: offset of the next page."},
        },
    },
}

RUN_WORKFLOW = {
    "name": "wiro_run_workflow",
    "description": ("Run a Wiro AI workflow and wait for its outputs, which are saved like the other tools' files. "
                    "Read its inputs with wiro_workflows action get first. Every model step is charged like a normal "
                    "run. To keep waiting on a run that was still going, pass its run_token instead of starting it "
                    "again."),
    "parameters": {
        "type": "object",
        "properties": {
            "workflow": {"type": "string", "description": "The workflow slug or its wiro.ai page URL."},
            "inputs": {"type": "object", "additionalProperties": True,
                       "description": "Input key -> value, as wiro_workflows action get lists them. File inputs take URLs or local paths."},
            "run_token": {"type": "string", "description": "Keep waiting on this earlier run instead of starting a new one."},
            "wait_seconds": WAIT,
        },
    },
}

ALL = (GENERATE_IMAGE, EDIT_IMAGE, GENERATE_VIDEO, TEXT_TO_SPEECH, GENERATE_MUSIC, TRANSCRIBE, GENERATE_3D,
       SEARCH_MODELS, MODEL_INFO, RUN_MODEL, TASK, WORKFLOWS, RUN_WORKFLOW)
