#!/usr/bin/env python3
"""
Content Generator — OpenAI only (GPT-4o + DALL-E 3)

Generates professional LinkedIn post text via GPT-4o and
AI-themed imagery via DALL-E 3, for use by the linkedin_professional_post CLI.

Secrets: .secrets/ai_credentials.json
  {
    "openai_api_key": "sk-..."
  }
"""

import json
import logging
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class ContentGeneratorError(Exception):
    """Raised when content or image generation fails."""
    pass


class ContentGenerator:
    """
    Generates LinkedIn post text (GPT-4o) and AI-themed images (DALL-E 3).

    Usage:
        gen = ContentGenerator()
        text  = gen.generate_linkedin_post()
        image = gen.generate_image()
    """

    SECRETS_FILE = Path(".secrets/ai_credentials.json")

    # OpenAI text model
    OPENAI_TEXT_MODEL = "gpt-4o"

    # DALL-E image spec
    DALLE_MODEL = "dall-e-3"
    DALLE_SIZE = "1024x1024"
    DALLE_QUALITY = "standard"
    DALLE_STYLE = "vivid"

    def __init__(self, secrets_file: Path = None):
        if secrets_file is not None:
            self.SECRETS_FILE = Path(secrets_file)
        self._creds = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_credentials(self) -> dict:
        """Load AI API keys from .secrets/ai_credentials.json."""
        if self._creds is not None:
            return self._creds

        secrets_path = self.SECRETS_FILE
        if not secrets_path.is_absolute():
            # Resolve relative to repo root (two dirs up from this file)
            repo_root = Path(__file__).parent.parent.parent.parent
            secrets_path = repo_root / secrets_path

        if not secrets_path.exists():
            raise ContentGeneratorError(
                f"AI credentials file not found: {secrets_path}\n"
                "Create .secrets/ai_credentials.json with:\n"
                '{\n'
                '  "openai_api_key": "sk-..."\n'
                '}'
            )

        try:
            with open(secrets_path, 'r') as f:
                self._creds = json.load(f)
        except json.JSONDecodeError as e:
            raise ContentGeneratorError(
                f"Invalid JSON in ai_credentials.json: {e}"
            )

        return self._creds

    def _get_openai_client(self):
        """Return an authenticated OpenAI client."""
        try:
            from openai import OpenAI
        except ImportError:
            raise ContentGeneratorError(
                "openai package not installed. Run: pip install openai>=1.50.0"
            )

        creds = self._load_credentials()
        api_key = creds.get('openai_api_key', '')
        if not api_key:
            raise ContentGeneratorError(
                "openai_api_key missing in .secrets/ai_credentials.json"
            )

        return OpenAI(api_key=api_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_linkedin_post(self, topic: str = "Agentic AI") -> str:
        """
        Generate a professional LinkedIn post about Agentic AI using GPT-4o.

        Args:
            topic: Post topic (default: "Agentic AI")

        Returns:
            Post text string (900-1200 chars, 3-5 hashtags)

        Raises:
            ContentGeneratorError: If the API call fails
        """
        client = self._get_openai_client()

        system_prompt = (
            "You are a LinkedIn thought leader who writes engaging, insightful posts "
            "about AI, technology, and the future of work. "
            "Write professional posts that:\n"
            "- Start with a strong hook that grabs attention\n"
            "- Share a genuine insight or perspective\n"
            "- Are 150-200 words in length (900-1200 characters)\n"
            "- Include a clear call-to-action at the end\n"
            "- End with 3-5 relevant hashtags\n"
            "- Use a professional but conversational tone\n"
            "- Avoid buzzword overload and emoji spam; be concrete and specific\n"
            "- Use no more than 2 emojis total\n"
            "Output ONLY the post text, no preamble or explanation."
        )

        user_prompt = (
            f"Write a LinkedIn post about: {topic}\n\n"
            "Focus on the transformative impact of autonomous AI agents in business, "
            "the shift from AI assistants to AI employees, and what this means for "
            "professionals and organizations today."
        )

        logger.info(
            f"Generating LinkedIn post via OpenAI {self.OPENAI_TEXT_MODEL}..."
        )

        try:
            response = client.chat.completions.create(
                model=self.OPENAI_TEXT_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=512,
                temperature=0.4,
            )

            post_text = response.choices[0].message.content.strip()

            if not post_text:
                raise ContentGeneratorError("OpenAI returned empty post text")

            logger.info(
                f"LinkedIn post generated ({len(post_text.split())} words, "
                f"{len(post_text)} chars)"
            )
            return post_text

        except Exception as exc:
            if isinstance(exc, ContentGeneratorError):
                raise
            raise ContentGeneratorError(
                f"OpenAI text generation failed: {exc}"
            ) from exc

    def generate_image(self, concept: str = "Agentic AI futuristic") -> bytes:
        """
        Generate an AI-themed image using DALL-E 3.

        Args:
            concept: Image concept description (default: "Agentic AI futuristic")

        Returns:
            Raw image bytes (JPEG/PNG)

        Raises:
            ContentGeneratorError: If generation or download fails
        """
        client = self._get_openai_client()

        image_prompt = (
            "Professional LinkedIn banner image. "
            "Agentic AI: autonomous AI agents collaborating in a digital neural network. "
            "Deep blue and gold color palette. "
            "Clean corporate aesthetic with geometric patterns and glowing nodes. "
            "Futuristic but professional. "
            "No text or letters overlaid on the image."
        )

        logger.info(
            f"Generating image via DALL-E 3 "
            f"(size={self.DALLE_SIZE}, style={self.DALLE_STYLE})..."
        )

        try:
            response = client.images.generate(
                model=self.DALLE_MODEL,
                prompt=image_prompt,
                size=self.DALLE_SIZE,
                quality=self.DALLE_QUALITY,
                style=self.DALLE_STYLE,
                n=1,
            )

            image_url = response.data[0].url
            if not image_url:
                raise ContentGeneratorError("DALL-E returned no image URL")

            logger.info("DALL-E image URL received, downloading...")

            # Download image bytes
            dl_resp = requests.get(image_url, timeout=60)
            dl_resp.raise_for_status()

            image_bytes = dl_resp.content
            logger.info(f"Image downloaded ({len(image_bytes):,} bytes)")

            return image_bytes

        except Exception as exc:
            if isinstance(exc, ContentGeneratorError):
                raise
            raise ContentGeneratorError(
                f"DALL-E image generation failed: {exc}"
            ) from exc
