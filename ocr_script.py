import ocrmypdf
import sys

if len(sys.argv) < 3:
    print("Usage: python ocr_script.py input.pdf output.pdf")
    sys.exit(1)

input_pdf = sys.argv[1]
output_pdf = sys.argv[2]

try:
    ocrmypdf.ocr(input_pdf, output_pdf, language='eng')
    print(f"OCR completed! Saved as {output_pdf}")
except Exception as e:
    print("Error:", e)
