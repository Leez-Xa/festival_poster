from __future__ import annotations

import base64
import json
import logging
import random
import re
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.config import AISettings, PROMPT_CACHE_DIR, ROOT_DIR, get_ai_settings
from app.models import PosterTaskCreate
from config.seed_data import COMPLIANCE_RULES

logger = logging.getLogger(__name__)


DEFAULT_CANVAS_SIZE = (1080, 1920)
MAX_PROVIDER_IMAGE_BYTES = 20 * 1024 * 1024
REFERENCE_POSTER_DIR = ROOT_DIR / "素材" / "节日节气海报"
REFERENCE_PROMPT_CACHE = PROMPT_CACHE_DIR / "reference_posters.json"
REFERENCE_PROMPT_VERSION = "reference_prompt_v2_chinese_art_text"
REFERENCE_ANALYSIS_MAX_IMAGES = 6


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
    on_image_title: str = ""
    on_image_subtitle: str = ""
    reference_prompt_source: str = "local_fallback"
    base_style_prompt: str = ""
    node_style_prompt: str = ""
    final_image_prompt: str = ""
    reference_analysis_fallback_used: bool = True

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "provider": self.provider,
            "model": self.model,
            "on_image_title": self.on_image_title,
            "on_image_subtitle": self.on_image_subtitle,
            "reference_prompt_source": self.reference_prompt_source,
            "base_style_prompt": self.base_style_prompt,
            "node_style_prompt": self.node_style_prompt,
            "final_image_prompt": self.final_image_prompt or self.positive_prompt,
            "reference_analysis_fallback_used": self.reference_analysis_fallback_used,
        }


@dataclass
class BrandReferenceAsset:
    role: str
    asset_id: str
    path: Path


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
    on_image_title: str = ""
    on_image_subtitle: str = ""
    reference_prompt_source: str = "local_fallback"
    base_style_prompt: str = ""
    node_style_prompt: str = ""
    final_image_prompt: str = ""
    reference_analysis_fallback_used: bool = True
    ai_receives_brand_assets: bool = False
    brand_asset_roles: list[str] = field(default_factory=list)
    protected_brand_asset_ids: dict[str, str] = field(default_factory=dict)
    brand_protection_mode: str = "none"


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
        copy: dict[str, Any] | None = None,
    ) -> ScenePromptResult:
        return self._run_with_timeout(
            "generate_scene_prompt",
            lambda: self._generate_scene_prompt(node=node, product=product, payload=payload, copy=copy),
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
        copy: dict[str, Any] | None = None,
        canvas_size: tuple[int, int] = DEFAULT_CANVAS_SIZE,
        safe_zones: dict[str, tuple[int, int, int, int]] | None = None,
        brand_reference_assets: list[BrandReferenceAsset] | None = None,
    ) -> SceneFusionResult:
        try:
            prompt_result = self.generate_scene_prompt(node=node, product=product, payload=payload, copy=copy)
        except AiProviderError:
            prompt_result = build_local_scene_prompt(node=node, product=product, payload=payload, copy=copy)

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
                brand_reference_assets=brand_reference_assets or [],
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
        copy: dict[str, Any] | None = None,
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
        brand_reference_assets: list[BrandReferenceAsset],
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
        copy: dict[str, Any] | None = None,
    ) -> ScenePromptResult:
        return build_local_scene_prompt(node=node, product=product, payload=payload, copy=copy)

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
        brand_reference_assets: list[BrandReferenceAsset],
    ) -> SceneFusionResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / "ai_scene_mock.png"
        create_mock_scene_with_product(
            path=image_path,
            node=node,
            product_image_path=transparent_product_png,
            scene_reference_path=None,
            canvas_size=canvas_size,
            safe_zones=safe_zones,
            on_image_title=prompt_result.on_image_title,
            on_image_subtitle=prompt_result.on_image_subtitle,
            brand_reference_assets=brand_reference_assets,
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
            on_image_title=prompt_result.on_image_title,
            on_image_subtitle=prompt_result.on_image_subtitle,
            reference_prompt_source=prompt_result.reference_prompt_source,
            base_style_prompt=prompt_result.base_style_prompt,
            node_style_prompt=prompt_result.node_style_prompt,
            final_image_prompt=prompt_result.final_image_prompt or prompt_result.positive_prompt,
            reference_analysis_fallback_used=prompt_result.reference_analysis_fallback_used,
            ai_receives_brand_assets=bool(brand_reference_assets),
            brand_asset_roles=[asset.role for asset in brand_reference_assets],
            protected_brand_asset_ids=brand_asset_id_map(brand_reference_assets),
            brand_protection_mode="ai_fusion_with_exact_final_overlay" if brand_reference_assets else "none",
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
        copy: dict[str, Any] | None = None,
    ) -> ScenePromptResult:
        if not self.settings.has_text_credentials:
            raise AiProviderError("提示词生成 Key 未配置")

        reference_library = load_reference_prompt_library(self.settings, allow_remote=True)
        rough_copy = normalize_copy_context(copy=copy, node=node, product=product, payload=payload)
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
                            "你是中文节日产品海报的生图提示词导演，只返回 JSON，不要 Markdown。"
                            "图片模型只会收到一张产品参考图，不会收到节日参考海报图。"
                            "你必须把参考海报提示词库、当前节点、产品资料和用户需求整合成最终中文生图提示词。"
                            "最终画面允许生成中文主标题、副标题和艺术字，但语气必须先有节日氛围，再自然带出产品陪伴，"
                            "不要强硬推销，不要绝对化宣传，不要医疗功效承诺。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": "为图片模型生成中文节日产品海报提示词",
                                "required_schema": {
                                    "on_image_title": "不超过18个中文字符，适合艺术字主标题",
                                    "on_image_subtitle": "不超过42个中文字符，柔性带出产品陪伴",
                                    "scene_prompt": "节日氛围、人物/空间/产品自然融入方式",
                                    "typography_prompt": "中文艺术字风格、位置、层级、可读性要求",
                                    "negative_prompt": "禁止英文、乱码、硬广、医疗功效、额外Logo、二维码、水印、产品变形",
                                },
                                "reference_prompt_library": reference_library,
                                "rough_copy": rough_copy,
                                "node": node,
                                "product": product,
                                "scene_prompt": payload.scene_prompt,
                                "custom_requirement": payload.custom_requirement,
                                "contact_text": payload.contact_text,
                                "hard_constraints": [
                                    "最终画面是 1080x1920 竖版中文节日产品海报。",
                                    "图片模型只接收产品图，请用文字描述参考海报风格，不要要求模型读取第二张参考图。",
                                    "产品必须像真实物体一样融入场景，有接触面、阴影、环境光和合理比例。",
                                    "顶部预留 Logo 区，底部预留底部宣传条区；不要生成额外 Logo 和二维码。",
                                    "可生成中文艺术字主标题和副标题；除产品型号外不要出现英文字母。",
                                ],
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
        return build_scene_prompt_result(
            parsed=parsed,
            node=node,
            product=product,
            payload=payload,
            copy=rough_copy,
            reference_library=reference_library,
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
        brand_reference_assets: list[BrandReferenceAsset],
    ) -> SceneFusionResult:
        if not self.settings.has_image_credentials:
            raise AiProviderError("图片生成 Key 未配置")

        output_dir.mkdir(parents=True, exist_ok=True)
        image_path = output_dir / "ai_scene.png"
        prepared_brand_assets = prepare_brand_reference_images(brand_reference_assets, output_dir)
        brand_roles = [asset.role for asset in prepared_brand_assets]
        negative_prompt = (
            relax_negative_prompt_for_brand_assets(prompt_result.negative_prompt)
            if prepared_brand_assets
            else prompt_result.negative_prompt
        )
        prompt = append_image_constraints(
            prompt=prompt_result.positive_prompt,
            negative_prompt=negative_prompt,
            canvas_size=canvas_size,
            safe_zones=safe_zones,
            brand_asset_roles=brand_roles,
        )
        if prepared_brand_assets:
            prompt = f"{prompt}\n\nFinal brand reference instruction: {build_brand_fusion_instruction(brand_roles)}"
        if self.settings.image_fusion_request_mode == "json_base64":
            body = {
                "model": self.settings.image_model,
                "mode": "image_edit",
                "size": self.settings.image_output_size,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "product_image_b64": encode_image_base64(transparent_product_png),
                "brand_assets_b64": brand_reference_payload(prepared_brand_assets),
                "canvas_size": list(canvas_size),
                "safe_zones": normalize_safe_zones(safe_zones),
                "constraints": {
                    "preserve_product_pixels": True,
                    "preserve_reference_brand_assets": bool(prepared_brand_assets),
                    "integrate_reference_brand_assets": bool(prepared_brand_assets),
                    "no_text": False,
                    "chinese_text_only": True,
                    "allow_artistic_chinese_title": True,
                    "no_extra_logo": True,
                    "no_extra_qrcode": True,
                    "no_distorted_brand_mark": True,
                    "no_unreadable_qrcode": True,
                    "no_watermark": True,
                },
                "metadata": {
                    "node_id": node.get("id"),
                    "product_id": product.get("id"),
                    "contact_text": payload.contact_text,
                    "on_image_title": prompt_result.on_image_title,
                    "on_image_subtitle": prompt_result.on_image_subtitle,
                    "reference_prompt_source": prompt_result.reference_prompt_source,
                    "reference_image_roles": ["product", *brand_roles],
                    "brand_protection_mode": "ai_fusion_with_exact_final_overlay" if prepared_brand_assets else "none",
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
                    "reference_image_roles": json.dumps(["product", *brand_roles], ensure_ascii=False),
                    "brand_protection_mode": "ai_fusion_with_exact_final_overlay" if prepared_brand_assets else "none",
                },
                image_files=[transparent_product_png, *[asset.path for asset in prepared_brand_assets]],
                image_field=self.settings.image_file_field,
                timeout_seconds=self.settings.image_timeout_seconds,
            )
        write_response_image(response=response, image_path=image_path, timeout_seconds=self.settings.image_timeout_seconds)
        return SceneFusionResult(
            image_path=image_path,
            prompt=prompt,
            negative_prompt=negative_prompt,
            provider="openai_compatible_image",
            model=self.settings.image_model,
            mode=f"image_edit_{self.settings.image_fusion_request_mode}",
            prompt_provider=prompt_result.provider,
            used_product_asset_id=product_asset_id,
            preserve_product_pixels=True,
            on_image_title=prompt_result.on_image_title,
            on_image_subtitle=prompt_result.on_image_subtitle,
            reference_prompt_source=prompt_result.reference_prompt_source,
            base_style_prompt=prompt_result.base_style_prompt,
            node_style_prompt=prompt_result.node_style_prompt,
            final_image_prompt=prompt_result.final_image_prompt or prompt,
            reference_analysis_fallback_used=prompt_result.reference_analysis_fallback_used,
            ai_receives_brand_assets=bool(prepared_brand_assets),
            brand_asset_roles=brand_roles,
            protected_brand_asset_ids=brand_asset_id_map(brand_reference_assets),
            brand_protection_mode="ai_fusion_with_exact_final_overlay" if prepared_brand_assets else "none",
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
    copy: dict[str, Any] | None = None,
) -> ScenePromptResult:
    reference_library = load_reference_prompt_library(get_ai_settings(), allow_remote=False)
    rough_copy = normalize_copy_context(copy=copy, node=node, product=product, payload=payload)
    return build_scene_prompt_result(
        parsed={},
        node=node,
        product=product,
        payload=payload,
        copy=rough_copy,
        reference_library=reference_library,
        provider="local_fallback",
        model="local_template",
    )


def load_reference_prompt_library(settings: AISettings, *, allow_remote: bool) -> dict[str, Any]:
    cache = read_reference_prompt_cache()
    if cache and (not cache.get("reference_analysis_fallback_used") or not allow_remote or not settings.has_text_credentials):
        return cache

    fallback = build_fallback_reference_prompt_library()
    if allow_remote and settings.has_text_credentials:
        try:
            library = analyze_reference_posters(settings=settings, fallback=fallback)
            write_reference_prompt_cache(library)
            return library
        except AiProviderError as exc:
            logger.warning("Reference poster analysis fallback used: %s", exc)

    if not cache:
        write_reference_prompt_cache(fallback)
    return fallback


def read_reference_prompt_cache() -> dict[str, Any] | None:
    if not REFERENCE_PROMPT_CACHE.exists():
        return None
    try:
        data = json.loads(REFERENCE_PROMPT_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != REFERENCE_PROMPT_VERSION:
        return None
    return data


def write_reference_prompt_cache(library: dict[str, Any]) -> None:
    try:
        PROMPT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        REFERENCE_PROMPT_CACHE.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write reference prompt cache: %s", exc)


def build_fallback_reference_prompt_library() -> dict[str, Any]:
    files = list_reference_poster_files()
    return {
        "version": REFERENCE_PROMPT_VERSION,
        "source": "local_fallback",
        "reference_analysis_fallback_used": True,
        "analyzed_files": [path.name for path in files],
        "base_style_prompt": (
            "中文节日产品海报，竖版构图，节日氛围先行，画面有真实生活空间、暖光、层次和留白。"
            "主标题使用中文艺术字，与节日元素自然结合；副文案温和表达产品陪伴，不做强硬卖点推销。"
            "产品像真实物体一样出现在餐边柜、厨房台面、客厅边柜、办公室茶水间等合适位置。"
        ),
        "node_style_prompts": {},
        "typography_rules": (
            "中文主标题清晰可读、具有节日艺术字质感；副标题较小，贴近生活祝福语。"
            "文字与场景光影融合，但不能变成乱码、错别字或英文。"
        ),
        "composition_rules": (
            "顶部保留 Logo 安全区，底部保留底部宣传条安全区。"
            "画面中心到中下部用于产品和人物/空间关系，不让产品悬浮或被遮挡。"
        ),
        "negative_prompt": "不要英文硬广、乱码、错别字、价格标签、额外Logo、二维码、水印、医疗功效承诺、绝对化承诺。",
    }


def analyze_reference_posters(*, settings: AISettings, fallback: dict[str, Any]) -> dict[str, Any]:
    files = list_reference_poster_files()[:REFERENCE_ANALYSIS_MAX_IMAGES]
    if not files:
        return fallback

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps(
                {
                    "task": "分析这些中文节日节气参考海报，提炼给图片模型使用的提示词库。",
                    "required_schema": {
                        "base_style_prompt": "统一风格提示词",
                        "node_style_prompts": {"节点名或文件名": "该节点提示词"},
                        "typography_rules": "中文艺术字规则",
                        "composition_rules": "构图和留白规则",
                        "negative_prompt": "负向提示词",
                    },
                    "rules": [
                        "只提炼风格、节日氛围、构图和中文艺术字规律。",
                        "不要复制原海报具体文字、二维码、价格、联系方式。",
                        "提示词要适合净水机/饮水产品自然融入节日生活场景。",
                    ],
                    "files": [path.name for path in files],
                },
                ensure_ascii=False,
            ),
        }
    ]
    for path in files:
        data_url = encode_reference_image_data_url(path)
        if data_url:
            content.append({"type": "image_url", "image_url": {"url": data_url}})

    response = post_openai_compatible_json(
        base_url=settings.base_url,
        endpoint="/chat/completions",
        api_key=settings.text_api_key,
        body={
            "model": settings.scene_prompt_model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是中文商业海报视觉分析师。只返回 JSON，不要 Markdown。",
                },
                {"role": "user", "content": content},
            ],
            "temperature": 0.25,
            "response_format": {"type": "json_object"},
        },
        timeout_seconds=settings.text_timeout_seconds,
    )
    parsed = parse_json_content(response["choices"][0]["message"]["content"])
    return normalize_reference_prompt_library(parsed=parsed, fallback=fallback, files=files)


def normalize_reference_prompt_library(
    *,
    parsed: dict[str, Any],
    fallback: dict[str, Any],
    files: list[Path],
) -> dict[str, Any]:
    base_style_prompt = str(parsed.get("base_style_prompt") or fallback["base_style_prompt"]).strip()
    typography_rules = str(parsed.get("typography_rules") or fallback["typography_rules"]).strip()
    composition_rules = str(parsed.get("composition_rules") or fallback["composition_rules"]).strip()
    negative_prompt = str(parsed.get("negative_prompt") or fallback["negative_prompt"]).strip()
    node_prompts = parsed.get("node_style_prompts")
    if not isinstance(node_prompts, dict):
        node_prompts = {}
    return {
        "version": REFERENCE_PROMPT_VERSION,
        "source": "vision_text_analysis",
        "reference_analysis_fallback_used": False,
        "analyzed_files": [path.name for path in files],
        "base_style_prompt": base_style_prompt,
        "node_style_prompts": {str(key): str(value).strip() for key, value in node_prompts.items() if str(value).strip()},
        "typography_rules": typography_rules,
        "composition_rules": composition_rules,
        "negative_prompt": negative_prompt,
    }


def list_reference_poster_files() -> list[Path]:
    if not REFERENCE_POSTER_DIR.exists():
        return []
    allowed = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(
        [path for path in REFERENCE_POSTER_DIR.iterdir() if path.is_file() and path.suffix.lower() in allowed],
        key=lambda item: item.name,
    )


def encode_reference_image_data_url(path: Path) -> str | None:
    try:
        with Image.open(path) as image:
            image = ImageOps.contain(image.convert("RGB"), (768, 768), method=Image.Resampling.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=72, optimize=True)
    except Exception as exc:
        logger.warning("Could not encode reference poster %s: %s", path.name, exc)
        return None
    return f"data:image/jpeg;base64,{base64.b64encode(buffer.getvalue()).decode('utf-8')}"


def normalize_copy_context(
    *,
    copy: dict[str, Any] | None,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
) -> dict[str, str]:
    title = str((copy or {}).get("title") or payload.copy_preference.title or f"{node.get('name', '节日')}好水相伴").strip()
    subtitle = str(
        (copy or {}).get("subtitle")
        or payload.copy_preference.subtitle
        or f"让{product.get('name', '产品')}自然融入团圆时刻"
    ).strip()
    return {
        "title": sanitize_marketing_text(title, fallback=f"{node.get('name', '节日')}好水相伴", limit=18),
        "subtitle": sanitize_marketing_text(subtitle, fallback="把健康饮水融入节日生活", limit=42),
    }


def build_scene_prompt_result(
    *,
    parsed: dict[str, Any],
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
    copy: dict[str, str],
    reference_library: dict[str, Any],
    provider: str,
    model: str,
) -> ScenePromptResult:
    on_image_title = sanitize_marketing_text(
        str(parsed.get("on_image_title") or copy["title"]),
        fallback=copy["title"],
        limit=18,
    )
    on_image_subtitle = sanitize_marketing_text(
        str(parsed.get("on_image_subtitle") or copy["subtitle"]),
        fallback=copy["subtitle"],
        limit=42,
    )
    base_style_prompt = str(reference_library.get("base_style_prompt") or "").strip()
    node_style_prompt = get_node_style_prompt(reference_library=reference_library, node=node)
    typography_prompt = str(parsed.get("typography_prompt") or reference_library.get("typography_rules") or "").strip()
    scene_prompt = str(parsed.get("scene_prompt") or build_default_scene_prompt(node=node, product=product, payload=payload)).strip()
    negative_prompt = combine_negative_prompts(
        str(parsed.get("negative_prompt") or ""),
        str(reference_library.get("negative_prompt") or ""),
        build_negative_prompt(),
    )
    positive_prompt = compose_final_image_prompt(
        node=node,
        product=product,
        payload=payload,
        title=on_image_title,
        subtitle=on_image_subtitle,
        base_style_prompt=base_style_prompt,
        node_style_prompt=node_style_prompt,
        scene_prompt=scene_prompt,
        typography_prompt=typography_prompt,
    )
    return ScenePromptResult(
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        provider=provider,
        model=model,
        on_image_title=on_image_title,
        on_image_subtitle=on_image_subtitle,
        reference_prompt_source=str(reference_library.get("source") or provider),
        base_style_prompt=base_style_prompt,
        node_style_prompt=node_style_prompt,
        final_image_prompt=positive_prompt,
        reference_analysis_fallback_used=bool(reference_library.get("reference_analysis_fallback_used", True)),
    )


def get_node_style_prompt(*, reference_library: dict[str, Any], node: dict[str, Any]) -> str:
    prompts = reference_library.get("node_style_prompts")
    if isinstance(prompts, dict):
        candidates = [node.get("id"), node.get("name"), node.get("type")]
        for candidate in candidates:
            if candidate and str(candidate) in prompts and str(prompts[str(candidate)]).strip():
                return str(prompts[str(candidate)]).strip()
    return build_default_node_style_prompt(node)


def build_default_node_style_prompt(node: dict[str, Any]) -> str:
    name = str(node.get("name") or "节日")
    keywords = "、".join(str(item) for item in node.get("keywords", []) if item)
    colors = "、".join(str(item) for item in node.get("colors", []) if item)
    visual_direction = str(node.get("visual_direction") or "有节日氛围、干净高级")
    scenario = node_scenario_hint(node)
    return f"{name}节点：{visual_direction}；关键词：{keywords}；色彩参考：{colors}。{scenario}"


def node_scenario_hint(node: dict[str, Any]) -> str:
    node_text = f"{node.get('id', '')} {node.get('name', '')} {' '.join(node.get('keywords', []))}"
    if any(term in node_text for term in ("春节", "除夕", "spring")):
        return "春节/除夕场景以家庭团圆饭、暖光餐厅、餐边柜或厨房台面为主，产品像家中原有电器一样自然出现。"
    if any(term in node_text for term in ("元宵", "lantern")):
        return "元宵场景以灯笼、暖色灯火、团圆餐桌为主，产品放在餐边柜或厨房转角。"
    if any(term in node_text for term in ("中秋", "mid")):
        return "中秋场景以月色、团圆、茶点和温暖家居为主，产品作为家庭饮水陪伴自然融入。"
    if any(term in node_text for term in ("冬至", "立冬", "dong", "lidong")):
        return "冬日场景以热汤、暖光、家人围坐和室内温暖感为主，产品放在厨房或餐厅边侧。"
    if any(term in node_text for term in ("国庆", "national")):
        return "国庆场景以红金喜庆、家庭假期和明亮客餐厅为主，产品不抢节日主体但清晰可见。"
    if any(term in node_text for term in ("618", "双11", "double")):
        return "电商节点避免廉价促销感，用清爽现代厨房、茶水间或产品展示空间表达咨询与焕新。"
    return "根据节点氛围安排真实生活空间，让产品与人物动线、家具和光影关系自然融合。"


def build_default_scene_prompt(*, node: dict[str, Any], product: dict[str, Any], payload: PosterTaskCreate) -> str:
    extra = payload.scene_prompt.strip() or payload.custom_requirement.strip()
    extra_text = f"用户补充要求：{extra}。" if extra else ""
    return (
        f"围绕“{node.get('name', '节日')}”营造中文节日生活场景，先表达祝福、团圆、焕新或陪伴，"
        f"再让“{product.get('name', '产品')}”作为真实物体自然融入空间。"
        f"{node_scenario_hint(node)}{extra_text}"
    )


def compose_final_image_prompt(
    *,
    node: dict[str, Any],
    product: dict[str, Any],
    payload: PosterTaskCreate,
    title: str,
    subtitle: str,
    base_style_prompt: str,
    node_style_prompt: str,
    scene_prompt: str,
    typography_prompt: str,
) -> str:
    product_points = "、".join(str(item) for item in product.get("selling_points", []) if item)
    extra = payload.scene_prompt.strip() or payload.custom_requirement.strip()
    return (
        "生成一张 1080x1920 竖版中文节日产品海报。"
        f"画面节点：{node.get('name', '节日')}。"
        f"中文艺术字主标题必须写：{title}。"
        f"中文副标题必须写：{subtitle}。"
        "文案需要先符合节日氛围，再自然表达产品陪伴和健康饮水场景，不要强硬推销产品功能。"
        f"统一参考风格：{base_style_prompt}"
        f"当前节点提示词：{node_style_prompt}"
        f"具体场景：{scene_prompt}"
        f"中文艺术字要求：{typography_prompt}"
        f"产品参考：{product.get('name', '')}，品类：{product.get('category', '')}，参考卖点仅作生活化表达：{product_points}。"
        "图片模型只会收到一张产品参考图；产品必须保留真实外观、结构、颜色、比例和材质，"
        "像真实物体一样摆放在餐边柜、厨房台面、客厅边柜或茶水间等合理位置，具有接触面、阴影、遮挡关系和环境光。"
        "顶部为后期 Logo 保留干净空间，底部为后期宣传条保留空间；画面中不要生成额外 Logo、二维码、水印、价格牌或按钮。"
        "除产品型号外不要出现英文字母；所有可见文字都应为清晰中文，不要乱码和错别字。"
        f"{'用户补充需求：' + extra + '。' if extra else ''}"
    )


def combine_negative_prompts(*parts: str) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        for item in re.split(r"[，,。；;\n]+", part or ""):
            clean = item.strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
    return "，".join(result)


def sanitize_marketing_text(text: str, *, fallback: str, limit: int) -> str:
    safe = (text or "").strip()
    for term in COMPLIANCE_RULES["blocked_terms"]:
        safe = safe.replace(term, "")
    safe = re.sub(r"\s+", "", safe)
    if not safe:
        safe = fallback
    return safe[:limit]


def build_negative_prompt() -> str:
    return (
        "不要重绘产品，不要改变产品颜色、结构、比例、材质和细节，"
        "不要让产品变形，不要替换为相似产品，不要生成多个产品，"
        "不要裁切掉产品关键结构，不要遮挡产品主体，不要人物手持，"
        "不要英文文案，不要乱码错字，不要错误汉字，不要硬广口号，不要价格标签，"
        "不要 Logo，不要二维码，不要水印，不要边框，不要按钮，不要贴纸，"
        "不要绝对化承诺，不要医疗功效暗示，不要杂乱背景，"
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
    on_image_title: str,
    on_image_subtitle: str,
    brand_reference_assets: list[BrandReferenceAsset],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base_image = create_mock_atmosphere_canvas(node=node, canvas_size=canvas_size)

    add_festive_motifs(base_image, node)
    add_mock_art_text(base_image, title=on_image_title, subtitle=on_image_subtitle, safe_zones=safe_zones)
    paste_product_into_scene(
        canvas=base_image,
        product_path=product_image_path,
        placement_zone=compute_product_zone(canvas_size=canvas_size, safe_zones=safe_zones),
    )
    add_mock_brand_references(base_image, brand_reference_assets)
    add_depth_vignette(base_image)
    base_image.save(path, "PNG")


def add_mock_brand_references(canvas: Image.Image, brand_reference_assets: list[BrandReferenceAsset]) -> None:
    for asset in brand_reference_assets:
        if asset.role == "logo":
            paste_mock_reference(canvas, asset.path, (72, 78, 332, 166), max_size=(228, 82), plate_alpha=82)
        elif asset.role == "bottom_bar":
            paste_mock_reference(canvas, asset.path, (0, 1760, 1080, 1920), max_size=(1080, 160), plate_alpha=0)
        elif asset.role == "qrcode":
            paste_mock_reference(canvas, asset.path, (778, 1508, 1020, 1754), max_size=(208, 208), plate_alpha=210)


def paste_mock_reference(
    canvas: Image.Image,
    source_path: Path,
    box: tuple[int, int, int, int],
    *,
    max_size: tuple[int, int],
    plate_alpha: int,
) -> None:
    try:
        with Image.open(source_path) as source:
            image = ImageOps.contain(source.convert("RGBA"), max_size, method=Image.Resampling.LANCZOS)
    except Exception:
        return
    draw = ImageDraw.Draw(canvas)
    x = box[0] + (box[2] - box[0] - image.width) // 2
    y = box[1] + (box[3] - box[1] - image.height) // 2
    if plate_alpha:
        draw.rounded_rectangle(
            (x - 16, y - 12, x + image.width + 16, y + image.height + 12),
            radius=22,
            fill=(255, 255, 255, plate_alpha),
        )
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_tile = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_tile.putalpha(image.getchannel("A").filter(ImageFilter.GaussianBlur(6)))
    shadow.alpha_composite(shadow_tile, (x + 3, y + 5))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)))
    canvas.alpha_composite(image, (x, y))


def add_mock_art_text(
    canvas: Image.Image,
    *,
    title: str,
    subtitle: str,
    safe_zones: dict[str, tuple[int, int, int, int]],
) -> None:
    draw = ImageDraw.Draw(canvas)
    width, _ = canvas.size
    top_safe = safe_zones["top_logo"][3]
    title_font = load_prompt_font(82, bold=True)
    subtitle_font = load_prompt_font(34, bold=False)
    title = title or "节日好水相伴"
    subtitle = subtitle or "把健康饮水融入团圆时刻"
    title_box = draw.textbbox((0, 0), title, font=title_font)
    title_x = max(58, (width - (title_box[2] - title_box[0])) // 2)
    title_y = top_safe + 64
    draw.text((title_x + 4, title_y + 5), title, font=title_font, fill=(74, 20, 20, 120))
    draw.text((title_x, title_y), title, font=title_font, fill=(255, 248, 220, 255))
    subtitle_box = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_x = max(70, (width - (subtitle_box[2] - subtitle_box[0])) // 2)
    subtitle_y = title_y + 106
    draw.rounded_rectangle(
        (subtitle_x - 24, subtitle_y - 12, subtitle_x + subtitle_box[2] - subtitle_box[0] + 24, subtitle_y + 52),
        radius=24,
        fill=(255, 255, 255, 74),
    )
    draw.text((subtitle_x, subtitle_y), subtitle, font=subtitle_font, fill=(33, 58, 54, 255))


def load_prompt_font(size: int, *, bold: bool) -> Any:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for font_name in candidates:
        try:
            if font_name.exists():
                return ImageFont.truetype(str(font_name), size)
        except Exception:
            continue
    return ImageFont.load_default()


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


def prepare_brand_reference_images(
    brand_reference_assets: list[BrandReferenceAsset],
    output_dir: Path,
) -> list[BrandReferenceAsset]:
    if not brand_reference_assets:
        return []
    reference_dir = output_dir / "ai_references"
    reference_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[BrandReferenceAsset] = []
    for index, asset in enumerate(brand_reference_assets, start=1):
        if not asset.path.exists() or not asset.path.is_file():
            continue
        role = sanitize_reference_role(asset.role)
        output_path = reference_dir / f"{index:02d}_{role}.png"
        try:
            normalize_brand_reference_image(source_path=asset.path, output_path=output_path, role=role)
        except Exception as exc:
            logger.warning("Could not prepare brand reference %s: %s", asset.asset_id, exc)
            continue
        prepared.append(BrandReferenceAsset(role=role, asset_id=asset.asset_id, path=output_path))
    return prepared


def normalize_brand_reference_image(*, source_path: Path, output_path: Path, role: str) -> None:
    with Image.open(source_path) as source:
        image = source.convert("RGBA")
        if role == "bottom_bar":
            max_size = (1080, 320)
            background = (255, 255, 255, 0)
        elif role == "qrcode":
            max_size = (360, 360)
            background = (255, 255, 255, 255)
        else:
            max_size = (640, 260)
            background = (255, 255, 255, 0)

        image = ImageOps.contain(image, max_size, method=Image.Resampling.LANCZOS)
        if role == "qrcode":
            side = max(image.width, image.height) + 48
            canvas = Image.new("RGBA", (side, side), background)
        else:
            canvas = Image.new("RGBA", max_size, background)
        x = (canvas.width - image.width) // 2
        y = (canvas.height - image.height) // 2
        canvas.alpha_composite(image, (x, y))
        canvas.save(output_path, "PNG")


def sanitize_reference_role(role: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", role.strip().lower()).strip("_")
    return clean or "brand_asset"


def brand_reference_payload(brand_reference_assets: list[BrandReferenceAsset]) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for asset in brand_reference_assets:
        encoded = encode_image_base64(asset.path)
        if not encoded:
            continue
        payload.append({"role": asset.role, "asset_id": asset.asset_id, "image_b64": encoded})
    return payload


def brand_asset_id_map(brand_reference_assets: list[BrandReferenceAsset]) -> dict[str, str]:
    return {asset.role: asset.asset_id for asset in brand_reference_assets if asset.role and asset.asset_id}


def relax_negative_prompt_for_brand_assets(negative_prompt: str) -> str:
    relaxed: list[str] = []
    for item in re.split(r"[锛?銆傦紱;\n]+", negative_prompt or ""):
        clean = item.strip()
        if not clean:
            continue
        lowered = clean.lower()
        if "logo" in lowered or "qrcode" in lowered or "qr code" in lowered or "浜岀淮" in clean:
            continue
        relaxed.append(clean)
    relaxed.extend(
        [
            "no extra non-reference logos",
            "no distorted brand marks",
            "no invented QR codes",
            "no unreadable QR code when a QR reference is supplied",
        ]
    )
    return combine_negative_prompts(*relaxed)


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
            validate_downloaded_image(image_path)
            return
    url = first_image.get("url") or response.get("url")
    if url:
        image_path.write_bytes(download_image(str(url), timeout_seconds))
        validate_downloaded_image(image_path)
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
    brand_asset_roles: list[str] | None = None,
) -> str:
    zones = normalize_safe_zones(safe_zones)
    brand_instruction = build_brand_fusion_instruction(brand_asset_roles or [])
    if brand_instruction:
        prompt = f"{prompt}\n\n{brand_instruction}"
    return (
        f"{prompt}\n\n"
        f"画布比例：竖版 {canvas_size[0]}x{canvas_size[1]}。"
        "请把参考产品自然融入真实营销场景，像原本就在场景里一样，有合理接触面、遮挡关系、阴影、环境光和景深。"
        "例如春节可生成一家人围坐团圆饭的温暖场景，产品自然放在餐边柜、厨房台面或餐厅角落，不要突兀。"
        "保留参考产品的真实外观、品牌结构、材质和比例，不要把产品变成其他物体。"
        f"顶部和底部预留海报排版安全区：{json.dumps(zones, ensure_ascii=False)}。"
        "画面允许生成清晰中文艺术字主标题和中文副文案，但不要生成英文、乱码、错别字、额外 Logo、二维码、水印、价格标签或促销标签。"
        f"负向约束：{negative_prompt}"
    )


def build_brand_fusion_instruction(brand_asset_roles: list[str]) -> str:
    if not brand_asset_roles:
        return (
            "No brand reference image is supplied except the product. Keep clean poster zones for later exact brand placement. "
            "Do not invent logos, QR codes, bottom strips, watermarks, or price tags."
        )
    roles = ", ".join(brand_asset_roles)
    return (
        f"Additional reference images are supplied for these brand roles: {roles}. "
        "Use only the supplied brand references, integrate them into the poster lighting, color, and layout, "
        "and keep their intended zones consistent with the safe-zone JSON. "
        "Logo and bottom strip may be visually blended with the scene; QR code must stay as a crisp high-contrast scan area. "
        "Do not invent extra logos, extra QR codes, fake brand marks, watermarks, buttons, or price tags."
    )


def download_image(url: str, timeout_seconds: int) -> bytes:
    validate_provider_image_url(url)
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            if content_type and not content_type.lower().split(";", 1)[0].startswith("image/"):
                raise AiProviderError("图片 URL 返回的 Content-Type 不是图片")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PROVIDER_IMAGE_BYTES:
                    raise AiProviderError("图片 URL 下载超过大小限制")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        raise AiProviderError(f"图片 URL 下载 HTTP 错误：{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise AiProviderError(f"图片 URL 下载失败：{exc.reason}") from exc


def validate_provider_image_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise AiProviderError("图片 URL 只允许 http/https")
    if not parsed.hostname:
        raise AiProviderError("图片 URL 缺少 host")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise AiProviderError("图片 URL host 无法解析") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
            raise AiProviderError("图片 URL 指向非公网地址")


def validate_downloaded_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise AiProviderError("图片接口返回内容不是有效图片") from exc


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
