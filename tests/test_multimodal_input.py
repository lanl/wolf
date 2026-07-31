import base64
import tempfile
import unittest
from pathlib import Path

from framework.agentic.agents import OpenAIAgent
from framework.utils.multimodal_input import (
    MultimodalInputConfig,
    MultimodalInputProcessor,
    append_text_block,
    combine_prompt_with_user_content,
)


class DummyAgent:
    def __init__(self, capabilities=None):
        self.capabilities = capabilities or []


class TestMultimodalInputProcessor(unittest.TestCase):
    def test_text_attachment_bundle_is_history_safe_and_agent_ready(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            f = root / "note.txt"
            f.write_text("hello from attachment", encoding="utf-8")

            processor = MultimodalInputProcessor(
                MultimodalInputConfig(root_dir=str(root), max_text_chars=1000)
            )
            bundle = processor.process(
                "Please inspect <input> note.txt <input/> now", agent=DummyAgent()
            )

            self.assertEqual(bundle.clean_text, "Please inspect now")
            self.assertTrue(bundle.has_attachments)
            self.assertEqual(len(bundle.attachments), 1)
            self.assertEqual(bundle.attachments[0].modality, "text")
            self.assertIn("[attachments]", bundle.history_text)
            self.assertNotIn("base64", bundle.history_text.lower())
            self.assertEqual(bundle.agent_content[0], {"type": "text", "text": "Please inspect now"})
            self.assertIn("[Text content start]", bundle.agent_content[-1]["text"])
            self.assertIn("hello from attachment", bundle.agent_content[-1]["text"])

    def test_missing_attachment_reports_error_without_raising(self):
        with tempfile.TemporaryDirectory() as td:
            processor = MultimodalInputProcessor(MultimodalInputConfig(root_dir=td))
            bundle = processor.process("Look <input> missing.png </input>", agent=DummyAgent())

            self.assertTrue(bundle.has_attachments)
            self.assertEqual(len(bundle.errors), 1)
            self.assertIn("Attachment not found", bundle.errors[0])
            self.assertIn("[attachment_errors]", bundle.history_text)
            self.assertIn("[Attachment error]", bundle.agent_content[-1]["text"])

    def test_image_attachment_inlines_for_vision_agent(self):
        # Minimal valid PNG bytes: 1x1 transparent-ish PNG.
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "pixel.png"
            img.write_bytes(png_bytes)

            processor = MultimodalInputProcessor(MultimodalInputConfig(root_dir=str(root)))
            bundle = processor.process("Describe <input> pixel.png <input/>", agent=DummyAgent(["vision"]))

            self.assertEqual(bundle.attachments[0].modality, "image")
            image_blocks = [b for b in bundle.agent_content if b.get("type") == "image_url"]
            self.assertEqual(len(image_blocks), 1)
            self.assertTrue(image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertNotIn("data:image/png;base64", bundle.history_text)

    def test_image_attachment_metadata_only_without_vision(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "pixel.png"
            img.write_bytes(b"not really png but extension is enough for classification")

            processor = MultimodalInputProcessor(MultimodalInputConfig(root_dir=str(root)))
            bundle = processor.process("Describe <input> pixel.png <input/>", agent=DummyAgent([]))

            self.assertFalse(any(b.get("type") == "image_url" for b in bundle.agent_content))
            self.assertIn("lacks 'vision' capability", bundle.agent_content[-1]["text"])

    def test_prompt_adapter_helpers_and_openai_context_shape(self):
        blocks = [{"type": "text", "text": "user text"}]
        combined = combine_prompt_with_user_content("workflow prompt", blocks)
        self.assertIsInstance(combined, list)
        self.assertEqual(combined[0], {"type": "text", "text": "workflow prompt"})

        with_schema = append_text_block(combined, "schema text")
        self.assertEqual(with_schema[-1], {"type": "text", "text": "schema text"})

        prompt = [
            {"type": "text", "text": "hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]

        class AgentShell:
            CTX = []

        ctx = OpenAIAgent._make_ctx(AgentShell(), prompt)
        self.assertEqual(ctx[0]["role"], "user")
        self.assertEqual(ctx[0]["content"][1], prompt[1])



    def test_image_attachment_with_string_vision_capability(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "pixel.png"
            img.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="))
            processor = MultimodalInputProcessor(MultimodalInputConfig(root_dir=str(root)))
            bundle = processor.process("Describe <input> pixel.png <input/>", agent=DummyAgent("vision"))
            self.assertTrue(any(b.get("type") == "image_url" for b in bundle.agent_content))

    def test_image_attachment_with_comma_string_vision_capability(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "pixel.png"
            img.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="))
            processor = MultimodalInputProcessor(MultimodalInputConfig(root_dir=str(root)))
            bundle = processor.process("Describe <input> pixel.png <input/>", agent=DummyAgent("text,vision"))
            self.assertTrue(any(b.get("type") == "image_url" for b in bundle.agent_content))

if __name__ == "__main__":
    unittest.main()
