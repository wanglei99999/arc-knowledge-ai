# docker/paddleocr/main.py
from fastapi import FastAPI, UploadFile
import tempfile, os, threading
from paddleocr import PaddleOCR

app = FastAPI()

_ocr = None
_lock = threading.Lock()

def get_ocr():
    global _ocr
    if _ocr is None:
        with _lock:
            if _ocr is None:
                _ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    return _ocr

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ocr")
async def ocr(file: UploadFile):
    data = await file.read()
    suffix = os.path.splitext(file.filename or ".pdf")[1] or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = get_ocr().ocr(tmp_path, cls=True)
    finally:
        os.unlink(tmp_path)

    lines = []
    for page in result:
        if page is None:
            continue
        for line in page:
            text, confidence = line[1]
            if confidence >= 0.7:
                lines.append(text)

    return {"text": "\n".join(lines), "line_count": len(lines)}