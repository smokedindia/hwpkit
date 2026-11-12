import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hancom_export as he  # noqa: E402


class CheckContainer(unittest.TestCase):
    def _file(self, name: str, data: bytes) -> Path:
        d = Path(tempfile.mkdtemp())
        p = d / name
        p.write_bytes(data)
        return p

    def assert_bad_input(self, name: str, data: bytes) -> None:
        with self.assertRaises(he.HwpkitError) as cm:
            he.check_container(self._file(name, data))
        self.assertEqual(cm.exception.code, "bad_input")

    def test_rejects_non_hwp_bytes(self):
        self.assert_bad_input("x.hwp", b"not an hwp")

    def test_rejects_empty_file(self):
        self.assert_bad_input("x.hwp", b"")

    def test_hwpx_must_be_zip(self):
        self.assert_bad_input("x.hwpx", he.OLE_MAGIC + b"rest")

    def test_accepts_hancom_openable_signatures(self):
        for name, data in [("a.hwp", he.OLE_MAGIC), ("b.hwp", he.HWP3_PREFIX + b".00"),
                           ("c.hwp", he.ZIP_MAGIC), ("d.hwpx", he.ZIP_MAGIC)]:
            he.check_container(self._file(name, data + b"\0" * 16))

    def test_export_never_reaches_hancom_for_garbage(self):
        src = self._file("x.hwp", b"not an hwp")
        called = []
        orig = he.export_hancom
        he.export_hancom = lambda *a, **k: called.append(a)
        try:
            for engine in ("auto", "hancom"):
                with self.assertRaises(he.HwpkitError) as cm:
                    he.export(src, "pdf", src.with_suffix(".pdf"), False, engine)
                self.assertEqual(he.EXIT[cm.exception.code], 11)
        finally:
            he.export_hancom = orig
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
