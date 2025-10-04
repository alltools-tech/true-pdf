import os
import shutil
import tempfile
import zipfile
import subprocess
import base64
from typing import List
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import fitz  # PyMuPDF
from pdf2image import convert_from_path
from PIL import Image
from pathlib import Path

app = FastAPI(title="PDF Toolkit - Compressor & Converter")

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

@app.get("/", response_class=HTMLResponse)
def index():
    if Path("index.html").exists():
        html = Path("index.html").read_text(encoding="utf-8")
        return HTMLResponse(content=html)
    else:
        return HTMLResponse(content="<h1>PDF Toolkit API</h1>")

# --- PDF Compress (Basic) ---
@app.post("/compress/basic")
async def compress_basic(
    file: UploadFile = File(...),
    quality: int = Query(80, ge=10, le=100),
    background_tasks: BackgroundTasks = None
):
    in_path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        doc = fitz.open(in_path)
        scale_map = {100: 1.0, 90: 0.9, 80: 0.8, 70: 0.7, 60: 0.65, 50: 0.6, 40: 0.55, 30: 0.5, 20: 0.45, 10: 0.4}
        scale_keys = sorted(scale_map.keys())
        scale = scale_map[min(scale_keys, key=lambda x: abs(x-quality))]
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
                    pix = pix.resize(new_w, new_h)
                except AttributeError:
                    raise HTTPException(status_code=500, detail="PyMuPDF version is incompatible (use >=1.20.0).")
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

# --- OCR: produce searchable PDF using ocrmypdf ---
@app.post("/ocr")
async def ocr_pdf(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    in_path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
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

# --- PDF/Image to Images / ZIP, with Compression Quality ---
@app.post("/pdf-to-images")
async def pdf_to_images(
    file: UploadFile = File(...),
    dpi: int = Query(150, ge=72, le=600),
    fmt: str = Query("png"),
    outtype: str = Query("images"),  # "images" or "zip"
    quality: int = Query(80, ge=10, le=100),
    background_tasks: BackgroundTasks = None
):
    in_path = save_upload_tmp(file)
    tmpdir = tempfile.mkdtemp()
    try:
        images = []
        suffix = Path(file.filename).suffix.lower()
        # If PDF, use pdf2image; else, open as single image
        if suffix == ".pdf":
            images = convert_from_path(in_path, dpi=dpi)
        else:
            img = Image.open(in_path)
            images = [img]
        if not images:
            raise HTTPException(status_code=500, detail="conversion failed")
        out_files = []
        img_urls = []
        img_sizes = []
        for idx, img in enumerate(images, start=1):
            out_path = os.path.join(tmpdir, f"page_{idx}.{fmt}")
            fmt_lower = fmt.lower()
            try:
                if fmt_lower in ("jpg", "jpeg"):
                    img.save(out_path, format="JPEG", quality=quality)
                elif fmt_lower == "png":
                    img.save(out_path, format="PNG", compress_level=max(0, min(9, int((100-quality)/10))))
                elif fmt_lower == "tiff":
                    img.save(out_path, format="TIFF", compression="tiff_deflate")
                elif fmt_lower == "bmp":
                    img.save(out_path, format="BMP")
                elif fmt_lower == "webp":
                    img.save(out_path, format="WEBP", quality=quality)
                elif fmt_lower == "avif":
                    img.save(out_path, format="AVIF", quality=quality)
                elif fmt_lower in ("heic", "heif"):
                    try:
                        import pillow_heif
                        pillow_heif.register_heif_opener()
                        img.save(out_path, format="HEIF", quality=quality)
                    except ImportError:
                        raise HTTPException(status_code=500, detail="HEIF/HEIC support requires pillow-heif installed.")
                elif fmt_lower == "svg":
                    if suffix == ".pdf":
                        try:
                            doc = fitz.open(in_path)
                            page = doc[idx-1]
                            svg_data = page.get_svg_image()
                            with open(out_path, "w", encoding="utf-8") as f:
                                f.write(svg_data)
                        except Exception:
                            raise HTTPException(status_code=500, detail="SVG export failed (requires PyMuPDF).")
                    else:
                        raise HTTPException(status_code=500, detail="SVG export from image not supported.")
                else:
                    img.save(out_path, format="PNG")  # fallback
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Could not save image as {fmt}: {e}")
            out_files.append(out_path)
            if outtype == "images":
                with open(out_path, "rb") as fimg:
                    bdata = fimg.read()
                    img_urls.append(f"data:image/{fmt_lower};base64," + base64.b64encode(bdata).decode())
                    img_sizes.append(len(bdata))
        if outtype == "zip":
            zip_fd, zip_path = tempfile.mkstemp(suffix=".zip")
            os.close(zip_fd)
            make_zip_from_files(out_files, zip_path)
            if background_tasks:
                background_tasks.add_task(cleanup_files, [in_path, tmpdir, zip_path])
            return FileResponse(zip_path, filename="pages.zip", media_type="application/zip")
        else:
            if background_tasks:
                background_tasks.add_task(cleanup_files, [in_path, tmpdir])
            return JSONResponse(content={"images": img_urls, "sizes": img_sizes})
    finally:
        pass

# --- Images to PDF Advanced ---
@app.post("/images-to-pdf")
async def images_to_pdf(
    files: list[UploadFile] = File(...),
    quality: int = Query(80, ge=10, le=100), # For JPEG images, etc.
    background_tasks: BackgroundTasks = None
):
    temp_files = []
    for file in files:
        suffix = os.path.splitext(file.filename)[1]
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        temp_files.append(path)
    images = []
    for f in temp_files:
        try:
            img = Image.open(f)
            if img.mode != "RGB":
                img = img.convert("RGB")
            # Optionally re-save with quality for JPEG/WEBP/AVIF etc.
            if os.path.splitext(f)[1].lower() in [".jpg", ".jpeg", ".webp", ".avif"]:
                img.save(f, quality=quality)
            images.append(img)
        except Exception:
            pass
    if not images:
        raise HTTPException(status_code=400, detail="No valid images.")
    out_pdf = tempfile.mkstemp(suffix=".pdf")[1]
    images[0].save(out_pdf, save_all=True, append_images=images[1:])
    for f in temp_files:
        try:
            os.remove(f)
        except Exception:
            pass
    if background_tasks:
        background_tasks.add_task(cleanup_files, [out_pdf])
    return FileResponse(out_pdf, filename="images.pdf", media_type="application/pdf")

# --- Office to PDF Advanced ---
@app.post("/office-to-pdf")
async def office_to_pdf(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None
):
    suffix = os.path.splitext(file.filename)[1]
    fd, in_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    with open(in_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    out_dir = tempfile.mkdtemp()
    cmd = ["soffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, in_path]
    subprocess.run(cmd, check=True)
    base = os.path.splitext(os.path.basename(in_path))[0]
    out_pdf = os.path.join(out_dir, base + ".pdf")
    if not os.path.exists(out_pdf):
        raise HTTPException(status_code=500, detail="PDF not created.")
    if background_tasks:
        background_tasks.add_task(cleanup_files, [in_path, out_dir])
    return FileResponse(out_pdf, filename="converted.pdf", media_type="application/pdf")

# --- Old endpoints unchanged ---