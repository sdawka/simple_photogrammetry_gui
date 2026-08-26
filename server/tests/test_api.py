import http.client
import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote

from server.app import create_web_server, normalize_upload_name, validate_job_payload


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server = create_web_server(
            "127.0.0.1", 0, data_dir=Path(self.temporary.name), web_dir=None
        )
        self.server.min_free_bytes = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        self.connection.request(method, path, body=body, headers=headers or {})
        response = self.connection.getresponse()
        payload = json.loads(response.read())
        return response.status, payload

    def test_create_idempotent_upload_and_queue(self):
        body = json.dumps({"name": "Garden", "kind": "mesh", "quality": "high"})
        status, job = self.request(
            "POST", "/api/v1/jobs", body, {"Content-Type": "application/json"}
        )
        self.assertEqual(201, status)

        for filename in ("one image.jpg", "two.jpg", "three.png"):
            data = b"image bytes"
            status, result = self.request(
                "PUT",
                f"/api/v1/jobs/{job['id']}/images/{quote(filename)}",
                data,
                {"Content-Length": str(len(data))},
            )
            self.assertEqual(200, status)
            self.assertEqual(len(data), result["bytes"])

        # Replacing the same logical filename is intentional and does not add a fourth image.
        status, _ = self.request(
            "PUT",
            f"/api/v1/jobs/{job['id']}/images/{quote('one image.jpg')}",
            b"new",
            {"Content-Length": "3"},
        )
        self.assertEqual(200, status)
        status, queued = self.request("POST", f"/api/v1/jobs/{job['id']}/start", b"")
        self.assertEqual(202, status)
        self.assertEqual("queued", queued["state"])
        self.assertEqual(3, queued["uploaded_images"])

    def test_start_requires_three_images(self):
        body = json.dumps({"name": "Too small", "kind": "splat"})
        _, job = self.request("POST", "/api/v1/jobs", body)
        status, error = self.request("POST", f"/api/v1/jobs/{job['id']}/start", b"")
        self.assertEqual(400, status)
        self.assertIn("three", error["error"])

    def test_low_disk_rejection_keeps_http_connection_framed(self):
        self.server.min_free_bytes = 1 << 62
        body = json.dumps({"name": "No room", "kind": "mesh"})
        status, error = self.request("POST", "/api/v1/jobs", body)
        self.assertEqual(507, status)
        self.assertIn("disk space", error["error"])

        # The JSON body was consumed, so this request can reuse the connection.
        status, health = self.request("GET", "/api/v1/health")
        self.assertEqual(200, status)
        self.assertEqual("ok", health["status"])

    def test_filename_and_settings_validation(self):
        with self.assertRaises(ValueError):
            normalize_upload_name("../secret.jpg")
        with self.assertRaises(ValueError):
            normalize_upload_name("script.sh")
        _, _, _, settings = validate_job_payload(
            {"kind": "splat", "settings": {"splat_steps": 1234}}
        )
        self.assertEqual(1234, settings["splat_steps"])

    def test_completed_splat_exposes_diagnostics_and_framing_artifact(self):
        job = self.server.store.create_job(
            name="Sparse splat", kind="splat", quality="low", settings={}
        )
        root = self.server.store.job_dir(job["id"])
        for number in range(11):
            (root / "input" / f"{number}.jpg").write_bytes(b"image")
        model = root / "work" / "images_scaled" / "sparse" / "0"
        model.mkdir(parents=True)
        (model / "images.bin").write_bytes(struct.pack("<Q", 7))
        (model / "points3D.bin").write_bytes(struct.pack("<Q", 31))
        (root / "results" / "export_30000.ply").write_text(
            "ply\nformat ascii 1.0\nelement vertex 2\n"
            "property float x\nproperty float y\nproperty float z\nend_header\n"
            "-1 -1 -1\n1 1 1\n"
        )

        status, detail = self.request("GET", f"/api/v1/jobs/{job['id']}")
        self.assertEqual(200, status)
        self.assertEqual("poor", detail["capture_diagnostics"]["level"])
        self.assertEqual(7, detail["capture_diagnostics"]["registered_views"])

        status, payload = self.request("GET", f"/api/v1/jobs/{job['id']}/artifacts")
        self.assertEqual(200, status)
        self.assertIn("viewer-settings.json", [item["name"] for item in payload["artifacts"]])


if __name__ == "__main__":
    unittest.main()
