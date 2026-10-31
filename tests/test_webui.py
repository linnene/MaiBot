"""只读观测页测试：声明合法、无变更操作。"""

from __future__ import annotations

import json
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_NODE_TYPES = {
    "stack",
    "card",
    "grid",
    "tabs",
    "collapsible",
    "dialog",
    "text",
    "stat",
    "progress",
    "image",
    "table",
    "chart",
    "pagination",
    "input",
    "date",
    "switch",
    "select",
    "button",
}


class WebUIContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads((ROOT / "webui.json").read_text(encoding="utf-8"))

    def test_read_only_page_has_no_actions_and_uses_status_query(self) -> None:
        self.assertEqual(self.data["schema_version"], 2)
        pages = self.data["pages"]
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertNotIn("actions", page)
        self.assertEqual(page["placement"], "workspace")
        self.assertEqual(page["queries"]["status"]["api"], "status")
        self.assertTrue(page["auto_refresh"])

    def test_content_nodes_are_declarative_and_known(self) -> None:
        page = self.data["pages"][0]
        self.assertTrue(page["content"])
        for node in page["content"]:
            self.assertIn(node["type"], ALLOWED_NODE_TYPES)
            self.assertNotEqual(node["type"], "button")
            if "value" in node and isinstance(node["value"], dict):
                self.assertEqual(node["value"]["source"], "status")

    def test_workspace_title_matches_page(self) -> None:
        self.assertEqual(self.data["workspace_title"], "她的虚拟旅行")


if __name__ == "__main__":
    unittest.main()
