"""Enroll a known speaker by computing their voice embedding from clean samples.

Prereqs:

- HuggingFace token with access to ``pyannote/wespeaker-voxceleb-resnet34-LM``
  (accept the model license on huggingface.co first).
- Sample audio >= 20 s of clean speech from the target speaker, no overlap.

Multiple clips are embedded separately, L2-normalized, and stored as a stack
of shape ``(N_clips, D)``. The pipeline scores each cluster against every clip
and takes the best cosine per speaker, so contamination in one clip doesn't
mask a match on another. Enrolling again appends to the stored stack; pass
``replace=True`` (CLI ``--replace``) to overwrite it.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from pyannote.audio import Inference, Model

from .pipeline import hf_auth


def _pick_device() -> torch.device:
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def enroll_speaker(
    name: str,
    audio_paths: list[str | Path],
    out_dir: str | Path = "enrollments",
    hf_token: str | None = None,
    replace: bool = False,
) -> Path:
    """Enroll a speaker from one or more reference clips.

    Saves ``{out_dir}/{name}.npy`` with shape ``(N_clips, D)``, each row
    L2-normalized. If the file exists, the new rows are appended to it
    (a clip whose embedding is already stored is skipped); ``replace=True``
    overwrites it instead. Returns the path to the saved file.
    """
    auth = hf_auth(hf_token)
    device = _pick_device()
    print(f"device: {device}")

    model = Model.from_pretrained(
        "pyannote/wespeaker-voxceleb-resnet34-LM",
        use_auth_token=auth,
    )
    inference = Inference(model, window="whole", device=device)

    normalized = []
    for p in audio_paths:
        emb = np.asarray(inference(str(p))).squeeze()
        emb = emb / (np.linalg.norm(emb) + 1e-9)
        normalized.append(emb)
        print(f"  embedded {p} (dim={emb.shape[0]})")

    stack = np.stack(normalized)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    out_path = out / f"{name}.npy"
    if out_path.exists():
        stored = np.load(out_path)
        if stored.ndim == 1:
            stored = stored[None, :]
        if replace:
            print(f"replacing {out_path} ({stored.shape[0]} stored embedding(s))")
        else:
            if stored.shape[1] != stack.shape[1]:
                raise ValueError(
                    f"{out_path} stores {stored.shape[1]}-dim embeddings but the "
                    f"new clips give {stack.shape[1]}-dim; use --replace to "
                    f"overwrite it."
                )
            unit = stored / (np.linalg.norm(stored, axis=1, keepdims=True) + 1e-9)
            new = [row for row in stack if float(np.max(unit @ row)) < 0.9999]
            if len(new) < len(stack):
                print(f"  skipped {len(stack) - len(new)} clip(s) already stored")
            print(f"appending to {out_path} ({stored.shape[0]} stored embedding(s))")
            stack = np.vstack([stored, *new]) if new else stored
    np.save(out_path, stack)
    print(f"saved {out_path} (stored {stack.shape[0]} clip embedding(s))")
    return out_path
