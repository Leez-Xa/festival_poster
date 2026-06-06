from __future__ import annotations

import base64
import json
import logging
import random
import re
import urllib.error
import urllib.request
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from app.config import AISettings, get_ai_settings
from app.models import PosterTaskCreate

logger = logging.getLogger(__name__)


DEFAULT_CANVAS_SIZE = (1080, 1920)


class AiProviderError(Exception):
    """Raised when the AI adapter cannot return a usable result."""


@dataclass
class CopyGenerationResult:
    title: str
    subtitle: str
    alternatives: list[dict[str, str]] = field(default_factory=list)
    risk_level: str = "low"
    provider: str = "mock"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "alternatives": self.alternatives,
            "risk_level": self.risk_level,
            "provider": self.provider,
        }


@dataclass
class ScenePromptResult:
    positive_prompt: str
    negative_prompt: str
    provider: str = "mock"
    model: str = "local"

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "provider": self.provider,
            "model": self.model,
        }


@dataclass
class SceneFusionResult:
    image_path: Path
    prompt: str
    negative_prompt: str
    provider: str = "mock"
    model: str = "local"
    mode: str = "local_mock"
    prompt_provider: str = "mock"
    used_product_asset_id: str = ""
    preserve_product_pixels: bool = True
    warnings: list[str] = field(default_factory=list)


@dataclass
class BackgroundGenerationResult:
    image_path: Path
    prompt: str
    negative_prompt: str
    provider: str = "mock"
    model: str = "local"


class AiProvider:
    def __init__(self, settings: AISettings) -> None:
        self.settings = settings

    def generate_copy(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> CopyGenerationResult:
        return self._run_with_timeout(
            "generate_copy",
            lambda: self._generate_copy(node=node, product=product, payload=payload),
            timeout_seconds=self.settings.text_timeout_seconds,
        )

    def generate_scene_prompt(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> ScenePromptResult:
        return self._run_with_timeout(
            "generate_scene_prompt",
            lambda: self._generate_scene_prompt(node=node, product=product, payload=payload),
            timeout_seconds=self.settings.text_timeout_seconds,
        )

    def generate_scene_with_product(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
        transparent_product_png: Path,
        product_asset_id: str,
        output_dir: Path,
        scene_reference_path: Path | None = None,
        canvas_size: tuple[int, int] = DEFAULT_CANVAS_SIZE,
        safe_zones: dict[str, tuple[int, int, int, int]] | None = None,
    ) -> SceneFusionResult:
        try:
            prompt_result = self.generate_scene_prompt(node=node, product=product, payload=payload)
        except AiProviderError:
            prompt_result = build_local_scene_prompt(node=node, product=product, payload=payload)

        return self._run_with_timeout(
            "generate_scene_with_product",
            lambda: self._generate_scene_with_product(
                node=node,
                product=product,
                payload=payload,
                transparent_product_png=transparent_product_png,
                product_asset_id=product_asset_id,
                output_dir=output_dir,
                scene_reference_path=scene_reference_path,
                canvas_size=canvas_size,
                safe_zones=safe_zones or default_safe_zones(),
                prompt_result=prompt_result,
            ),
            timeout_seconds=self.settings.image_timeout_seconds,
        )

    def generate_background_only(
        self,
        *,
        node: dict[str, Any],
        payload: PosterTaskCreate,
        output_dir: Path,
    ) -> BackgroundGenerationResult:
        return self._run_with_timeout(
            "generate_background_only",
            lambda: self._generate_background_only(node=node, payload=payload, output_dir=output_dir),
            timeout_seconds=self.settings.image_timeout_seconds,
        )

    def rewrite_copy(
        self,
        *,
        title: str,
        subtitle: str,
        issues: list[dict[str, Any]],
    ) -> tuple[str, str]:
        return self._run_with_timeout(
            "rewrite_copy",
            lambda: self._rewrite_copy(title=title, subtitle=subtitle, issues=issues),
            timeout_seconds=self.settings.text_timeout_seconds,
        )

    def _run_with_timeout(self, operation: str, fn: Any, timeout_seconds: int) -> Any:
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(fn)
            return future.result(timeout=timeout_seconds)
        except TimeoutError as exc:
            logger.warning(
                "AI provider timeout: %s, context=%s",
                operation,
                self.settings.safe_log_context(),
            )
            raise AiProviderError(f"AI调用超时：{operation}") from exc
        except AiProviderError:
            raise
        except Exception as exc:
            logger.warning(
                "AI provider failed: %s, context=%s, error=%s",
                operation,
                self.settings.safe_log_context(),
                exc,
            )
            raise AiProviderError(f"AI调用失败：{operation}") from exc
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _generate_copy(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> CopyGenerationResult:
        raise NotImplementedError

    def _generate_scene_prompt(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> ScenePromptResult:
        raise NotImplementedError

    def _generate_scene_with_product(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
        transparent_product_png: Path,
        product_asset_id: str,
        output_dir: Path,
        scene_reference_path: Path | None,
        canvas_size: tuple[int, int],
        safe_zones: dict[str, tuple[int, int, int, int]],
        prompt_result: ScenePromptResult,
    ) -> SceneFusionResult:
        raise NotImplementedError

    def _generate_background_only(
        self,
        *,
        node: dict[str, Any],
        payload: PosterTaskCreate,
        output_dir: Path,
    ) -> BackgroundGenerationResult:
        raise NotImplementedError

    def _rewrite_copy(self, *, title: str, subtitle: str, issues: list[dict[str, Any]]) -> tuple[str, str]:
        raise NotImplementedError


class MockBridgeAiProvider(AiProvider):
    """Local mock provider used when external image editing contracts are unavailable."""

    def __init__(self, settings: AISettings) -> None:
        super().__init__(settings)
        if settings.has_text_credentials or settings.has_image_credentials:
            logger.info(
                "AI credentials detected, using mock adapter until fusion request schema is wired. context=%s",
                settings.safe_log_context(),
            )

    def _generate_copy(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> CopyGenerationResult:
        title = payload.copy_preference.title.strip() or f"{node['name']}已至"
        subtitle = payload.copy_preference.subtitle.strip() or "健康饮水，陪伴每一天"
        product_hint = product.get("model") or product["name"]
        alternatives = [
            {
                "title": f"{node['name']}好水相伴",
                "subtitle": f"{product_hint}融入节日场景，适合朋友圈传播",
            },
            {
                "title": f"{node['name']}焕新饮水",
                "subtitle": "让真实产品自然出现在节日氛围之中",
            },
        ]
        return CopyGenerationResult(
            title=title[:18],
            subtitle=subtitle[:42],
            alternatives=alternatives,
            risk_level="low",
            provider="mock_text",
        )

    def _generate_scene_prompt(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> ScenePromptResult:
        return build_local_scene_prompt(node=node, product=product, payload=payload)

    def _generate_scene_with_product(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
        transparent_product_png: Path,
        product_asset_id: str,
        output_dir: Path,
        scene_reference_path: Path | None,
        canvas_size: tuple[int, int],
        safe_zones: dict[str, tuple[int, int, int, int]],
        prompt_result: ScenePromptResult,
    ) -> SceneFusionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / "ai_scene_mock.png"
        create_mock_scene_with_product(
            path=image_path,
            node=node,
            product_image_path=transparent_product_png,
            scene_reference_path=scene_reference_path,
            canvas_size=canvas_size,
            safe_zones=safe_zones,
        )
        return SceneFusionResult(
            image_path=image_path,
            prompt=prompt_result.positive_prompt,
            negative_prompt=prompt_result.negative_prompt,
            provider="mock_image",
            model="local_mock",
            mode="local_mock_composite",
            prompt_provider=prompt_result.provider,
            used_product_asset_id=product_asset_id,
            preserve_product_pixels=True,
        )

    def _generate_background_only(
        self,
        *,
        node: dict[str, Any],
        payload: PosterTaskCreate,
        output_dir: Path,
    ) -> BackgroundGenerationResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / "ai_background_mock.jpg"
        prompt = build_background_prompt(node=node, payload=payload)
        negative_prompt = build_negative_prompt()
        create_textless_atmosphere_background(image_path, node)
        return BackgroundGenerationResult(
            image_path=image_path,
            prompt=prompt,
            negative_prompt=negative_prompt,
            provider="mock_image",
            model="local_mock",
        )

    def _rewrite_copy(self, *, title: str, subtitle: str, issues: list[dict[str, Any]]) -> tuple[str, str]:
        blocked_terms = [issue["term"] for issue in issues if issue.get("term")]
        safe_title = title
        safe_subtitle = subtitle
        for term in blocked_terms:
            safe_title = safe_title.replace(term, "")
            safe_subtitle = safe_subtitle.replace(term, "")
        return safe_title.strip() or "节日已至", safe_subtitle.strip() or "健康饮水，陪伴每一天"


class OpenAICompatibleAiProvider(AiProvider):
    def _generate_copy(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> CopyGenerationResult:
        if not self.settings.has_text_credentials:
            raise AiProviderError("文本生成 Key 未配置")

        response = post_openai_compatible_json(
            base_url=self.settings.base_url,
            endpoint="/chat/completions",
            api_key=self.settings.text_api_key,
            body={
                "model": self.settings.text_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是朴道海报文案助手。只返回 JSON，不要返回 Markdown。"
                            "文案必须基于节点和产品信息，避免绝对化和医疗功效宣传。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "生成海报主标题、副标题和备选文案",
                                "required_schema": {
                                    "title": "不超过18个中文字符",
                                    "subtitle": "不超过42个中文字符",
                                    "alternatives": [{"title": "备选标题", "subtitle": "备选副标题"}],
                                    "risk_level": "low|medium|high",
                                },
                                "node": node,
                                "product": product,
                                "scene_prompt": payload.scene_prompt,
                                "custom_requirement": payload.custom_requirement,
                                "copy_preference": payload.copy_preference.model_dump(),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            },
            timeout_seconds=self.settings.text_timeout_seconds,
        )
        content = response["choices"][0]["message"]["content"]
        parsed = parse_json_content(content)
        return CopyGenerationResult(
            title=str(parsed.get("title", "")).strip()[:18],
            subtitle=str(parsed.get("subtitle", "")).strip()[:42],
            alternatives=normalize_alternatives(parsed.get("alternatives", [])),
            risk_level=str(parsed.get("risk_level", "low")).strip() or "low",
            provider="openai_compatible_text",
        )

    def _generate_scene_prompt(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
    ) -> ScenePromptResult:
        if not self.settings.has_text_credentials:
            raise AiProviderError("提示词生成 Key 未配置")

        response = post_openai_compatible_json(
            base_url=self.settings.base_url,
            endpoint="/chat/completions",
            api_key=self.settings.text_api_key,
            body={
                "model": self.settings.scene_prompt_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是海报场景提示词助手。只返回 JSON。"
                            "目标是为图像模型生成正向和负向提示词。"
                            "必须强调：产品主体必须来自用户提供的透明 PNG，不允许重绘产品结构，"
                            "禁止输出文字、Logo、二维码、水印，并为顶部 Logo 区与底部文案二维码区预留安全区。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "生成融合场景图的正向提示词和负向提示词",
                                "required_schema": {
                                    "positive_prompt": "完整中文提示词",
                                    "negative_prompt": "完整中文负向提示词",
                                },
                                "node": node,
                                "product": product,
                                "scene_prompt": payload.scene_prompt,
                                "custom_requirement": payload.custom_requirement,
                                "contact_text": payload.contact_text,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0.6,
                "response_format": {"type": "json_object"},
            },
            timeout_seconds=self.settings.text_timeout_seconds,
        )
        parsed = parse_json_content(response["choices"][0]["message"]["content"])
        positive_prompt = str(parsed.get("positive_prompt", "")).strip()
        negative_prompt = str(parsed.get("negative_prompt", "")).strip()
        if not positive_prompt or not negative_prompt:
            raise AiProviderError("提示词生成结果缺少 positive_prompt 或 negative_prompt")
        return ScenePromptResult(
            positive_prompt=positive_prompt,
            negative_prompt=negative_prompt,
            provider="openai_compatible_text",
            model=self.settings.scene_prompt_model,
        )

    def _generate_scene_with_product(
        self,
        *,
        node: dict[str, Any],
        product: dict[str, Any],
        payload: PosterTaskCreate,
        transparent_product_png: Path,
        product_asset_id: str,
        output_dir: Path,
        scene_reference_path: Path | None,
        canvas_size: tuple[int, int],
        safe_zones: dict[str, tuple[int, int, int, int]],
        prompt_result: ScenePromptResult,
    ) -> SceneFusionResult:
        if not self.settings.has_image_credentials:
            raise AiProviderError("图片生成 Key 未配置")

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / "ai_scene.png"
        prompt = append_image_constraints(
            prompt=prompt_result.positive_prompt,
            negative_prompt=prompt_result.negative_prompt,
            canvas_size=canvas_size,
            safe_zones=safe_zones,
        )
        if self.settings.image_fusion_request_mode == "json_base64":
            body = {
                "model": self.settings.image_model,
                "mode": "image_edit",
                "size": self.settings.image_output_size,
                "prompt": prompt,
                "negative_prompt": prompt_result.negative_prompt,
                "product_image_b64": encode_image_base64(transparent_product_png),
                "scene_reference_b64": encode_image_base64(scene_reference_path) if scene_reference_path else None,
                "canvas_size": list(canvas_size),
                "safe_zones": normalize_safe_zones(safe_zones),
                "constraints": {
                    "preserve_product_pixels": True,
                    "no_text": True,
                    "no_logo": True,
                    "no_qrcode": True,
                    "no_watermark": True,
                },
                "metadata": {
                    "node_id": node.get("id"),
                    "product_id": product.get("id"),
                    "contact_text": payload.contact_text,
                },
            }
            if self.settings.image_response_format:
                body["response_format"] = self.settings.image_response_format
            response = post_openai_compatible_json(
                base_url=self.settings.base_url,
                endpoint=self.settings.image_fusion_endpoint,
                api_key=self.settings.image_api_key,
                body=body,
                timeout_seconds=self.settings.image_timeout_seconds,
            )
        else:
            image_files = [transparent_product_png]
            if scene_reference_path:
                image_files.append(scene_reference_path)
            response = post_openai_compatible_multipart_json(
                base_url=self.settings.base_url,
                endpoint=self.settings.image_fusion_endpoint,
                api_key=self.settings.image_api_key,
                fields={
                    "model": self.settings.image_model,
                    "prompt": prompt,
                    "size": self.settings.image_output_size,
                    "n": "1",
                    "quality": self.settings.image_quality,
                    "output_format": self.settings.image_output_format,
                    "response_format": self.settings.image_response_format,
                },
                image_files=image_files,
                image_field=self.settings.image_file_field,
                timeout_seconds=self.settings.image_timeout_seconds,
            )
        write_response_image(response=response, image_path=image_path, timeout_seconds=self.settings.image_timeout_seconds)
        return SceneFusionResult(
            image_path=image_path,
            prompt=prompt,
            negative_prompt=prompt_result.negative_prompt,
            provider="openai_compatible_image",
            model=self.settings.image_model,
            mode=f"image_edit_{self.settings.image_fusion_request_mode}",
            prompt_provider=prompt_result.provider,
            used_product_asset_id=product_asset_id,
            preserve_product_pixels=True,
        )

    def _generate_background_only(
        self,
        *,
        node: dict[str, Any],
        payload: PosterTaskCreate,
        output_dir: Path,
    ) -> BackgroundGenerationResult:
        if not self.settings.has_image_credentials:
            raise AiProviderError("图片生成 Key 未配置")

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / "ai_background.jpg"
        prompt = build_background_prompt(node=node, payload=payload)
        negative_prompt = build_negative_prompt()
        body = {
            "model": self.settings.image_model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "n": 1,
            "size": self.settings.image_output_size,
        }
        if self.settings.image_response_format:
            body["response_format"] = self.settings.image_response_format
        response = post_openai_compatible_json(
            base_url=self.settings.base_url,
            endpoint=self.settings.image_generation_endpoint,
            api_key=self.settings.image_api_key,
            body=body,
            timeout_seconds=self.settings.image_timeout_seconds,
        )
        write_response_image(response=response, image_path=image_path, timeout_seconds=self.settings.image_timeout_seconds)
        return BackgroundGenerationResult(
            image_path=image_path,
            prompt=prompt,
            negative_prompt=negative_prompt,
            provider="openai_compatible_image",
            model=self.settings.image_model,
        )

    def _rewrite_copy(self, *, title: str, subtitle: str, issues: list[dict[str, Any]]) -> tuple[str, str]:
        if not self.settings.has_text_credentials:
            raise AiProviderError("文本改写 Key 未配置")

        response = post_openai_compatible_json(
            base_url=self.settings.base_url,
            endpoint="/chat/completions",
            api_key=self.settings.text_api_key,
            body={
                "model": self.settings.text_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是广告合规文案改写助手。只返回 JSON，不要返回 Markdown。",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "把高风险海报文案改写为低风险表达",
                                "required_schema": {
                                    "title": "改写后的主标题",
                                    "subtitle": "改写后的副标题",
                                },
                                "title": title,
                                "subtitle": subtitle,
                                "issues": issues,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
            },
            timeout_seconds=self.settings.text_timeout_seconds,
        )
        parsed = parse_json_content(response["choices"][0]["message"]["content"])
        return str(parsed.get("title", "")).strip(), str(parsed.get("subtitle", "")).strip()


def get_ai_provider() -> AiProvider:
    settings = get_ai_settings()
    if settings.has_text_credentials or settings.has_image_credentials:
        return OpenAICompatibleAiProvider(settings)
    return MockBridgeAiProvider(settings)


def build_local_scene_prompt(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
) -> ScenePromptResult:
    positive_prompt = build_scene_positive_prompt(node=node, product=product, payload=payload)
    negative_prompt = build_negative_prompt()
    return ScenePromptResult(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        provider="local_fallback",
        model="local_template",
    )


def build_scene_positive_prompt(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
) -> str:
    visual_direction = node.get("visual_direction") or "高级、干净、适合营销传播"
    colors = "、".join(node.get("colors", []))
    keywords = "、".join(node.get("keywords", []))
    user_scene_prompt = payload.scene_prompt.strip() or payload.custom_requirement.strip() or "让产品自然融入节日营销场景"
    return (
        "请基于我提供的透明产品 PNG 生成一张 1080x1920 竖版营销场景图。"
        "产品主体必须直接来自我提供的透明产品素材，必须保留真实外观、颜色、结构、比例、材质和细节，"
        "不要重绘产品，不要改变产品外形。"
        f"请让产品自然融入与“{node.get('name', '营销节点')}”相关的氛围场景中，"
        f"视觉方向为“{visual_direction}”，色彩倾向为“{colors}”，关键词包括“{keywords}”。"
        f"补充场景要求：{user_scene_prompt}。"
        "可适度加入与节日相关的环境元素，例如端午节可加入粽子、龙舟氛围，"
        "春节或元宵节可加入团圆饭、灯笼、暖光室内场景，但不要喧宾夺主。"
        f"产品名称参考：{product.get('name', '')}。"
        "画面仅生成场景与氛围，不要出现任何文字、Logo、二维码、水印、边框、按钮、海报排版元素。"
        "请为顶部 Logo 区和底部标题、副标题、二维码、联系方式、底部条区域预留安全区。"
        "整体风格要高级、干净、和谐，产品要明显可见，并且与背景融洽。"
    )


def build_negative_prompt() -> str:
    return (
        "不要重绘产品，不要改变产品颜色、结构、比例、材质和细节，"
        "不要让产品变形，不要替换为相似产品，不要生成多个产品，"
        "不要裁切掉产品关键结构，不要遮挡产品主体，不要人物手持，"
        "不要文字，不要汉字，不要英文文案，不要 Logo，不要二维码，不要水印，"
        "不要边框，不要贴纸，不要价格标签，不要促销字样，不要杂乱背景，"
        "不要夸张光效，不要脏污噪点，不要不可控品牌元素。"
    )


def default_safe_zones() -> dict[str, tuple[int, int, int, int]]:
    return {
        "top_logo": (72, 72, 1008, 220),
        "bottom_copy_qrcode": (72, 1320, 1008, 1820),
    }


def normalize_safe_zones(zones: dict[str, tuple[int, int, int, int]]) -> dict[str, list[int]]:
    return {name: [int(value) for value in rect] for name, rect in zones.items()}


def create_mock_scene_with_product(
    *,
    path: Path,
    node: dict[str, Any],
    product_image_path: Path,
    scene_reference_path: Path | None,
    canvas_size: tuple[int, int],
    safe_zones: dict[str, tuple[int, int, int, int]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_image = create_mock_atmosphere_canvas(node=node, canvas_size=canvas_size)
    if scene_reference_path and scene_reference_path.exists():
        with Image.open(scene_reference_path) as reference:
            reference_rgba = ImageOps.fit(
                reference.convert("RGBA"),
                canvas_size,
                method=Image.Resampling.LANCZOS,
            )
            reference_rgba = reference_rgba.filter(ImageFilter.GaussianBlur(radius=1.4))
            base_image = Image.blend(base_image, reference_rgba, 0.24)

    add_festive_motifs(base_image, node)
    paste_product_into_scene(
        canvas=base_image,
        product_path=product_image_path,
        placement_zone=compute_product_zone(canvas_size=canvas_size, safe_zones=safe_zones),
    )
    add_depth_vignette(base_image)
    base_image.save(path, "PNG")


def create_mock_atmosphere_canvas(
    *,
    node: dict[str, Any],
    canvas_size: tuple[int, int],
) -> Image.Image:
    width, height = canvas_size
    colors = node.get("colors") or ["#E7F5F2", "#0F766E"]
    top = _hex_to_rgb(colors[1] if len(colors) > 1 else "#F5F5F5")
    bottom = _hex_to_rgb(colors[0])
    image = Image.new("RGBA", (width, height), top + (255,))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        mixed = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=mixed + (255,))

    random.seed(str(node.get("id", "node")))
    for _ in range(20):
        x = random.randint(-120, width)
        y = random.randint(-120, height)
        r = random.randint(80, 320)
        alpha = random.randint(20, 52)
        layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.ellipse((x, y, x + r, y + r), fill=bottom + (alpha,))
        image = Image.alpha_composite(image, layer.filter(ImageFilter.GaussianBlur(30)))
    return image


def add_festive_motifs(canvas: Image.Image, node: dict[str, Any]) -> None:
    name = str(node.get("name", ""))
    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size

    if "端午" in name:
        for offset in (120, 270, 420):
            draw.rounded_rectangle((offset, height - 620, offset + 90, height - 500), radius=18, fill=(84, 122, 59, 80))
            draw.polygon(
                [(offset + 45, height - 660), (offset, height - 550), (offset + 90, height - 550)],
                fill=(110, 153, 78, 110),
            )
        draw.arc((760, 980, 1040, 1260), start=200, end=338, fill=(255, 255, 255, 96), width=6)
    elif "春节" in name or "元宵" in name:
        for x in (130, 900):
            draw.ellipse((x, 250, x + 78, 328), fill=(194, 44, 44, 170))
            draw.rectangle((x + 33, 328, x + 45, 380), fill=(214, 180, 86, 160))
        draw.ellipse((280, 360, 800, 640), fill=(255, 244, 220, 54))
    elif "中秋" in name:
        draw.ellipse((730, 180, 980, 430), fill=(255, 243, 202, 160))
        draw.ellipse((760, 210, 950, 400), fill=(255, 247, 223, 210))
    elif "立冬" in name or "冬至" in name:
        for x in (160, 320, 910):
            draw.line((x, 220, x + 24, 260), fill=(255, 255, 255, 120), width=3)
            draw.line((x + 24, 220, x, 260), fill=(255, 255, 255, 120), width=3)


def compute_product_zone(
    *,
    canvas_size: tuple[int, int],
    safe_zones: dict[str, tuple[int, int, int, int]],
) -> tuple[int, int, int, int]:
    width, height = canvas_size
    top_safe = safe_zones["top_logo"][3]
    bottom_safe = safe_zones["bottom_copy_qrcode"][1]
    return (120, top_safe + 160, width - 120, bottom_safe - 80)


def paste_product_into_scene(
    *,
    canvas: Image.Image,
    product_path: Path,
    placement_zone: tuple[int, int, int, int],
) -> None:
    with Image.open(product_path) as product_image:
        product = product_image.convert("RGBA")
        zone_width = placement_zone[2] - placement_zone[0]
        zone_height = placement_zone[3] - placement_zone[1]
        product = ImageOps.contain(product, (zone_width, zone_height), method=Image.Resampling.LANCZOS)

        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_alpha = product.getchannel("A").filter(ImageFilter.GaussianBlur(18))
        shadow_tile = Image.new("RGBA", product.size, (0, 0, 0, 0))
        shadow_tile.putalpha(shadow_alpha)

        x = placement_zone[0] + (zone_width - product.width) // 2
        y = placement_zone[1] + (zone_height - product.height) // 2
        shadow.alpha_composite(shadow_tile, (x + 18, y + 24))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        canvas.alpha_composite(shadow)

        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.ellipse(
            (x - 30, y + product.height - 40, x + product.width + 30, y + product.height + 70),
            fill=(255, 255, 255, 42),
        )
        canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
        canvas.alpha_composite(product, (x, y))


def add_depth_vignette(canvas: Image.Image) -> None:
    width, height = canvas.size
    vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(vignette)
    draw.rectangle((0, 0, width, height), fill=(0, 0, 0, 30))
    draw.rounded_rectangle((34, 34, width - 34, height - 34), radius=58, fill=(0, 0, 0, 0), outline=(255, 255, 255, 28), width=3)
    canvas.alpha_composite(vignette.filter(ImageFilter.GaussianBlur(30)))


def post_openai_compatible_json(
    *,
    base_url: str,
    endpoint: str,
    api_key: str,
    body: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AiProviderError(f"中转站 HTTP 错误：{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AiProviderError(f"中转站连接失败：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AiProviderError("中转站返回不是有效 JSON") from exc


def post_openai_compatible_multipart_json(
    *,
    base_url: str,
    endpoint: str,
    api_key: str,
    fields: dict[str, str],
    image_files: list[Path],
    image_field: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    boundary = f"----festival-poster-{uuid4().hex}"
    body = bytearray()

    for key, value in fields.items():
        if value is None or value == "":
            continue
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(str(value).encode("utf-8"))
        body.extend(b"\r\n")

    for index, image_path in enumerate(image_files):
        if not image_path or not image_path.exists():
            continue
        field_name = image_field
        if "{index}" in field_name:
            field_name = field_name.replace("{index}", str(index))
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{image_path.name}"\r\n'
            ).encode("utf-8")
        )
        body.extend(b"Content-Type: image/png\r\n\r\n")
        body.extend(image_path.read_bytes())
        body.extend(b"\r\n")

    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AiProviderError(f"中转站图片编辑 HTTP 错误：{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AiProviderError(f"中转站图片编辑连接失败：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise AiProviderError("中转站图片编辑返回不是有效 JSON") from exc


def write_response_image(response: dict[str, Any], image_path: Path, timeout_seconds: int) -> None:
    first_image = {}
    data = response.get("data")
    if isinstance(data, list) and data:
        first_image = data[0] if isinstance(data[0], dict) else {}
    candidates = [
        first_image.get("b64_json"),
        first_image.get("base64"),
        first_image.get("image"),
        response.get("b64_json"),
        response.get("base64"),
        response.get("image"),
    ]
    for candidate in candidates:
        if candidate:
            image_path.write_bytes(base64.b64decode(strip_data_url(str(candidate))))
            return
    url = first_image.get("url") or response.get("url")
    if url:
        image_path.write_bytes(download_image(str(url), timeout_seconds))
        return
    raise AiProviderError("图片接口返回缺少 b64_json 或 url")


def strip_data_url(value: str) -> str:
    if "," in value and value.strip().lower().startswith("data:"):
        return value.split(",", 1)[1]
    return value


def append_image_constraints(
    *,
    prompt: str,
    negative_prompt: str,
    canvas_size: tuple[int, int],
    safe_zones: dict[str, tuple[int, int, int, int]],
) -> str:
    zones = normalize_safe_zones(safe_zones)
    return (
        f"{prompt}\n\n"
        f"画布比例：竖版 {canvas_size[0]}x{canvas_size[1]}。"
        "请把参考产品自然融入真实营销场景，像原本就在场景里一样，有合理接触面、遮挡关系、阴影、环境光和景深。"
        "例如春节可生成一家人围坐团圆饭的温暖场景，产品自然放在餐边柜、厨房台面或餐厅角落，不要突兀。"
        "保留参考产品的真实外观、品牌结构、材质和比例，不要把产品变成其他物体。"
        f"顶部和底部预留海报排版安全区：{json.dumps(zones, ensure_ascii=False)}。"
        "画面本身不要生成任何文字、汉字、英文、Logo、二维码、水印、价格标签或促销标签。"
        f"负向约束：{negative_prompt}"
    )


def download_image(url: str, timeout_seconds: int) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise AiProviderError(f"图片 URL 下载 HTTP 错误：{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AiProviderError(f"图片 URL 下载失败：{exc.reason}") from exc


def encode_image_base64(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    with Image.open(path) as image:
        buffer = BytesIO()
        image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def parse_json_content(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AiProviderError("文本模型未返回结构化 JSON") from exc
    if not isinstance(parsed, dict):
        raise AiProviderError("文本模型 JSON 顶层不是对象")
    return parsed


def normalize_alternatives(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    alternatives: list[dict[str, str]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        alternatives.append(
            {
                "title": str(item.get("title", "")).strip()[:18],
                "subtitle": str(item.get("subtitle", "")).strip()[:42],
            }
        )
    return alternatives


def build_background_prompt(*, node: dict[str, Any], payload: PosterTaskCreate) -> str:
    keywords = "、".join(node.get("keywords", []))
    colors = "、".join(node.get("colors", []))
    extra = payload.scene_prompt.strip() or payload.custom_requirement.strip()
    extra_text = f"补充要求：{extra}。" if extra else ""
    return (
        f"为“{node.get('name', '营销节点')}”生成节日/节气/活动氛围背景图，"
        f"关键词：{keywords}，色彩方向：{colors}。"
        "要求：无文字、无 Logo、无二维码、无水印、无产品主体，"
        "仅生成可用于海报合成的背景氛围，并为顶部 Logo 区与底部文案二维码区预留安全区。"
        f"{extra_text}"
    )


def create_textless_atmosphere_background(path: Path, node: dict[str, Any]) -> None:
    width, height = DEFAULT_CANVAS_SIZE
    colors = node.get("colors") or ["#E7F5F2", "#0F766E"]
    base = _hex_to_rgb(colors[1] if len(colors) > 1 else "#E7F5F2")
    accent = _hex_to_rgb(colors[0])
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / max(height - 1, 1)
        mixed = tuple(int(base[i] * (1 - ratio) + accent[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=mixed)

    random.seed(str(node.get("id", "node")))
    for _ in range(18):
        x = random.randint(-180, width)
        y = random.randint(-180, height)
        radius = random.randint(120, 380)
        fill = (*accent, random.randint(18, 46))
        layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.ellipse((x, y, x + radius, y + radius), fill=fill)
        image = Image.alpha_composite(image.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(34))).convert("RGB")

    overlay = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for offset in range(-height, width, 170):
        overlay_draw.line((offset, 0, offset + height, height), fill=(255, 255, 255, 28), width=3)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    image.save(path, "JPEG", quality=92, optimize=True)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return (15, 118, 110)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
