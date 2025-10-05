import os
import shutil
import tempfile
import zipfile
import subprocess
import base64
from typing import List
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import fitz  # PyMuPDF
from pdf2image import convert_from_path
from PIL import Image
from PyPDF2 import PdfReader, PdfWriter
from pathlib import Path

app = FastAPI(title="PDF Tool — Advanced API")

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
        return HTMLResponse(content="<h1>PDF Tool API</h1>")

# --- PDF Page Count ---
@app.post("/pdf-page-count")
async def pdf_page_count(file: UploadFile = File(...)):
    path = save_upload_tmp(file)
    try:
        doc = fitz.open(path)
        page_count = doc.page_count
        doc.close()
    except Exception:
        os.remove(path)
        raise HTTPException(status_code=400, detail="Invalid PDF")
    os.remove(path)
    return {"filename": file.filename, "page_count": page_count}

# --- PDF Split ---
@app.post("/pdf-split")
async def pdf_split(
    file: UploadFile = File(...),
    from_page: int = Form(...),
    to_page: int = Form(...)
):
    path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        reader = PdfReader(path)
        writer = PdfWriter()
        n = len(reader.pages)
        fp = max(1, from_page)
        tp = min(to_page, n)
        for i in range(fp-1, tp):
            writer.add_page(reader.pages[i])
        with open(out_path, "wb") as out_f:
            writer.write(out_f)
        return FileResponse(out_path, filename=f"{file.filename.replace('.pdf','')}_pages_{fp}-{tp}.pdf", media_type="application/pdf")
    finally:
        cleanup_files([path])

# --- PDF Merge ---
@app.post("/pdf-merge")
async def pdf_merge(files: List[UploadFile] = File(...)):
    temp_files = []
    for file in files:
        path = save_upload_tmp(file)
        temp_files.append(path)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        writer = PdfWriter()
        for p in temp_files:
            reader = PdfReader(p)
            for page in reader.pages:
                writer.add_page(page)
        with open(out_path, "wb") as out_f:
            writer.write(out_f)
        return FileResponse(out_path, filename="merged.pdf", media_type="application/pdf")
    finally:
        cleanup_files(temp_files)

# --- PDF Extract Text ---
@app.post("/pdf-extract-text")
async def pdf_extract_text(file: UploadFile = File(...)):
    path = save_upload_tmp(file)
    try:
        doc = fitz.open(path)
        all_text = ""
        for page in doc:
            all_text += page.get_text()
        doc.close()
    except Exception:
        os.remove(path)
        raise HTTPException(status_code=400, detail="Invalid PDF")
    os.remove(path)
    return JSONResponse({"filename": file.filename, "text": all_text})

# --- PDF Set Password ---
@app.post("/pdf-set-password")
async def pdf_set_password(
    file: UploadFile = File(...),
    password: str = Form(...)
):
    path = save_upload_tmp(file)
    out_fd, out_path = tempfile.mkstemp(suffix=".pdf")
    os.close(out_fd)
    try:
        reader = PdfReader(path)
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(password)
        with open(out_path, "wb") as out_f:
            writer.write(out_f)
        return FileResponse(out_path, filename=f"{file.filename.replace('.pdf','')}_protected.pdf", media_type="application/pdf")
    finally:
        cleanup_files([path])

# --------------- OLD ENDPOINTS BELOW (unchanged) ----------------

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
                if len(img_bytes) < 30000:
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
        file_size = os.path.getsize(out_path)
        response = FileResponse(out_path, filename=file.filename.replace('.pdf','') + '_compressed.pdf', media_type="application/pdf")
        response.headers["X-Converted-Filename"] = file.filename.replace('.pdf','') + '_compressed.pdf'
        response.headers["X-Converted-Filesize"] = str(file_size)
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, out_path])
        return response
    except Exception as e:
        try:
            os.remove(out_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

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
        file_size = os.path.getsize(out_path)
        response = FileResponse(out_path, filename=file.filename.replace('.pdf','') + '_ocr.pdf', media_type="application/pdf")
        response.headers["X-Converted-Filename"] = file.filename.replace('.pdf','') + '_ocr.pdf'
        response.headers["X-Converted-Filesize"] = str(file_size)
        if background_tasks:
            background_tasks.add_task(cleanup_files, [in_path, out_path])
        return response
    finally:
        pass

@app.post("/pdf-to-images")
async def pdf_to_images(
    file: UploadFile = File(...),
    dpi: int = Query(150, ge=72, le=600),
    fmt: str = Query("png"),
    outtype: str = Query("images"),
    quality: int = Query(80, ge=10, le=100),
    background_tasks: BackgroundTasks = None
):
    in_path = save_upload_tmp(file)
    tmpdir = tempfile.mkdtemp()
    try:
        images = []
        suffix = Path(file.filename).suffix.lower()
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
                    img.save(out_path, format="PNG")
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
            file_size = os.path.getsize(zip_path)
            response = FileResponse(zip_path, filename=file.filename.replace('.pdf','') + '_images.zip', media_type="application/zip")
            response.headers["X-Converted-Filename"] = file.filename.replace('.pdf','') + '_images.zip'
            response.headers["X-Converted-Filesize"] = str(file_size)
            if background_tasks:
                background_tasks.add_task(cleanup_files, [in_path, tmpdir, zip_path])
            return response
        else:
            if background_tasks:
                background_tasks.add_task(cleanup_files, [in_path, tmpdir])
            return JSONResponse(content={"images": img_urls, "sizes": img_sizes})
    finally:
        pass

@app.post("/images-to-pdf")
async def images_to_pdf(
    files: List[UploadFile] = File(...),
    quality: int = Query(80, ge=10, le=100),
    background_tasks: BackgroundTasks = None
):
    temp_files = []
    images = []
    for file in files:
        suffix = os.path.splitext(file.filename)[1]
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        temp_files.append((path, file.filename))

    for fpath, fname in temp_files:
        ext = os.path.splitext(fname)[1].lower()
        try:
            if ext in [".svg"]:
                try:
                    import cairosvg
                    png_path = fpath + ".png"
                    cairosvg.svg2png(url=fpath, write_to=png_path)
                    img = Image.open(png_path).convert("RGB")
                    os.remove(png_path)
                except ImportError:
                    raise HTTPException(status_code=500, detail="SVG support requires cairosvg installed.")
            elif ext in [".bmp", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".avif"]:
                img = Image.open(fpath)
                if img.mode != "RGB":
                    img = img.convert("RGB")
            elif ext in [".heic", ".heif"]:
                try:
                    import pillow_heif
                    pillow_heif.register_heif_opener()
                    img = Image.open(fpath)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                except ImportError:
                    raise HTTPException(status_code=500, detail="HEIF/HEIC support requires pillow-heif installed.")
            else:
                img = Image.open(fpath)
                if img.mode != "RGB":
                    img = img.convert("RGB")
            if ext in [".jpg", ".jpeg", ".webp", ".avif"]:
                img.save(fpath, quality=quality)
            images.append(img)
        except Exception:
            pass
    if not images:
        raise HTTPException(status_code=400, detail="No valid images.")
    out_pdf = tempfile.mkstemp(suffix=".pdf")[1]
    images[0].save(out_pdf, save_all=True, append_images=images[1:])
    for fpath, _ in temp_files:
        try:
            os.remove(fpath)
        except Exception:
            pass
    file_size = os.path.getsize(out_pdf)
    first_name = files[0].filename if files and files[0].filename else "images"
    base_name = os.path.splitext(first_name)[0]
    response = FileResponse(out_pdf, filename=base_name + "_images.pdf", media_type="application/pdf")
    response.headers["X-Converted-Filename"] = base_name + "_images.pdf"
    response.headers["X-Converted-Filesize"] = str(file_size)
    if background_tasks:
        background_tasks.add_task(cleanup_files, [out_pdf])
    return response

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
    file_size = os.path.getsize(out_pdf)
    response = FileResponse(out_pdf, filename=f"{base}.pdf", media_type="application/pdf")
    response.headers["X-Converted-Filename"] = f"{base}.pdf"
    response.headers["X-Converted-Filesize"] = str(file_size)
    if background_tasks:
        background_tasks.add_task(cleanup_files, [in_path, out_dir])
    return response 