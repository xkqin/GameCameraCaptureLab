from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Callable

from .paths import MODELS_DIR, ensure_data_dirs


AESTHETIC_WEIGHT_URL = (
    "https://raw.githubusercontent.com/LAION-AI/aesthetic-predictor/main/"
    "sac%2Blogos%2Bava1-l14-linearMSE.pth"
)


class LAIONAestheticScorer:
    def __init__(self, *, device: str = "auto") -> None:
        self.requested_device = device
        self.device = ""
        self.clip_model = None
        self.preprocess = None
        self.head = None

    def load(self) -> None:
        try:
            import open_clip
            import torch
            from torch import nn
        except ImportError as exc:
            raise RuntimeError(
                "LAION scoring dependencies are unavailable. Use the project "
                "launcher or install the 'analysis' optional dependencies."
            ) from exc
        ensure_data_dirs()
        self.device = (
            "cuda"
            if self.requested_device == "auto" and torch.cuda.is_available()
            else "cpu"
            if self.requested_device == "auto"
            else self.requested_device
        )
        if self.device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"
        open_clip_cache = MODELS_DIR / "open_clip"
        open_clip_cache.mkdir(parents=True, exist_ok=True)
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-L-14",
            pretrained="openai",
            cache_dir=str(open_clip_cache),
        )
        model.to(self.device)
        model.eval()
        weights = self._weight_path()
        state = torch.load(weights, map_location="cpu")
        if isinstance(state, nn.Module):
            head = state
        else:
            if isinstance(state, dict) and isinstance(state.get("state_dict"), dict):
                state = state["state_dict"]
            clean = {
                str(key).replace("module.", ""): value
                for key, value in state.items()
                if torch.is_tensor(value)
            }
            linear_key = next(
                (
                    key
                    for key, value in clean.items()
                    if value.ndim == 2
                    and value.shape[1] == 768
                    and value.shape[0] == 1
                ),
                None,
            )
            if linear_key is not None:
                head = nn.Linear(768, 1)
                head.weight.data.copy_(clean[linear_key].float())
                bias_key = linear_key.replace("weight", "bias")
                if bias_key in clean:
                    head.bias.data.copy_(
                        clean[bias_key].float().reshape_as(head.bias.data)
                    )
            else:
                head = _mlp(nn, 768)
                missing, unexpected = head.load_state_dict(clean, strict=False)
                if len(missing) > 2 or unexpected:
                    raise RuntimeError(
                        "LAION weight architecture was not recognized: "
                        f"missing={missing}, unexpected={unexpected}"
                    )
        head.to(self.device)
        head.eval()
        self.clip_model = model
        self.preprocess = preprocess
        self.head = head

    def score_metadata(
        self,
        metadata_csv: str | Path,
        output_csv: str | Path,
        *,
        batch_size: int = 16,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if self.clip_model is None:
            self.load()
        import torch
        from PIL import Image

        with Path(metadata_csv).open(
            "r", newline="", encoding="utf-8-sig"
        ) as handle:
            metadata = list(csv.DictReader(handle))
        rows: list[dict[str, Any]] = []
        tensors = []
        batch_rows: list[dict[str, str]] = []

        def flush() -> None:
            if not tensors:
                return
            batch = torch.stack(tensors).to(self.device)
            with torch.no_grad():
                embeddings = self.clip_model.encode_image(batch)
                embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
                scores = (
                    self.head(embeddings).detach().cpu().numpy().reshape(-1)
                )
            for item, score in zip(batch_rows, scores, strict=True):
                row = dict(item)
                row["score"] = float(score)
                rows.append(row)
            tensors.clear()
            batch_rows.clear()
            if progress_callback is not None:
                progress_callback(len(rows), len(metadata))

        for item in metadata:
            image_path = Path(item["frame_path"])
            if not image_path.exists():
                continue
            with Image.open(image_path) as image:
                tensors.append(self.preprocess(image.convert("RGB")))
            batch_rows.append(item)
            if len(tensors) >= batch_size:
                flush()
        flush()
        fields = list(rows[0]) if rows else list(metadata[0]) + ["score"]
        output = Path(output_csv).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return {
            "output_csv": str(output),
            "scored_count": len(rows),
            "metadata_count": len(metadata),
            "device": self.device,
        }

    def _weight_path(self) -> Path:
        target = MODELS_DIR / "sac+logos+ava1-l14-linearMSE.pth"
        if target.exists():
            return target
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                f"Aesthetic weights are missing and requests is unavailable: {target}"
            ) from exc
        response = requests.get(AESTHETIC_WEIGHT_URL, timeout=60)
        response.raise_for_status()
        target.write_bytes(response.content)
        return target


def _mlp(nn, input_size: int):
    class MLP(nn.Module):
        def __init__(self) -> None:
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

        def forward(self, embed):
            return self.layers(embed)

    return MLP()
