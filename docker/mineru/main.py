import logging
import os
import tempfile
import threading

from fastapi import FastAPI, HTTPException, UploadFile

logger = logging.getLogger(__name__)
app = FastAPI()

# MinerU 模型初始化开销较大，用锁保证只初始化一次
_ready = False
_lock = threading.Lock()


def _ensure_ready() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        # 触发 MinerU 内部模型配置加载
        import magic_pdf.model as model_config  # noqa: F401
        _ready = True
        logger.info("MinerU ready")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ready": _ready}


@app.post("/parse")
async def parse(file: UploadFile) -> dict:
    """
    接收 PDF 或图片文件，返回解析后的 Markdown 文本。

    MinerU 自动判断文档类型：
    - 原生 PDF（可提取文字）→ txt 模式，速度快，保留文档结构
    - 扫描版 PDF / 图片    → ocr 模式，调用内置 OCR 模型
    """
    _ensure_ready()

    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")

    data = await file.read()
    suffix = os.path.splitext(file.filename)[1].lower() or ".pdf"

    with tempfile.TemporaryDirectory() as tmp_dir:
        input_path = os.path.join(tmp_dir, f"input{suffix}")
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)

        with open(input_path, "wb") as f:
            f.write(data)

        from magic_pdf.config.enums import SupportedPdfParseMethod
        from magic_pdf.data.data_reader_writer import (
            FileBasedDataReader,
            FileBasedDataWriter,
        )
        from magic_pdf.data.dataset import PymuDocDataset
        from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze

        pdf_bytes = FileBasedDataReader("").read(input_path)
        image_writer = FileBasedDataWriter(img_dir)
        ds = PymuDocDataset(pdf_bytes)

        parse_method = ds.classify()
        is_ocr = parse_method == SupportedPdfParseMethod.OCR

        infer_result = ds.apply(doc_analyze, ocr=is_ocr)
        pipe_result = (
            infer_result.pipe_ocr_mode(image_writer)
            if is_ocr
            else infer_result.pipe_txt_mode(image_writer)
        )

        md_text = pipe_result.get_markdown("images")

    # 从 Markdown 提取第一个一级标题作为文档标题
    title = ""
    for line in md_text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return {
        "text": md_text,
        "title": title,
        "is_ocr": is_ocr,
        "char_count": len(md_text),
    }
