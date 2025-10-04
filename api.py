import os
import shutil
import tempfile
import zipfile
import subprocess
from typing import List
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
import fitz  # PyMuPDF
from pdf2image import convert_from_path
from pathlib import Path

app = FastAPI(title="PDF Toolkit - Compressor, Converter & OCR")

# --- Helpers ---

def save_upload_tmp(upload: UploadFile) -> str:
    suffix = Path(upload.filename).suffix or ".pdf"
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(path, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return path

def make_zip_from_files(file_paths: List[str], zip_path: str):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for fp in file_paths:
            z.write(fp, arcname=os.path.basename(fp))

def cleanup_files(paths: List[str]):
    for p in paths:
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
        except Exception:
            pass

# Serve minimal frontend
@app.get("/", response_class=HTMLResponse)
def index():
    if Path("index.html").exists():
        html = Path("index.html").read_text(encoding="utf-8")
        return HTMLResponse(content=html)
    else:
        return HTMLResponse(content="<h1>PDF Toolkit API</h1>")

# --- 1) PyMuPDF "smart" compress (non-destructive attempt) ---
@app.post("/compress/basic")
async def compress_basic(file: UploadFile = File(...), level: int = Query(2, ge=0, le=3), background_tasks: BackgroundTasks = None):
    in_path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        doc = fitz.open(in_path)
        quality_map = {0: 90, 1: 80, 2: 65, 3: 50}
        scale_map = {0: 1.0, 1: 0.9, 2: 0.75, 3: 0.6}
        quality = quality_map.get(level, 65)
        scale = scale_map.get(level, 0.75)
        for pno in range(len(doc)):
            page = doc[pno]
            imglist = page.get_images(full=True)
            if not imglist:
                continue
            for img in imglist:
                xref = img[0]
                base = doc.extract_image(xref)
                img_bytes = base["image"]
                if len(img_bytes) < 30_000:
                    continue
                pix = fitz.Pixmap(doc, xref)
                if pix.n >= 4:
                    pix = fitz.Pixmap(pix, 0)
                new_w = int(pix.width * scale)
                new_h = int(pix.height * scale)
                if new_w < 1 or new_h < 1:
                    continue
                try:
                    pix = pix.resize(new_w, new_h)  # Updated for PyMuPDF >=1.20
                except AttributeError:
                    raise HTTPException(status_code=500, detail="PyMuPDF version incompatible: use >=1.20.0")
                jpg_bytes = pix.tobytes("jpeg", quality=quality)
                try:
                    for r in page.get_images(full=True):
                        if r[0] == xref:
                            rect = page.rect
                            page.insert_image(rect, stream=jpg_bytes)
                            break
                except Exception:
                    page.insert_image(page.rect, stream=jpg_bytes)
                pix = None
        doc.save(out_path, garbage=4, deflate=True)
        doc.close()
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, out_path])
        return FileResponse(out_path, filename="compressed_basic.pdf", media_type="application/pdf")
    except Exception as e:
        try:
            os.remove(out_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

# --- 2) Aggressive Ghostscript compression ---
@app.post("/compress/gs")
async def compress_ghostscript(file: UploadFile = File(...), preset: str = Query("ebook"), background_tasks: BackgroundTasks = None):
    allowed = {"screen", "ebook", "printer", "prepress"}
    if preset not in allowed:
        raise HTTPException(status_code=400, detail="bad preset")
    in_path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        cmd = [
            "gs", "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS=/{preset}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={out_path}", in_path
        ]
        subprocess.run(cmd, check=True)
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, out_path])
        return FileResponse(out_path, filename=f"compressed_gs_{preset}.pdf", media_type="application/pdf")
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="Ghostscript failed")

# --- 3) qpdf optimize (linearize/web-opt) ---
@app.post("/optimize/qpdf")
async def optimize_qpdf(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    in_path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        cmd = ["qpdf", "--linearize", in_path, out_path]
        subprocess.run(cmd, check=True)
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, out_path])
        return FileResponse(out_path, filename="optimized_qpdf.pdf", media_type="application/pdf")
    except subprocess.CalledProcessError:
        raise HTTPException(status_code=500, detail="qpdf failed")

# --- 4) PDF -> images (all pages) and return ZIP ---
@app.post("/pdf-to-images")
async def pdf_to_images(file: UploadFile = File(...), dpi: int = Query(150, ge=72, le=600), fmt: str = Query("png"), background_tasks: BackgroundTasks = None):
    in_path = save_upload_tmp(file)
    tmpdir = tempfile.mkdtemp()
    try:
        images = convert_from_path(in_path, dpi=dpi, fmt=fmt, output_folder=tmpdir)
        if not images:
            raise HTTPException(status_code=500, detail="conversion failed")
        out_files = []
        for idx, img in enumerate(images, start=1):
            out_path = os.path.join(tmpdir, f"page_{idx}.{fmt}")
            if fmt.lower() in ("jpg", "jpeg"):
                img.save(out_path, format="JPEG", quality=85)
            else:
                img.save(out_path, format=fmt.upper())
            out_files.append(out_path)
        zip_fd, zip_path = tempfile.mkstemp(suffix=".zip")
        os.close(zip_fd)
        make_zip_from_files(out_files, zip_path)
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, tmpdir, zip_path])
        return FileResponse(zip_path, filename="pages.zip", media_type="application/zip")
    finally:
        pass

# --- 5) OCR: produce searchable PDF using ocrmypdf (no options) ---
@app.post("/ocr")
async def ocr_pdf(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """
    Accepts a PDF upload and returns a searchable PDF (image + hidden text layer) using ocrmypdf + tesseract.
    This endpoint intentionally exposes NO options ("no options" as requested).
    """
    in_path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        # Validate input file is a PDF and not empty/corrupted
        if os.path.getsize(in_path) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        try:
            fitz.open(in_path)
        except Exception:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF.")
        cmd = ["ocrmypdf", "--skip-text", in_path, out_path]
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="ocrmypdf not installed on server. Install tesseract-ocr and ocrmypdf.")
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"ocrmypdf failed: {e}")
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, out_path])
        return FileResponse(out_path, filename="ocr_searchable.pdf", media_type="application/pdf")
    finally:
        pass