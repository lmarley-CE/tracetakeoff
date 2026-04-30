"""
TraceTakeoff Prototype — Comprehensive Streamlit App

Purpose:
A simple prototype tool for mechanical estimating takeoffs.

Core workflow:
1. Estimator types what they need in plain English.
2. Assistant converts that instruction into product rules and takeoff notes.
3. Estimator uploads a PDF drawing set.
4. Estimator manually traces matching pipe/product runs in red.
5. App calculates quantity using a drawing calibration value.
6. App exports a marked PDF/PNG and Excel takeoff workbook.

Important:
This version is intentionally human-reviewed. It does NOT yet auto-detect and auto-highlight
pipe from drawings. That should be the next phase after the manual workflow is proven.

GitHub file name:
    streamlit_app.py

requirements.txt:
    streamlit
    PyMuPDF
    pandas
    openpyxl
    pillow
    streamlit-drawable-canvas

Run locally:
    pip uninstall -y fitz
    pip install --upgrade PyMuPDF streamlit pandas openpyxl pillow streamlit-drawable-canvas
    streamlit run streamlit_app.py

Run tests:
    python streamlit_app.py --run-tests
"""

from __future__ import annotations

import importlib
import io
import math
import re
import sys
import unittest
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from PIL import Image, ImageDraw

try:
    import streamlit as st
except ModuleNotFoundError:
    st = None  # type: ignore

try:
    from streamlit_drawable_canvas import st_canvas
except ModuleNotFoundError:
    st_canvas = None  # type: ignore


def patch_streamlit_drawable_canvas_image_helper() -> Optional[str]:
    """Patch a Streamlit compatibility issue used by streamlit-drawable-canvas.

    Problem:
    Newer Streamlit versions moved `image_to_url` from
    `streamlit.elements.image` to `streamlit.elements.lib.image_utils`.
    The streamlit-drawable-canvas package still calls the old location, which
    causes an AttributeError when a background image is passed to st_canvas.

    Returns:
        None if patched/already compatible, otherwise a short warning message.
    """
    try:
        old_image_module = importlib.import_module("streamlit.elements.image")

        if hasattr(old_image_module, "image_to_url"):
            return None

        image_utils = importlib.import_module("streamlit.elements.lib.image_utils")
        if not hasattr(image_utils, "image_to_url"):
            return "Streamlit image helper was not found in the expected compatibility location."

        old_image_module.image_to_url = image_utils.image_to_url  # type: ignore[attr-defined]
        return None
    except Exception as exc:
        return f"


# ======================================================
# Data Models
# ======================================================

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
    project_name: str
    estimator_name: str
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


@dataclass
class EstimatorInstruction:
    raw_instruction: str
    suggested_product_name: str
    suggested_aliases: List[str]
    suggested_unit: str
    suggested_measurement_type: str
    suggested_unit_cost: float
    things_to_watch_for: List[str]
    calculation_notes: str


# ======================================================
# Estimator Assistant — Rule-Based AI Helper
# ======================================================

HELPER_EXAMPLE = (
    'Find all 6 inch storm drain labeled "6 SD" or "6 STORM". '
    'Measure in LF at $18/LF. Watch for leader arrows and dashed continuation lines.'
)


def split_aliases_from_text(text: str) -> List[str]:
    """Extract likely drawing labels/specs from estimator text.

    This is intentionally lightweight so the prototype works without a paid AI API.
    Later, this function can be replaced with a true LLM parser.
    """
    aliases: List[str] = []

    quoted_matches = re.findall(r'"([^"\n]{1,50})"', text)
    for match in quoted_matches:
        cleaned = match.strip()
        if cleaned and cleaned not in aliases:
            aliases.append(cleaned)

    common_patterns = [
        r"\b\d+\s*(?:in|inch|\")\s*[A-Z]{1,10}\b",
        r"\b\d+\s*(?:in|inch|\")\s*(?:storm|sanitary|domestic|water|drain|sd|cw|hw|ss|condensate)\b",
        r"\b(?:CO|FD|RD|SD|SS|CW|HW|CD)\b",
    ]

    for pattern in common_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            cleaned = match.strip().upper()
            cleaned = cleaned.replace(" INCH", '"').replace(" IN", '"')
            if cleaned and cleaned not in aliases:
                aliases.append(cleaned)

    return aliases[:10]


def infer_unit(text: str) -> str:
    lowered = text.lower()
    if any(term in lowered for term in ["linear feet", "lineal feet", " lf", "/lf", "per lf"]):
        return "LF"
    if any(term in lowered for term in ["square feet", " sf", "/sf", "per sf"]):
        return "SF"
    if any(term in lowered for term in ["count", "each", " ea", "/ea", "per each", "quantity of"]):
        return "EA"
    return "LF"


def infer_measurement_type(unit: str, text: str) -> str:
    lowered = text.lower()
    if unit == "EA" or any(term in lowered for term in ["count", "each", "how many"]):
        return "count"
    if unit == "SF" or any(term in lowered for term in ["area", "square feet"]):
        return "area"
    return "length"


def infer_unit_cost(text: str) -> float:
    patterns = [
        r"\$\s*(\d+(?:\.\d{1,2})?)\s*(?:/|per)\s*(?:lf|ea|sf|linear foot|linear feet|each|square foot|square feet)",
        r"(\d+(?:\.\d{1,2})?)\s*dollars?\s*(?:/|per)\s*(?:lf|ea|sf|linear foot|linear feet|each|square foot|square feet)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return 0.0


def infer_product_name(text: str, aliases: List[str]) -> str:
    lowered = text.lower()

    size_match = re.search(r"\b(\d+)\s*(?:inch|in|\")", lowered)
    size = f"{size_match.group(1)} inch" if size_match else ""

    product_terms = [
        "storm drain",
        "sanitary sewer",
        "domestic water",
        "cold water",
        "hot water",
        "condensate drain",
        "pipe insulation",
        "duct insulation",
        "cleanout",
        "floor drain",
        "roof drain",
        "valve",
        "fitting",
        "pipe",
    ]

    product = ""
    for term in product_terms:
        if term in lowered:
            product = term
            break

    if size and product:
        return f"{size} {product}"
    if product:
        return product
    if aliases:
        return aliases[0]
    return "New product takeoff"


def extract_watch_items(text: str) -> List[str]:
    lowered = text.lower()
    watch_items: List[str] = []

    watch_dictionary = {
        "leader arrows": ["leader", "arrow", "arrows"],
        "dashed continuation lines": ["dashed", "hidden", "continuation"],
        "risers or vertical drops": ["riser", "vertical", "drop", "drops"],
        "fittings and cleanouts": ["fitting", "fittings", "cleanout", "cleanouts"],
        "sheet match lines": ["match line", "matchline", "continued"],
        "alternate labels or abbreviations": ["abbreviation", "abbreviations", "also labeled", "alternate"],
        "existing-to-remain notes": ["existing", "remain", "demo", "demolition"],
    }

    for label, keywords in watch_dictionary.items():
        if any(keyword in lowered for keyword in keywords):
            watch_items.append(label)

    if not watch_items:
        watch_items.append("Estimator should confirm highlighted runs before export")

    return watch_items


def parse_estimator_instruction(text: str) -> EstimatorInstruction:
    aliases = split_aliases_from_text(text)
    unit = infer_unit(text)
    measurement_type = infer_measurement_type(unit, text)
    unit_cost = infer_unit_cost(text)
    product_name = infer_product_name(text, aliases)
    watch_items = extract_watch_items(text)

    calculation_notes = (
        f"Measure as {measurement_type} using unit {unit}. "
        "Estimator must review marked runs before adding to final summary."
    )
    if unit_cost > 0:
        calculation_notes += f" Suggested unit cost: ${unit_cost:,.2f}/{unit}."

    return EstimatorInstruction(
        raw_instruction=text,
        suggested_product_name=product_name,
        suggested_aliases=aliases,
        suggested_unit=unit,
        suggested_measurement_type=measurement_type,
        suggested_unit_cost=unit_cost,
        things_to_watch_for=watch_items,
        calculation_notes=calculation_notes,
    )


def build_instruction_notes(instruction: EstimatorInstruction) -> str:
    return (
        f"Estimator instruction: {instruction.raw_instruction}\n\n"
        f"Things to watch for: {'; '.join(instruction.things_to_watch_for)}\n\n"
        f"Calculation notes: {instruction.calculation_notes}"
    )


# ======================================================
# PDF Engine Helpers
# ======================================================


def get_pymupdf() -> Tuple[Optional[Any], Optional[str]]:
    """Return PyMuPDF if installed correctly, otherwise return a useful error.

    Streamlit Cloud may expose PyMuPDF as either `pymupdf` or `fitz`
    depending on package version. We try `pymupdf` first, then carefully
    try `fitz` only if it looks like the real PyMuPDF package.
    """
    import_errors: List[str] = []

    for module_name in ("pymupdf", "fitz"):
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, "open") and hasattr(module, "Matrix") and hasattr(module, "Document"):
                return module, None
            import_errors.append(f"`{module_name}` imported, but it does not look like PyMuPDF.")
        except ModuleNotFoundError as exc:
            import_errors.append(f"`{module_name}` missing: {exc.name}")
        except Exception as exc:
            import_errors.append(f"`{module_name}` failed: {exc}")

    return None, (
        "PyMuPDF is not available or is conflicted. In requirements.txt, include `PyMuPDF` "
        "and do not include `fitz`. Then commit and reboot Streamlit. Details: "
        + " | ".join(import_errors)
    )


def require_pymupdf() -> Any:
    pymupdf, error = get_pymupdf()
    if pymupdf is None:
        raise RuntimeError(error or "PyMuPDF is unavailable.")
    return pymupdf


def render_pdf_pages_uncached(pdf_bytes: bytes, zoom: float = 1.5, max_pages: int = 50) -> List[Image.Image]:
    """Render PDF pages to images.

    Lower zoom keeps large construction drawings from exhausting Streamlit Cloud memory.
    max_pages prevents one massive drawing set from crashing the prototype.
    """
    if not pdf_bytes:
        raise ValueError("No PDF bytes were provided. Try uploading the PDF again.")

    fitz = require_pymupdf()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ValueError(
            "This file could not be opened as a PDF. Make sure it is a valid, uncorrupted PDF file. "
            f"Details: {exc}"
        ) from exc

    images: List[Image.Image] = []

    try:
        if getattr(doc, "needs_pass", False):
            raise ValueError("This PDF is password-protected. Please upload an unlocked copy.")

        page_count = len(doc)
        if page_count == 0:
            raise ValueError("This PDF does not contain any pages.")

        pages_to_render = min(page_count, max_pages)
        for page_index in range(pages_to_render):
            page = doc[page_index]
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            images.append(img)
    finally:
        doc.close()

    return images


if st is not None:
    @st.cache_data(show_spinner=False)
    def render_pdf_pages(pdf_bytes: bytes, zoom: float = 1.5) -> List[Image.Image]:
        return render_pdf_pages_uncached(pdf_bytes, zoom)
else:
    def render_pdf_pages(pdf_bytes: bytes, zoom: float = 1.5) -> List[Image.Image]:
        return render_pdf_pages_uncached(pdf_bytes, zoom)


def export_marked_pdf(
    pdf_bytes: bytes,
    page_index: int,
    line_segments: List[Dict[str, float]],
    image_width: int,
    image_height: int,
) -> bytes:
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


def export_marked_image_preview(image: Image.Image, line_segments: List[Dict[str, float]]) -> Image.Image:
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


# ======================================================
# Measurement Helpers
# ======================================================


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


# ======================================================
# Export Helpers
# ======================================================


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


# ======================================================
# Future Auto-Detection Placeholder
# ======================================================


def ai_assist_placeholder(product_rule: ProductRule, instruction: Optional[EstimatorInstruction] = None) -> Dict[str, Any]:
    aliases = ", ".join(product_rule.aliases) if product_rule.aliases else "your entered labels"
    watch_text = ""
    if instruction:
        watch_text = " Watch for: " + "; ".join(instruction.things_to_watch_for) + "."

    return {
        "status": "manual_review_required",
        "message": (
            f"Search guidance: look for labels/specs like {aliases}. "
            "For this prototype, manually trace the product runs with the red line tool."
            f"{watch_text}"
        ),
    }


# ======================================================
# Tests
# ======================================================

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
                {"type": "line", "left": 10, "top": 20, "x1": 0, "y1": 0, "x2": 100, "y2": 50},
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
                project_name="Test Project",
                estimator_name="Estimator",
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
        self.assertTrue(excel_bytes.startswith(b"PK"))

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

    def test_parse_estimator_instruction_extracts_storm_drain(self) -> None:
        instruction = parse_estimator_instruction(
            'Find all 6 inch storm drain labeled "6 SD" or "6 STORM". Measure in LF at $18/LF. Watch for leader arrows and dashed continuation lines.'
        )
        self.assertEqual(instruction.suggested_product_name, "6 inch storm drain")
        self.assertIn("6 SD", instruction.suggested_aliases)
        self.assertIn("6 STORM", instruction.suggested_aliases)
        self.assertEqual(instruction.suggested_unit, "LF")
        self.assertEqual(instruction.suggested_measurement_type, "length")
        self.assertEqual(instruction.suggested_unit_cost, 18.0)
        self.assertIn("leader arrows", instruction.things_to_watch_for)
        self.assertIn("dashed continuation lines", instruction.things_to_watch_for)

    def test_parse_estimator_instruction_infers_count(self) -> None:
        instruction = parse_estimator_instruction("Count all cleanouts labeled CO at $75 per each.")
        self.assertEqual(instruction.suggested_unit, "EA")
        self.assertEqual(instruction.suggested_measurement_type, "count")

    def test_canvas_compatibility_patch_does_not_crash(self) -> None:
        warning = patch_streamlit_drawable_canvas_image_helper()
        self.assertTrue(warning is None or isinstance(warning


def run_tests() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TraceTakeoffTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if not result.wasSuccessful():
        sys.exit(1)


# ======================================================
# Streamlit App
# ======================================================


def render_dependency_panel() -> bool:
    if st is None:
        print("Streamlit is not installed. Run: pip install streamlit")
        return False

    canvas_patch_warning = patch_streamlit_drawable_canvas_image_helper()
    if canvas_patch_warning:
        st.warning(canvas_patch_warning)

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
            "For Streamlit Cloud, put these packages in requirements.txt, commit to GitHub, then reboot the app."
        )
        return False

    if missing:
        st.error("One app dependency is missing")
        st.code("pip install --upgrade " + " ".join(missing), language="bash")
        return False

    return True


def initialize_state() -> None:
    if "takeoff_rows" not in st.session_state:
        st.session_state.takeoff_rows = []

    if "product_rules" not in st.session_state:
        st.session_state.product_rules = []

    if "assistant_messages" not in st.session_state:
        st.session_state.assistant_messages = [
            {
                "role": "assistant",
                "content": (
                    "Tell me what you need to take off. Example: " + HELPER_EXAMPLE
                ),
            }
        ]

    if "current_instruction" not in st.session_state:
        st.session_state.current_instruction = None

    if "assistant_notes" not in st.session_state:
        st.session_state.assistant_notes = "Manual trace from prototype review."


def get_current_defaults() -> Dict[str, Any]:
    defaults = {
        "product_name": "6 inch storm drain",
        "aliases_raw": '6" SD\n6" STORM\n6" STORM DRAIN',
        "unit": "LF",
        "measurement_type": "length",
        "unit_cost": 18.0,
    }

    instruction = st.session_state.get("current_instruction")
    if instruction:
        defaults["product_name"] = instruction.suggested_product_name
        defaults["aliases_raw"] = "\n".join(instruction.suggested_aliases) or defaults["aliases_raw"]
        defaults["unit"] = instruction.suggested_unit
        defaults["measurement_type"] = instruction.suggested_measurement_type
        defaults["unit_cost"] = instruction.suggested_unit_cost

    return defaults


def run_app() -> None:
    if st is None:
        print("Streamlit is not installed. Run: pip install streamlit")
        return

    st.set_page_config(page_title="TraceTakeoff", layout="wide")
    st.title("TraceTakeoff")
    st.caption("Simple takeoff support for mechanical drawings — prototype version")

    if not render_dependency_panel():
        return

    initialize_state()

    st.info(
        "Workflow: Describe the takeoff → upload drawings → trace the matching runs → review quantity → export Excel/PDF."
    )

    defaults = get_current_defaults()

    with st.sidebar:
        st.header("Project")
        project_name = st.text_input("Project name", value="Test Project")
        estimator_name = st.text_input("Estimator name", value="Estimator")

        st.header("Upload")
        uploaded_pdf = st.file_uploader("Upload PDF drawing set", type=["pdf"])

        st.header("Product Setup")
        product_name = st.text_input("Product name", value=defaults["product_name"])
        aliases_raw = st.text_area("Labels/specs to look for", value=defaults["aliases_raw"])

        measurement_options = ["length", "count", "area"]
        measurement_index = measurement_options.index(defaults["measurement_type"]) if defaults["measurement_type"] in measurement_options else 0
        measurement_type = st.selectbox("Measurement type", measurement_options, index=measurement_index)
        unit = st.text_input("Unit", value=defaults["unit"])
        unit_cost = st.number_input("Unit cost", min_value=0.0, value=float(defaults["unit_cost"]), step=1.0)

        st.header("Scale")
        feet_per_pixel = st.number_input(
            "Calibration: feet per screen pixel",
            min_value=0.0001,
            value=0.05,
            step=0.01,
            format="%.4f",
            help="Prototype calibration. Later this should become click-two-points calibration.",
        )

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

    assistant_tab, takeoff_tab, summary_tab = st.tabs([
        "Estimator Assistant",
        "Takeoff Workspace",
        "Summary & Export",
    ])

    with assistant_tab:
        st.subheader("Estimator Assistant")
        st.write(
            "Type the request the way an estimator would say it. The assistant will draft product rules, labels, costs, and notes."
        )

        for message in st.session_state.assistant_messages:
            with st.chat_message(message["role"]):
                st.write(message["content"])

        prompt = st.chat_input(HELPER_EXAMPLE)

        if prompt:
            st.session_state.assistant_messages.append({"role": "user", "content": prompt})
            parsed_instruction = parse_estimator_instruction(prompt)
            st.session_state.current_instruction = parsed_instruction
            st.session_state.assistant_notes = build_instruction_notes(parsed_instruction)

            assistant_reply = (
                "Got it. I drafted this setup:\n\n"
                f"**Product:** {parsed_instruction.suggested_product_name}\n\n"
                f"**Labels to look for:** {', '.join(parsed_instruction.suggested_aliases) if parsed_instruction.suggested_aliases else 'No specific labels found yet'}\n\n"
                f"**Measurement:** {parsed_instruction.suggested_measurement_type} ({parsed_instruction.suggested_unit})\n\n"
                f"**Unit cost:** ${parsed_instruction.suggested_unit_cost:,.2f}\n\n"
                f"**Things to watch for:** {'; '.join(parsed_instruction.things_to_watch_for)}\n\n"
                "Review the sidebar fields, then go to the Takeoff Workspace."
            )
            st.session_state.assistant_messages.append({"role": "assistant", "content": assistant_reply})
            st.rerun()

        if st.session_state.current_instruction:
            current = st.session_state.current_instruction
            st.divider()
            st.subheader("Current Takeoff Setup")
            col1, col2, col3 = st.columns(3)
            col1.metric("Product", current.suggested_product_name)
            col2.metric("Unit", current.suggested_unit)
            col3.metric("Unit Cost", f"${current.suggested_unit_cost:,.2f}")

            st.write("Labels/specs to look for:")
            st.code("\n".join(current.suggested_aliases) if current.suggested_aliases else "No labels extracted yet")

            st.write("Things to keep in mind:")
            for item in current.things_to_watch_for:
                st.write(f"- {item}")

    with takeoff_tab:
        if not uploaded_pdf:
            st.info("Upload a PDF drawing set in the sidebar to begin.")
            return

        pdf_bytes = uploaded_pdf.getvalue()
        st.caption(f"Uploaded file: {uploaded_pdf.name} • {len(pdf_bytes) / (1024 * 1024):.2f} MB")

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
            active_rule = ProductRule(
                product_name=product_name.strip(),
                aliases=[a.strip() for a in aliases_raw.splitlines() if a.strip()],
                unit=unit.strip(),
                measurement_type=measurement_type,
                unit_cost=float(unit_cost),
            )

            st.subheader("Current Product Rule")
            st.json(asdict(active_rule))

            guidance = ai_assist_placeholder(active_rule, st.session_state.current_instruction)
            st.warning(guidance["message"])

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

            st.write("Trace the matching product runs in red.")

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
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Pipe runs marked", len(line_segments))
        col2.metric("Quantity", f"{quantity:,.2f} {unit}")
        col3.metric("Unit cost", f"${unit_cost:,.2f}")
        col4.metric("Total cost", f"${total_cost:,.2f}")

        notes = st.text_area("Estimator notes", value=st.session_state.assistant_notes)

        if st.button("Add This Page to Takeoff Summary"):
            row = TakeoffRow(
                project_name=project_name,
                estimator_name=estimator_name,
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

    with summary_tab:
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

    st.caption("Next build phase: OCR + vision-assisted label detection, arrow following, and click-to-confirm suggested pipe runs.")


if __name__ == "__main__":
    if "--run-tests" in sys.argv:
        run_tests()
    else:
        run_app()


