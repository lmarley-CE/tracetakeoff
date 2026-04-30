"""
TraceTakeoff Prototype
A quick Streamlit prototype for mechanical takeoff assistance.

WHY THIS VERSION EXISTS:
Some environments accidentally install the wrong package named `fitz`, which causes:
    ModuleNotFoundError: No module named 'frontend'

This version avoids crashing at import time. It starts the Streamlit app first,
then checks whether the correct PDF engine is available. If PyMuPDF is missing
or conflicted, the app shows a clear setup message instead of throwing a hard error.

Recommended clean install:
    pip uninstall -y fitz
    pip install --upgrade PyMuPDF streamlit pandas openpyxl pillow streamlit-drawable-canvas

Run app:
    streamlit run app.py

Run lightweight tests:
    python app.py --run-tests

What this V1 does:
- Upload a PDF drawing set
- Let estimator define a product to find, such as 6" Storm Drain
- Render PDF pages as images when PyMuPDF is installed correctly
- Allow manual line segment marking on a page
- Calculate rough length from a calibration scale
- Generate a master takeoff table
- Export Excel summary
- Export a marked-up PDF with red highlighted takeoff lines when PyMuPDF is available

Notes:
- This V1 is intentionally human-guided.
- OCR/AI auto-detection should be added after the review/measurement workflow is proven.
"""

from __future__ import annotations

import importlib
import io
import math
import sys
import unittest
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from PIL import Image, ImageDraw

try:
    import streamlit as st
except ModuleNotFoundError:  # Allows unit tests/imports to explain missing Streamlit cleanly.
    st = None  # type: ignore

try:
    from streamlit_drawable_canvas import st_canvas
except ModuleNotFoundError:
    st_canvas = None  # type: ignore


# -----------------------------
# Data Models
# -----------------------------

@dataclass
class ProductRule:
    product_name: str
    aliases: List[str]
    unit: str
    measurement_type: str
    unit_cost: float
    highlight_color: str = "red"


@dataclass
class TakeoffRow:
    page_number: int
    drawing_name: str
    product_name: str
    matched_spec: str
    quantity: float
    unit: str
    unit_cost: float
    total_cost: float
    confidence: str
    notes: str


# -----------------------------
# PDF Engine Helpers
# -----------------------------

def get_pymupdf() -> Tuple[Optional[Any], Optional[str]]:
    """Return the correct PyMuPDF module if available, otherwise an explanation.

    PyMuPDF can be imported in modern environments as `pymupdf`.
    Older examples often use `fitz`, but a different package also named `fitz`
    exists and can break with `from frontend import *`.

    This function intentionally avoids crashing the whole app.
    """
    try:
        pymupdf = importlib.import_module("pymupdf")
        if hasattr(pymupdf, "open") and hasattr(pymupdf, "Matrix"):
            return pymupdf, None
        return None, "The installed `pymupdf` package does not look like PyMuPDF."
    except ModuleNotFoundError as exc:
        # If the missing module is pymupdf itself, provide setup instructions.
        if exc.name == "pymupdf":
            return None, (
                "PyMuPDF is not installed. Run: `pip install --upgrade PyMuPDF`. "
                "If you previously installed `fitz`, run: `pip uninstall -y fitz` first."
            )
        return None, f"PyMuPDF import failed because another module is missing: {exc.name}"
    except Exception as exc:
        return None, (
            "PyMuPDF could not be imported. This often happens when the wrong `fitz` "
            f"package is installed. Details: {exc}"
        )


def require_pymupdf() -> Any:
    """Return PyMuPDF or raise a clear runtime error."""
    pymupdf, error = get_pymupdf()
    if pymupdf is None:
        raise RuntimeError(error or "PyMuPDF is unavailable.")
    return pymupdf


def render_pdf_pages_uncached(pdf_bytes: bytes, zoom: float = 2.0) -> List[Image.Image]:
    """Render each PDF page into a PIL image.

    This uncached function is easy to test and does not depend on Streamlit.
    """
    if not pdf_bytes:
        raise ValueError("No PDF bytes were provided.")

    fitz = require_pymupdf()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images: List[Image.Image] = []

    try:
        for page in doc:
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            images.append(img)
    finally:
        doc.close()

    return images


if st is not None:
    @st.cache_data(show_spinner=False)
    def render_pdf_pages(pdf_bytes: bytes, zoom: float = 2.0) -> List[Image.Image]:
        return render_pdf_pages_uncached(pdf_bytes, zoom)
else:
    def render_pdf_pages(pdf_bytes: bytes, zoom: float = 2.0) -> List[Image.Image]:
        return render_pdf_pages_uncached(pdf_bytes, zoom)


def export_marked_pdf(
    pdf_bytes: bytes,
    page_index: int,
    line_segments: List[Dict[str, float]],
    image_width: int,
    image_height: int,
) -> bytes:
    """Create a new PDF with red takeoff lines drawn over the selected page.

    Canvas coordinates are based on rendered image size. We convert them back
    to PDF page coordinates.
    """
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Image width and height must be greater than zero.")

    fitz = require_pymupdf()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    try:
        if page_index < 0 or page_index >= len(doc):
            raise IndexError("Page index is outside the PDF page range.")

        page = doc[page_index]
        page_rect = page.rect

        scale_x = page_rect.width / image_width
        scale_y = page_rect.height / image_height

        for seg in line_segments:
            x1 = float(seg["x1"]) * scale_x
            y1 = float(seg["y1"]) * scale_y
            x2 = float(seg["x2"]) * scale_x
            y2 = float(seg["y2"]) * scale_y

            shape = page.new_shape()
            shape.draw_line(fitz.Point(x1, y1), fitz.Point(x2, y2))
            shape.finish(color=(1, 0, 0), width=2)
            shape.commit()

        output = io.BytesIO()
        doc.save(output)
        return output.getvalue()
    finally:
        doc.close()


def export_marked_image_preview(
    image: Image.Image,
    line_segments: List[Dict[str, float]],
) -> Image.Image:
    """Create a simple PNG preview with red lines.

    This is a fallback visual output and is also useful for testing without PyMuPDF.
    """
    preview = image.copy().convert("RGB")
    draw = ImageDraw.Draw(preview)
    for seg in line_segments:
        draw.line(
            [
                (float(seg["x1"]), float(seg["y1"])),
                (float(seg["x2"]), float(seg["y2"])),
            ],
            fill="red",
            width=4,
        )
    return preview


# -----------------------------
# Measurement Helpers
# -----------------------------

def distance_pixels(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def calculate_total_length(line_segments: List[Dict[str, float]], feet_per_pixel: float) -> float:
    if feet_per_pixel <= 0:
        raise ValueError("Feet per pixel must be greater than zero.")

    total_pixels = 0.0
    for seg in line_segments:
        total_pixels += distance_pixels(
            float(seg["x1"]),
            float(seg["y1"]),
            float(seg["x2"]),
            float(seg["y2"]),
        )
    return total_pixels * feet_per_pixel


def parse_canvas_lines(canvas_json: Dict[str, Any] | None) -> List[Dict[str, float]]:
    """Extract line objects from Streamlit drawable canvas JSON.

    Streamlit drawable canvas returns Fabric.js objects. For line objects,
    the actual coordinates are offset by the object's `left` and `top` values.
    """
    segments: List[Dict[str, float]] = []

    if not canvas_json or "objects" not in canvas_json:
        return segments

    for obj in canvas_json["objects"]:
        if obj.get("type") == "line":
            left = float(obj.get("left", 0) or 0)
            top = float(obj.get("top", 0) or 0)
            x1 = left + float(obj.get("x1", 0) or 0)
            y1 = top + float(obj.get("y1", 0) or 0)
            x2 = left + float(obj.get("x2", 0) or 0)
            y2 = top + float(obj.get("y2", 0) or 0)
            segments.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})

    return segments


# -----------------------------
# Export Helpers
# -----------------------------

def make_excel_download(rows: List[TakeoffRow]) -> bytes:
    df = pd.DataFrame([asdict(row) for row in rows])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Takeoff Summary")

        if not df.empty:
            summary = (
                df.groupby(["product_name", "unit"], as_index=False)
                .agg(quantity=("quantity", "sum"), total_cost=("total_cost", "sum"))
            )
            summary.to_excel(writer, index=False, sheet_name="Product Totals")

    return output.getvalue()


def image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


# -----------------------------
# Future AI/OCR Placeholder
# -----------------------------

def ai_assist_placeholder(product_rule: ProductRule) -> Dict[str, Any]:
    """Placeholder for future AI extraction.

    Later this can call an OCR engine plus a vision model to find labels,
    arrows, and likely pipe runs.
    """
    aliases = ", ".join(product_rule.aliases) if product_rule.aliases else "your entered labels"
    return {
        "status": "manual_review_required",
        "message": (
            f"Future AI step will search for labels like: {aliases}. "
            "For this prototype, manually trace the pipe runs with the line tool."
        ),
    }


# -----------------------------
# Tests
# -----------------------------

class TraceTakeoffTests(unittest.TestCase):
    def test_distance_pixels_3_4_5_triangle(self) -> None:
        self.assertEqual(distance_pixels(0, 0, 3, 4), 5)

    def test_calculate_total_length_multiple_segments(self) -> None:
        segments = [
            {"x1": 0, "y1": 0, "x2": 3, "y2": 4},
            {"x1": 0, "y1": 0, "x2": 0, "y2": 10},
        ]
        self.assertEqual(calculate_total_length(segments, feet_per_pixel=2), 30)

    def test_calculate_total_length_rejects_bad_scale(self) -> None:
        with self.assertRaises(ValueError):
            calculate_total_length([], feet_per_pixel=0)

    def test_parse_canvas_lines_empty(self) -> None:
        self.assertEqual(parse_canvas_lines(None), [])
        self.assertEqual(parse_canvas_lines({}), [])

    def test_parse_canvas_lines_extracts_line(self) -> None:
        canvas_json = {
            "objects": [
                {
                    "type": "line",
                    "left": 10,
                    "top": 20,
                    "x1": 0,
                    "y1": 0,
                    "x2": 100,
                    "y2": 50,
                },
                {"type": "circle", "left": 1, "top": 2},
            ]
        }
        self.assertEqual(
            parse_canvas_lines(canvas_json),
            [{"x1": 10.0, "y1": 20.0, "x2": 110.0, "y2": 70.0}],
        )

    def test_make_excel_download_returns_bytes(self) -> None:
        rows = [
            TakeoffRow(
                page_number=1,
                drawing_name="M2.1",
                product_name="6 inch storm drain",
                matched_spec='6" SD',
                quantity=100.0,
                unit="LF",
                unit_cost=18.0,
                total_cost=1800.0,
                confidence="Estimator reviewed",
                notes="Test row",
            )
        ]
        excel_bytes = make_excel_download(rows)
        self.assertGreater(len(excel_bytes), 100)
        self.assertTrue(excel_bytes.startswith(b"PK"))  # XLSX files are zipped packages.

    def test_get_pymupdf_returns_tuple_without_crashing(self) -> None:
        module, error = get_pymupdf()
        self.assertTrue(module is not None or isinstance(error, str))

    def test_require_pymupdf_error_is_clear_when_unavailable(self) -> None:
        module, error = get_pymupdf()
        if module is None:
            with self.assertRaises(RuntimeError) as ctx:
                require_pymupdf()
            self.assertIn("PyMuPDF", str(ctx.exception))
            self.assertTrue(error)

    def test_export_marked_image_preview_returns_image(self) -> None:
        image = Image.new("RGB", (200, 100), "white")
        segments = [{"x1": 0, "y1": 0, "x2": 100, "y2": 50}]
        preview = export_marked_image_preview(image, segments)
        self.assertEqual(preview.size, (200, 100))
        png_bytes = image_to_png_bytes(preview)
        self.assertTrue(png_bytes.startswith(b"\x89PNG"))


def run_tests() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TraceTakeoffTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)


# -----------------------------
# Streamlit App
# -----------------------------

def render_dependency_panel() -> bool:
    """Return True when the app can continue, False when setup is incomplete."""
    if st is None:
        print(
            "Streamlit is not installed. Run: pip install streamlit pandas openpyxl pillow streamlit-drawable-canvas PyMuPDF"
        )
        return False

    missing: List[str] = []
    if st_canvas is None:
        missing.append("streamlit-drawable-canvas")

    pymupdf, pymupdf_error = get_pymupdf()
    if pymupdf is None:
        st.error("PDF engine setup needed")
        st.write(pymupdf_error)
        st.code(
            "pip uninstall -y fitz\n"
            "pip install --upgrade PyMuPDF streamlit pandas openpyxl pillow streamlit-drawable-canvas",
            language="bash",
        )
        st.info(
            "After running the commands above, restart Streamlit. "
            "The app is intentionally not crashing now; it is pausing until the PDF renderer is available."
        )
        return False

    if missing:
        st.error("One app dependency is missing")
        st.code(
            "pip install --upgrade " + " ".join(missing),
            language="bash",
        )
        return False

    return True


def run_app() -> None:
    if st is None:
        print("Streamlit is not installed. Run: pip install streamlit")
        return

    st.set_page_config(page_title="TraceTakeoff Prototype", layout="wide")

    st.title("TraceTakeoff Prototype")
    st.caption("A simple takeoff assistant for marking, measuring, and exporting product quantities from mechanical drawings.")

    if not render_dependency_panel():
        return

    if "takeoff_rows" not in st.session_state:
        st.session_state.takeoff_rows = []

    if "product_rules" not in st.session_state:
        st.session_state.product_rules = []

    with st.sidebar:
        st.header("1. Upload Drawing")
        uploaded_pdf = st.file_uploader("Upload PDF drawing set", type=["pdf"])

        st.header("2. Product to Find")
        product_name = st.text_input("Product name", value="6 inch storm drain")
        aliases_raw = st.text_area("Labels/specs to look for", value='6" SD\n6" STORM\n6" STORM DRAIN')
        measurement_type = st.selectbox("Measurement type", ["length", "count", "area"], index=0)
        unit = st.text_input("Unit", value="LF")
        unit_cost = st.number_input("Unit cost", min_value=0.0, value=18.0, step=1.0)

        st.header("3. Drawing Scale")
        st.write("For this prototype, enter how many feet each pixel represents.")
        st.caption("Example: adjust this after testing with a known dimension.")
        feet_per_pixel = st.number_input("Feet per pixel", min_value=0.0001, value=0.05, step=0.01, format="%.4f")

        if st.button("Save Product Rule"):
            rule = ProductRule(
                product_name=product_name.strip(),
                aliases=[a.strip() for a in aliases_raw.splitlines() if a.strip()],
                unit=unit.strip(),
                measurement_type=measurement_type,
                unit_cost=float(unit_cost),
            )
            st.session_state.product_rules.append(rule)
            st.success("Product rule saved.")

    if not uploaded_pdf:
        st.info("Upload a PDF drawing set to begin.")
        return

    pdf_bytes = uploaded_pdf.read()

    try:
        pages = render_pdf_pages(pdf_bytes)
    except Exception as exc:
        st.error(f"Could not read this PDF: {exc}")
        return

    if not pages:
        st.error("This PDF did not render any pages.")
        return

    left, right = st.columns([2, 1])

    with right:
        st.subheader("Product Rule")
        active_rule = ProductRule(
            product_name=product_name.strip(),
            aliases=[a.strip() for a in aliases_raw.splitlines() if a.strip()],
            unit=unit.strip(),
            measurement_type=measurement_type,
            unit_cost=float(unit_cost),
        )

        st.json(asdict(active_rule))

        ai_status = ai_assist_placeholder(active_rule)
        st.warning(ai_status["message"])

        page_number = st.selectbox("Select drawing page", list(range(1, len(pages) + 1)))
        drawing_name = st.text_input("Drawing name / sheet number", value=f"Page {page_number}")

    with left:
        st.subheader(f"Drawing Page {page_number}")
        page_img = pages[page_number - 1]

        max_canvas_width = 1000
        scale = min(max_canvas_width / page_img.width, 1.0)
        display_width = max(1, int(page_img.width * scale))
        display_height = max(1, int(page_img.height * scale))
        display_img = page_img.resize((display_width, display_height))

        st.write("Use the line tool to trace the product runs that should be counted.")

        canvas_result = st_canvas(
            fill_color="rgba(255, 0, 0, 0.3)",
            stroke_width=4,
            stroke_color="#ff0000",
            background_image=display_img,
            update_streamlit=True,
            height=display_height,
            width=display_width,
            drawing_mode="line",
            key=f"canvas_page_{page_number}",
        )

    line_segments = parse_canvas_lines(canvas_result.json_data if canvas_result else {})
    adjusted_feet_per_pixel = feet_per_pixel / scale
    quantity = calculate_total_length(line_segments, adjusted_feet_per_pixel)
    total_cost = quantity * unit_cost

    st.divider()

    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    summary_col1.metric("Segments traced", len(line_segments))
    summary_col2.metric("Quantity", f"{quantity:,.2f} {unit}")
    summary_col3.metric("Unit cost", f"${unit_cost:,.2f}")
    summary_col4.metric("Total cost", f"${total_cost:,.2f}")

    notes = st.text_area("Estimator notes", value="Manual trace from prototype review.")

    if st.button("Add This Page to Takeoff Summary"):
        row = TakeoffRow(
            page_number=page_number,
            drawing_name=drawing_name,
            product_name=active_rule.product_name,
            matched_spec=", ".join(active_rule.aliases),
            quantity=round(quantity, 2),
            unit=active_rule.unit,
            unit_cost=active_rule.unit_cost,
            total_cost=round(total_cost, 2),
            confidence="Estimator reviewed",
            notes=notes,
        )
        st.session_state.takeoff_rows.append(row)
        st.success("Added to takeoff summary.")

    st.subheader("Master Takeoff Summary")

    if st.session_state.takeoff_rows:
        df = pd.DataFrame([asdict(row) for row in st.session_state.takeoff_rows])
        st.dataframe(df, use_container_width=True)

        excel_bytes = make_excel_download(st.session_state.takeoff_rows)
        st.download_button(
            label="Download Excel Takeoff Workbook",
            data=excel_bytes,
            file_name="takeoff_summary.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        st.info("No takeoff rows yet. Trace a page and add it to the summary.")

    if line_segments:
        try:
            marked_pdf = export_marked_pdf(
                pdf_bytes=pdf_bytes,
                page_index=page_number - 1,
                line_segments=line_segments,
                image_width=display_width,
                image_height=display_height,
            )

            st.download_button(
                label="Download Marked-Up PDF",
                data=marked_pdf,
                file_name="marked_takeoff.pdf",
                mime="application/pdf",
            )
        except Exception as exc:
            st.warning(f"Could not create marked-up PDF: {exc}")
            preview = export_marked_image_preview(display_img, line_segments)
            st.download_button(
                label="Download Marked-Up PNG Preview Instead",
                data=image_to_png_bytes(preview),
                file_name="marked_takeoff_preview.png",
                mime="image/png",
            )

    st.caption("Prototype note: Auto-detection of labels, arrows, and pipe runs should be added after this manual review workflow is validated with real drawings.")


if __name__ == "__main__":
    if "--run-tests" in sys.argv:
        run_tests()
    else:
        run_app()
