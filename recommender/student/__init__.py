"""Student recommendation algorithms.

Register content-based recommender with the global provider registry.
"""
from pathlib import Path

from ..contracts import Algorithm, PROVIDERS
from .content_recommender import ContentGenreRecommender

# Model manifest path - relative to package root, resolved at runtime
_model_dir = Path(__file__).resolve().parents[2] / "models" / "business" / "content"
_manifest_path = _model_dir / "model_manifest.json"

PROVIDERS[Algorithm.content] = ContentGenreRecommender(_manifest_path)
