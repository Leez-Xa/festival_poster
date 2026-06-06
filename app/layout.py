from __future__ import annotations


CANVAS_SIZE = (1080, 1920)

TEMPLATE_V1_VERTICAL_STANDARD = {
    "id": "template_v1_vertical_standard",
    "canvas": {"width": 1080, "height": 1920},
    "safe_area": {"top": 60, "right": 60, "bottom": 60, "left": 60},
    "logo": {
        "box": (72, 72, 292, 160),
        "max_size": (220, 88),
    },
    "title": {
        "box": (72, 250, 832, 420),
        "font_size": 72,
        "min_font_size": 56,
        "max_lines": 2,
        "line_gap": 10,
        "fill": "#FFFFFF",
    },
    "subtitle": {
        "box": (72, 440, 832, 550),
        "font_size": 34,
        "min_font_size": 28,
        "max_lines": 2,
        "line_gap": 8,
        "fill": "#F7FAFC",
    },
    "contact": {
        "box": (72, 1590, 732, 1730),
        "font_size": 30,
        "min_font_size": 24,
        "max_lines": 2,
        "line_gap": 6,
        "fill": "#FFFFFF",
    },
    "qrcode": {
        "card_box": (778, 1512, 1020, 1760),
        "image_box": (790, 1524, 1008, 1742),
        "target_size": 218,
        "min_size": 180,
        "caption_y": 1712,
    },
    "bottom_bar": {
        "box": (0, 1760, 1080, 1920),
    },
    "overlay": {
        "top_height": 600,
        "top_alpha": 92,
        "bottom_start_y": 1380,
        "bottom_alpha": 144,
    },
}


def get_template_layout(template_id: str) -> dict[str, object]:
    if template_id == TEMPLATE_V1_VERTICAL_STANDARD["id"]:
        return TEMPLATE_V1_VERTICAL_STANDARD
    raise ValueError(f"Unsupported template_id: {template_id}")
