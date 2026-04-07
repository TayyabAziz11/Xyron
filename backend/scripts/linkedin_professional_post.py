#!/usr/bin/env python3
"""
LinkedIn Professional Post — OpenAI content (GPT-4o + DALL-E 3)

Generates a fresh, engaging LinkedIn post about Agentic AI using GPT-4o,
creates a unique AI-themed visual with DALL-E 3, uploads it to LinkedIn,
and posts the combined result.

Usage:
    python3 scripts/linkedin_professional_post.py
    python3 scripts/linkedin_professional_post.py --dry-run
    python3 scripts/linkedin_professional_post.py --content-only
    python3 scripts/linkedin_professional_post.py --no-ai --dry-run

Flags:
    --dry-run       Generate content + image but do NOT post or upload to LinkedIn.
    --content-only  Generate + post text only (skip image generation and upload).
    --no-ai         Skip OpenAI calls; use static placeholder content (no tokens spent).
                    Useful for demos and CI testing without API keys.
"""

import argparse
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — make src/ importable when running as a script
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / 'src'))

from ai_operator.core.content_generator import ContentGenerator, ContentGeneratorError
from ai_operator.core.linkedin_api_helper import LinkedInAPIHelper, LinkedInAPIError

logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static placeholder content (used with --no-ai)
# ---------------------------------------------------------------------------
_PLACEHOLDER_POST = (
    "The era of Agentic AI is here.\n\n"
    "We've moved beyond AI assistants that wait for prompts. Today's autonomous "
    "AI agents plan, execute, and iterate — handling multi-step workflows without "
    "constant human intervention.\n\n"
    "For organizations, this is the shift from AI as a tool to AI as a team member. "
    "The question is no longer 'Can AI help us?' but 'How do we integrate AI "
    "employees into our workforce?'\n\n"
    "The professionals who understand this shift now will lead the next wave of "
    "productivity gains.\n\n"
    "What autonomous AI workflows are you exploring in your organization?\n\n"
    "#AgenticAI #ArtificialIntelligence #FutureOfWork #AIStrategy #Innovation"
)

# Minimal 1x1 PNG (67 bytes) — valid PNG header for placeholder use
_PLACEHOLDER_IMAGE_BYTES = (
    b'\x89PNG\r\n\x1a\n'           # PNG signature
    b'\x00\x00\x00\rIHDR'          # IHDR chunk length + type
    b'\x00\x00\x00\x01'            # width = 1
    b'\x00\x00\x00\x01'            # height = 1
    b'\x08\x02\x00\x00\x00'        # bit depth=8, color type=2 (RGB)
    b'\x90wS\xde'                   # CRC
    b'\x00\x00\x00\x0cIDATx\x9c'   # IDAT chunk
    b'b\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'  # compressed pixel
    b'\x00\x00\x00\x00IEND\xaeB`\x82'  # IEND
)


def main():
    parser = argparse.ArgumentParser(
        description='Post a professional AI-generated LinkedIn update with DALL-E image',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Generate content and image but do not upload or post',
    )
    parser.add_argument(
        '--content-only',
        action='store_true',
        help='Skip image generation; post text only',
    )
    parser.add_argument(
        '--no-ai',
        action='store_true',
        help='Skip OpenAI calls; use static placeholder post + placeholder image',
    )
    args = parser.parse_args()

    generator = ContentGenerator()
    helper = LinkedInAPIHelper()

    # ------------------------------------------------------------------
    # Step 1: Generate post text
    # ------------------------------------------------------------------
    if args.no_ai:
        print("\n[no-ai] Using static placeholder post content...")
        post_text = _PLACEHOLDER_POST
    else:
        print("\nGenerating post content via OpenAI GPT-4o...")
        try:
            post_text = generator.generate_linkedin_post()
        except ContentGeneratorError as exc:
            print(f"Content generation failed: {exc}")
            sys.exit(1)

    word_count = len(post_text.split())
    print(f"Post content ready ({word_count} words)")
    print(f"\n--- Post preview ---")
    print(post_text[:300] + ('...' if len(post_text) > 300 else ''))
    print("--------------------\n")

    image_bytes = None
    image_urn = None

    if not args.content_only:
        # ------------------------------------------------------------------
        # Step 2: Generate image
        # ------------------------------------------------------------------
        if args.no_ai:
            print("[no-ai] Using static placeholder image...")
            image_bytes = _PLACEHOLDER_IMAGE_BYTES
        else:
            print("Generating image via DALL-E 3...")
            try:
                image_bytes = generator.generate_image()
            except ContentGeneratorError as exc:
                print(f"Image generation failed: {exc}")
                sys.exit(1)

        size_kb = len(image_bytes) // 1024 or 1
        print(f"Image ready ({size_kb} KB)")

    if args.dry_run:
        print("\nDry-run mode — skipping LinkedIn upload and post.")
        print(f"   Post text ({word_count} words) is ready.")
        if image_bytes:
            print(f"   Image ({size_kb} KB) is ready.")
        print("\nDry-run complete — no data was sent to LinkedIn.")
        return

    if image_bytes and not args.content_only:
        # ------------------------------------------------------------------
        # Step 3: Upload image to LinkedIn
        # ------------------------------------------------------------------
        print("Uploading image to LinkedIn...")
        try:
            image_urn = helper.upload_image(image_bytes)
        except LinkedInAPIError as exc:
            print(f"Image upload failed: {exc}")
            sys.exit(1)
        print(f"Image uploaded: {image_urn}")

    # ------------------------------------------------------------------
    # Step 4: Create the LinkedIn post
    # ------------------------------------------------------------------
    print("Posting to LinkedIn...")
    try:
        if image_urn:
            result = helper.create_post_with_image(post_text, image_urn)
        else:
            result = helper.create_post(post_text)
    except LinkedInAPIError as exc:
        print(f"Post failed: {exc}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 5: Summary
    # ------------------------------------------------------------------
    print("Posted!")
    print(f"   URN      : {result.get('id')}")
    if image_urn:
        print(f"   Image    : {result.get('image_urn', image_urn)}")
    print(f"   Endpoint : {result.get('endpoint_used')}")
    preview = post_text[:100] + ('...' if len(post_text) > 100 else '')
    print(f'   Preview  : "{preview}"')
    print()


if __name__ == '__main__':
    main()
