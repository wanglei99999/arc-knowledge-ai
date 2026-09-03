import builtins

from app.providers.parser.unstructured_provider import UnstructuredParserProvider


def test_markdown_is_parsed_without_loading_heavy_document_models(monkeypatch, tmp_path):
    source = tmp_path / "smoke.md"
    source.write_text("# R0 Smoke\n\nThe codeword is ORCHID-7429.\n", encoding="utf-8")
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("unstructured.partition"):
            raise AssertionError("Markdown must not load Unstructured document models")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    parsed = UnstructuredParserProvider()._parse_sync(str(source))

    assert parsed.title == "R0 Smoke"
    assert "ORCHID-7429" in parsed.text
    assert parsed.metadata["provider"] == "plain-text"
