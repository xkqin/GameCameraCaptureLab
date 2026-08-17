from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import requests
import torch
from PIL import Image, UnidentifiedImageError
from torch import nn
from tqdm import tqdm

from .paths import resolve_project_path

LOGGER = logging.getLogger(__name__)

MODEL_SPECS = {
    "vit_l_14": {"clip_name": "ViT-L-14", "pretrained": "openai", "dim": 768},
    "vit_b_32": {"clip_name": "ViT-B-32", "pretrained": "openai", "dim": 512},
}

OFFICIAL_WEIGHT_URLS = [
    "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/sac%2Blogos%2Bava1-l14-linearMSE.pth",
    "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/ava%2Blogos-l14-linearMSE.pth",
    "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/sa_0_4_vit_l_14_linear.pth",
]


class MLP(nn.Module):
    """Architecture used by LAION aesthetic-predictor examples."""

    def __init__(self, input_size: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(input_size, 1024),
            nn.Dropout(0.2),
            nn.Linear(1024, 128),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.Dropout(0.1),
            nn.Linear(64, 16),
            nn.Linear(16, 1),
        )

    def forward(self, embed: torch.Tensor) -> torch.Tensor:
        return self.layers(embed)


@dataclass
class LAIONAestheticScorer:
    model_name: str = "vit_l_14"
    device: str = "auto"
    repo_dir: str | Path = "core/third_party/aesthetic-predictor"
    cache_dir: str | Path = "~/.cache/re9_pose_recorder"
    hf_cache_dir: str | Path = "core/third_party/huggingface_cache"

    def __post_init__(self) -> None:
        self.device_name = self._resolve_device(self.device)
        self.clip_model: nn.Module | None = None
        self.preprocess = None
        self.aesthetic_head: nn.Module | None = None

    def load_model(self) -> "LAIONAestheticScorer":
        self._configure_huggingface_cache()
        import open_clip

        key = self.model_name.lower()
        if key not in MODEL_SPECS:
            raise ValueError(f"Unsupported model '{self.model_name}'. Use one of: {', '.join(MODEL_SPECS)}")
        spec = MODEL_SPECS[key]
        clip_model, _, preprocess = open_clip.create_model_and_transforms(
            spec["clip_name"], pretrained=spec["pretrained"]
        )
        clip_model.to(self.device_name)
        clip_model.eval()
        head = self._load_aesthetic_head(int(spec["dim"]))
        head.to(self.device_name)
        head.eval()
        self.clip_model = clip_model
        self.preprocess = preprocess
        self.aesthetic_head = head
        return self

    def score_images(self, image_paths: Iterable[str | Path], batch_size: int = 32) -> pd.DataFrame:
        if self.clip_model is None or self.preprocess is None or self.aesthetic_head is None:
            self.load_model()

        rows: list[dict[str, object]] = []
        batch_tensors: list[torch.Tensor] = []
        batch_paths: list[Path] = []

        def flush() -> None:
            if not batch_tensors:
                return
            tensor = torch.stack(batch_tensors).to(self.device_name)
            with torch.no_grad():
                embeds = self.clip_model.encode_image(tensor)  # type: ignore[union-attr]
                embeds = embeds / embeds.norm(dim=-1, keepdim=True)
                scores = self.aesthetic_head(embeds).detach().cpu().numpy().reshape(-1)  # type: ignore[operator]
            for path, score in zip(batch_paths, scores, strict=True):
                rows.append({"frame_path": str(path), "file_name": path.name, "score": float(score)})
            batch_tensors.clear()
            batch_paths.clear()

        paths = [Path(item) for item in image_paths]
        for path in tqdm(paths, desc="Scoring frames"):
            try:
                with Image.open(path) as image:
                    tensor = self.preprocess(image.convert("RGB"))  # type: ignore[misc]
            except (OSError, UnidentifiedImageError) as exc:
                LOGGER.warning("Skipping corrupted image %s: %s", path, exc)
                continue
            batch_tensors.append(tensor)
            batch_paths.append(path)
            if len(batch_tensors) >= batch_size:
                flush()
        flush()
        return pd.DataFrame(rows)

    def score_pil_image(self, image: Image.Image) -> float:
        """Score one PIL image with the loaded LAION aesthetic head."""
        if self.clip_model is None or self.preprocess is None or self.aesthetic_head is None:
            self.load_model()
        tensor = self.preprocess(image.convert("RGB")).unsqueeze(0).to(self.device_name)  # type: ignore[misc]
        with torch.no_grad():
            embeds = self.clip_model.encode_image(tensor)  # type: ignore[union-attr]
            embeds = embeds / embeds.norm(dim=-1, keepdim=True)
            score = self.aesthetic_head(embeds).detach().cpu().numpy().reshape(-1)[0]  # type: ignore[operator]
        return float(score)

    def score_pil_images(self, images: Iterable[Image.Image], batch_size: int = 32) -> list[float]:
        """Score PIL images in batches while preserving one score per frame."""
        if self.clip_model is None or self.preprocess is None or self.aesthetic_head is None:
            self.load_model()

        scores_out: list[float] = []
        batch: list[torch.Tensor] = []

        def flush() -> None:
            if not batch:
                return
            tensor = torch.stack(batch).to(self.device_name)
            with torch.no_grad():
                embeds = self.clip_model.encode_image(tensor)  # type: ignore[union-attr]
                embeds = embeds / embeds.norm(dim=-1, keepdim=True)
                scores = self.aesthetic_head(embeds).detach().cpu().numpy().reshape(-1)  # type: ignore[operator]
            scores_out.extend(float(score) for score in scores)
            batch.clear()

        for image in images:
            batch.append(self.preprocess(image.convert("RGB")))  # type: ignore[misc]
            if len(batch) >= batch_size:
                flush()
        flush()
        return scores_out

    def score_folder(
        self,
        input_dir: str | Path,
        output_csv: str | Path,
        batch_size: int = 32,
        frame_metadata_csv: str | Path | None = None,
    ) -> pd.DataFrame:
        root = Path(input_dir)
        image_paths = sorted(
            path for path in root.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        scores = self.score_images(image_paths, batch_size=batch_size)

        if frame_metadata_csv is None:
            candidate = root / "frame_metadata.csv"
            frame_metadata_csv = candidate if candidate.exists() else None

        if frame_metadata_csv:
            metadata = pd.read_csv(frame_metadata_csv)
            metadata["frame_path_norm"] = metadata["frame_path"].map(lambda value: str(Path(value)))
            scores["frame_path_norm"] = scores["frame_path"].map(lambda value: str(Path(value)))
            merged = metadata.merge(scores[["frame_path_norm", "score"]], on="frame_path_norm", how="inner")
            result = merged.drop(columns=["frame_path_norm"])
            result["file_name"] = result["frame_path"].map(lambda value: Path(str(value)).name)
        else:
            result = _metadata_from_filenames(scores)

        columns = ["video_path", "frame_path", "file_name", "frame_index", "timestamp_sec", "score", "width", "height"]
        for column in columns:
            if column not in result.columns:
                result[column] = ""
        result = result[columns]
        Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(output_csv, index=False)
        return result

    def _load_aesthetic_head(self, embedding_dim: int) -> nn.Module:
        weights_path, state = _find_or_fetch_weights(self.repo_dir, self.cache_dir, embedding_dim)
        LOGGER.info("Using LAION aesthetic weights: %s", weights_path)
        return _head_from_state(state, embedding_dim)

    def _configure_huggingface_cache(self) -> None:
        cache_root = resolve_project_path(self.hf_cache_dir)
        hub_cache = cache_root / "hub"
        torch_cache = cache_root / "torch"
        hub_cache.mkdir(parents=True, exist_ok=True)
        torch_cache.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(cache_root)
        os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub_cache)
        os.environ["TORCH_HOME"] = str(torch_cache)

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda" and not torch.cuda.is_available():
            LOGGER.warning("CUDA requested but unavailable; falling back to CPU.")
            return "cpu"
        return device


def load_model(model_name: str = "vit_l_14", device: str = "auto") -> LAIONAestheticScorer:
    return LAIONAestheticScorer(model_name=model_name, device=device).load_model()


def score_images(image_paths: Iterable[str | Path], batch_size: int = 32) -> pd.DataFrame:
    return LAIONAestheticScorer().score_images(image_paths, batch_size=batch_size)


def score_folder(
    input_dir: str | Path,
    output_csv: str | Path,
    frame_metadata_csv: str | Path | None = None,
    model_name: str = "vit_l_14",
    device: str = "auto",
    batch_size: int = 32,
    repo_dir: str | Path = "core/third_party/aesthetic-predictor",
    cache_dir: str | Path = "~/.cache/re9_pose_recorder",
    hf_cache_dir: str | Path = "core/third_party/huggingface_cache",
) -> pd.DataFrame:
    scorer = LAIONAestheticScorer(
        model_name=model_name,
        device=device,
        repo_dir=repo_dir,
        cache_dir=cache_dir,
        hf_cache_dir=hf_cache_dir,
    )
    scorer.load_model()
    return scorer.score_folder(input_dir, output_csv, batch_size=batch_size, frame_metadata_csv=frame_metadata_csv)


def _metadata_from_filenames(scores: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    pattern = re.compile(r"frame_(\d+)_t([0-9.]+)")
    for _, row in scores.iterrows():
        path = Path(str(row["frame_path"]))
        match = pattern.search(path.stem)
        frame_index = int(match.group(1)) if match else ""
        timestamp = float(match.group(2)) if match else ""
        width: int | str = ""
        height: int | str = ""
        try:
            with Image.open(path) as image:
                width, height = image.size
        except OSError:
            pass
        rows.append(
            {
                "video_path": "",
                "frame_path": str(path),
                "file_name": path.name,
                "frame_index": frame_index,
                "timestamp_sec": timestamp,
                "score": row["score"],
                "width": width,
                "height": height,
            }
        )
    return pd.DataFrame(rows)


def _find_or_fetch_weights(
    repo_dir: str | Path, cache_dir: str | Path, embedding_dim: int
) -> tuple[Path, dict[str, torch.Tensor]]:
    repo = resolve_project_path(repo_dir)
    candidates = []
    if repo.exists():
        candidates.extend(sorted(repo.rglob("*.pth")))
        candidates.extend(sorted(repo.rglob("*.pt")))
    cache = Path(cache_dir).expanduser()
    cache.mkdir(parents=True, exist_ok=True)
    candidates.extend(sorted(cache.glob("*.pth")))
    candidates.extend(sorted(cache.glob("*.pt")))

    for path in candidates:
        state = _load_state(path)
        if state is not None and _state_matches_dim(state, embedding_dim):
            return path, state

    for url in OFFICIAL_WEIGHT_URLS:
        filename = url.rsplit("/", 1)[-1].replace("%2B", "+")
        target = cache / filename
        if not target.exists():
            response = requests.get(url, timeout=30)
            if response.status_code != 200:
                continue
            target.write_bytes(response.content)
        state = _load_state(target)
        if state is not None and _state_matches_dim(state, embedding_dim):
            return target, state

    raise RuntimeError(
        f"No official LAION aesthetic predictor weights matching {embedding_dim} dimensions were found. "
        "Run setup-laion, or place the official .pth weights in core/third_party/aesthetic-predictor."
    )


def _load_state(path: Path) -> dict[str, torch.Tensor] | None:
    try:
        loaded = torch.load(path, map_location="cpu")
    except Exception as exc:
        LOGGER.debug("Could not inspect weights %s: %s", path, exc)
        return None
    if isinstance(loaded, nn.Module):
        return loaded.state_dict()
    if isinstance(loaded, dict) and "state_dict" in loaded and isinstance(loaded["state_dict"], dict):
        loaded = loaded["state_dict"]
    if not isinstance(loaded, dict):
        return None
    return {str(key).replace("module.", ""): value for key, value in loaded.items() if torch.is_tensor(value)}


def _state_matches_dim(state: dict[str, torch.Tensor], embedding_dim: int) -> bool:
    for key, value in state.items():
        if value.ndim == 2 and value.shape[1] == embedding_dim:
            return True
        if value.ndim == 2 and value.shape[0] == embedding_dim:
            return True
    return False


def _head_from_state(state: dict[str, torch.Tensor], embedding_dim: int) -> nn.Module:
    clean = {key.replace("model.", "").replace("aesthetic.", ""): value for key, value in state.items()}
    linear_weight_key = next(
        (
            key
            for key, value in clean.items()
            if value.ndim == 2 and value.shape[1] == embedding_dim and value.shape[0] == 1
        ),
        None,
    )
    if linear_weight_key is not None:
        bias_key = linear_weight_key.replace("weight", "bias")
        linear = nn.Linear(embedding_dim, 1)
        linear.weight.data.copy_(clean[linear_weight_key].float())
        if bias_key in clean:
            linear.bias.data.copy_(clean[bias_key].float().reshape_as(linear.bias.data))
        return linear

    mlp = MLP(embedding_dim)
    missing, unexpected = mlp.load_state_dict(clean, strict=False)
    if len(missing) <= 2 and not unexpected:
        return mlp
    raise RuntimeError(
        "Found LAION weights, but their architecture was not recognized. "
        f"Missing keys: {missing}; unexpected keys: {unexpected}"
    )
