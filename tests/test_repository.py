from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / "n8n" / "workflows"
CREDENTIAL_DIR = ROOT / "n8n" / "credentials"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def workflow_reference(node: dict[str, Any]) -> str | None:
    value = node.get("parameters", {}).get("workflowId")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        nested = value.get("value")
        if isinstance(nested, str):
            return nested
    return None


class RepositoryManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow_paths = sorted(WORKFLOW_DIR.glob("*.json"))
        self.workflows = {path: load_json(path) for path in self.workflow_paths}

        self.credential_paths = sorted(CREDENTIAL_DIR.glob("*.template.json"))
        self.credentials: list[dict[str, Any]] = []
        for path in self.credential_paths:
            payload = load_json(path)
            self.assertIsInstance(payload, list, path)
            self.credentials.extend(payload)

    def test_expected_workflows_are_versioned(self) -> None:
        self.assertEqual(
            {path.stem for path in self.workflow_paths},
            {
                "Adapt Feature Image",
                "Adapt Hugo Media",
                "Adapt Reel Media",
                "Blog Post Publish",
                "Reel Publish",
            },
        )

    def test_workflow_names_and_ids_are_unique(self) -> None:
        ids: list[str] = []
        for path, workflow in self.workflows.items():
            self.assertIsInstance(workflow, dict, path)
            self.assertEqual(workflow.get("name"), path.stem, path)
            workflow_id = workflow.get("id")
            self.assertIsInstance(workflow_id, str, path)
            self.assertTrue(workflow_id, path)
            ids.append(workflow_id)

        self.assertEqual(len(ids), len(set(ids)), "workflow IDs must be unique")

    def test_subworkflow_references_resolve(self) -> None:
        known_ids = {workflow["id"] for workflow in self.workflows.values()}
        for path, workflow in self.workflows.items():
            for node in workflow.get("nodes", []):
                referenced_id = workflow_reference(node)
                if referenced_id is not None:
                    self.assertIn(
                        referenced_id,
                        known_ids,
                        f"{path.name}: {node.get('name')} references an unknown workflow",
                    )

    def test_workflow_exports_exclude_instance_state(self) -> None:
        for path, workflow in self.workflows.items():
            self.assertFalse(workflow.get("pinData"), path)
            self.assertNotIn("shared", workflow, path)
            self.assertNotIn("versionMetadata", workflow, path)
            self.assertNotIn("instanceId", workflow.get("meta", {}), path)

    def test_credential_ids_and_names_are_unique(self) -> None:
        ids = [credential.get("id") for credential in self.credentials]
        names = [credential.get("name") for credential in self.credentials]
        self.assertTrue(all(isinstance(value, str) and value for value in ids))
        self.assertTrue(all(isinstance(value, str) and value for value in names))
        self.assertEqual(len(ids), len(set(ids)), "credential IDs must be unique")
        self.assertEqual(len(names), len(set(names)), "credential names must be unique")

    def test_workflow_credential_references_resolve(self) -> None:
        known_ids = {credential["id"] for credential in self.credentials}
        for path, workflow in self.workflows.items():
            for node in workflow.get("nodes", []):
                for reference in node.get("credentials", {}).values():
                    credential_id = reference.get("id")
                    self.assertIn(
                        credential_id,
                        known_ids,
                        f"{path.name}: {node.get('name')} references an unknown credential",
                    )

    def test_credential_templates_contain_only_placeholders(self) -> None:
        placeholder = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")
        for credential in self.credentials:
            data = credential.get("data")
            self.assertIsInstance(data, dict, credential.get("name"))
            for key, value in data.items():
                self.assertIsInstance(value, str, f"{credential.get('name')}.{key}")
                self.assertRegex(value, placeholder, f"{credential.get('name')}.{key}")

    def test_runtime_dependencies_are_locked(self) -> None:
        runtime_files = [
            ROOT / "docker-compose.yml",
            ROOT / "n8n" / "Dockerfile",
            ROOT / "pyautoflip" / "Dockerfile",
        ]
        runtime_config = "\n".join(
            path.read_text(encoding="utf-8") for path in runtime_files
        )
        self.assertNotIn(":stable", runtime_config)
        self.assertGreaterEqual(
            len(re.findall(r"@sha256:[0-9a-f]{64}", runtime_config)),
            7,
            "container bases should be immutable by default",
        )

        package = load_json(ROOT / "n8n" / "package.json")
        for version in package["dependencies"].values():
            self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertTrue((ROOT / "n8n" / "package-lock.json").is_file())

        requirements = (ROOT / "pyautoflip" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("--hash=sha256:", requirements)
        self.assertTrue((ROOT / "pyautoflip" / "requirements.in").is_file())

    def test_example_configuration_is_host_neutral(self) -> None:
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotRegex(example, r"\b192\.168\.\d{1,3}\.\d{1,3}\b")

    def test_bootstrap_uses_supported_interfaces(self) -> None:
        bootstrap = (ROOT / "scripts" / "bootstrap-n8n.sh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("docker volume", bootstrap)
        self.assertNotIn("sqlite", bootstrap.lower())
        self.assertNotIn("PUBLISH_WORKFLOW_IDS", bootstrap)
        library = (ROOT / "scripts" / "lib.sh").read_text(encoding="utf-8")
        self.assertIn("/healthz/readiness", library)


if __name__ == "__main__":
    unittest.main()
