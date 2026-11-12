"""Default models and the short model lists Hermes' pickers show.

Every tool takes any Wiro AI model through its ``model`` argument; these are only the defaults
when none is given, and each one can be changed in the plugin settings
(``plugins.entries.wiro-media.settings.<key>``). Chosen on 2026-11-11 from the live catalog for
quality, speed and a low price per run.
"""

from __future__ import annotations

DEFAULT_MODELS = {
    "image_model": "google/nano-banana-2-1",
    "edit_model": "google/nano-banana-2-1",
    "upscale_model": "xinntao/real-esrgan",
    "background_model": "wiro/remove-background",
    "video_model": "bytedance/seedance-v1-pro-fast",
    "speech_model": "elevenlabs/eleven-v4-turbo",
    "music_model": "elevenlabs/text-to-music-v2",
    "transcribe_model": "elevenlabs/speech-to-text",
    "model3d_model": "microsoft/trellis-2",
}

DEFAULT_WAIT_SECONDS = 240
# Hermes stops a tool call after 420 s by default (timeouts.tools.concurrent_batch), so a wait
# stays under it and a longer job is picked up again with wiro_task.
MAX_WAIT_SECONDS = 400

# Category names Tool/List filters on, for the search tool's description.
SEARCH_CATEGORIES = (
    "text-to-image", "image-to-image", "image-editing", "upscale", "background-removal", "restoration",
    "text-to-video", "image-to-video", "video-to-video", "talking-head", "text-to-speech", "voice-clone",
    "speech-to-text", "speech-to-speech", "text-to-music", "text-to-song", "3d-generation", "llm", "chat",
)
PUBLIC_CATEGORIES = frozenset(SEARCH_CATEGORIES) | {"music-generation", "llm-reasoning", "llm-tool-call",
                                                    "realtime-conversation", "rag", "noise-reduction"}

# Shown in `hermes tools` when Wiro AI is the image_generate or video_generate backend. Any other
# model ID works too (image_gen.model / video_gen.model).
IMAGE_MODELS = (
    {"id": "google/nano-banana-2-1", "display": "Nano Banana 2.1 (Google)", "strengths": "Text in images, edits, 1K to 4K"},
    {"id": "openai/gpt-image-2", "display": "GPT Image 2 (OpenAI)", "strengths": "Prompt following, edits, low to high quality"},
    {"id": "bytedance/seedream-v5-pro", "display": "Seedream V5 Pro (ByteDance)", "strengths": "Photoreal scenes, 1K and 2K"},
    {"id": "alibaba/qwen-image-3-0", "display": "Qwen Image 3.0 (Alibaba)", "strengths": "Generation and editing"},
    {"id": "xai/grok-imagine-image-v2", "display": "Grok Imagine Image V2 (xAI)", "strengths": "Fast, stylised images"},
    {"id": "google/nano-banana-2-lite", "display": "Nano Banana 2 Lite (Google)", "strengths": "Low price per image"},
)
VIDEO_MODELS = (
    {"id": "bytedance/seedance-v1-pro-fast", "display": "Seedance V1 Pro Fast (ByteDance)", "strengths": "Fast, 480p to 1080p, text or image"},
    {"id": "bytedance/seedance-2-0-fast", "display": "Seedance 2.0 Fast (ByteDance)", "strengths": "Newer motion model"},
    {"id": "klingai/kling-v4-flash", "display": "Kling 4.0 Flash (KlingAI)", "strengths": "Cinematic motion"},
    {"id": "minimax/h3-lite", "display": "MiniMax H3 Lite", "strengths": "4 to 15 seconds"},
    {"id": "pruna/p-video-2-pro", "display": "P-Video 2 Pro (Pruna)", "strengths": "Speed or quality mode"},
)
SPEECH_MODELS = (
    {"id": "elevenlabs/eleven-v4-turbo", "display": "Eleven V4 Turbo (ElevenLabs)", "strengths": "Fast, many voices and languages"},
    {"id": "elevenlabs/eleven-v4", "display": "Eleven V4 (ElevenLabs)", "strengths": "Most expressive"},
    {"id": "google/gemini-3-1-tts", "display": "Gemini 3.1 Flash TTS (Google)", "strengths": "Style from the prompt"},
)
TRANSCRIBE_MODELS = (
    {"id": "elevenlabs/speech-to-text", "display": "ElevenLabs Speech to Text", "strengths": "Many languages, billed per audio second"},
    {"id": "openai/whisper-large-v3", "display": "Whisper Large V3 (OpenAI)", "strengths": "Speaker labels"},
    {"id": "qwen/qwen3-asr-1-7b", "display": "Qwen3-ASR 1.7B", "strengths": "Billed per audio second"},
)
