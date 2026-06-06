from __future__ import annotations


CANVAS_SIZE = (1080, 1920)

TEMPLATE_V1_VERTICAL_STANDARD = {
    "id": "template_v1_vertical_standard",
    "canvas": {"width": 1080, "height": 1920},
    "safe_area": {"top": 60, "right": 60, "bottom": 60, "left": 60},
    "scene_image": {
        "box": (0, 0, 1080, 1920),
        "fit": "cover",
        "position": "center",
        "no_stretch": True,
        "preferred_size": (1080, 1920),
    },
    "logo": {
        "box": (72, 72, 332, 166),
        "max_size": (228, 82),
        "min_readable_width": 160,
    },
    "title": {
        "box": (72, 224, 900, 392),
        "font_size": 72,
        "min_font_size": 48,
        "max_lines": 2,
        "line_gap": 10,
        "fill": "#FFFFFF",
    },
    "subtitle": {
        "box": (72, 412, 900, 524),
        "font_size": 34,
        "min_font_size": 26,
        "max_lines": 2,
        "line_gap": 8,
        "fill": "#F7FAFC",
    },
    "contact": {
        "box": (72, 1570, 740, 1732),
        "font_size": 30,
        "min_font_size": 22,
        "max_lines": 2,
        "line_gap": 6,
        "fill": "#FFFFFF",
    },
    "qrcode": {
        "card_box": (778, 1508, 1020, 1754),
        "image_box": (800, 1520, 1008, 1728),
        "target_size": 208,
        "min_size": 180,
        "caption_y": 1728,
    },
    "bottom_bar": {
        "box": (0, 1760, 1080, 1920),
    },
    "overlay": {
        "top_height": 620,
        "top_alpha": 132,
        "bottom_start_y": 1370,
        "bottom_alpha": 172,
    },
    "rules": {
        "qrcode_min_size": 180,
        "logo_min_width": 160,
        "text_contrast": "gradient_scrim_and_shadow",
        "overlap_strategy": "shrink_text_then_clip_to_max_lines",
    },
}


def get_template_layout(template_id: str) -> dict[str, object]:
    if template_id == TEMPLATE_V1_VERTICAL_STANDARD["id"]:
        return TEMPLATE_V1_VERTICAL_STANDARD
    raise ValueError(f"Unsupported template_id: {template_id}")
