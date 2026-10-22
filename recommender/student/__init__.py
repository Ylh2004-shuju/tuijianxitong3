"""Student recommendation algorithms for 实验一 / 实验二.

Every provider is registered explicitly with a sidecar manifest under
``models/business/<version>/``.  Algorithms without a provider keep the 501
``ALGORITHM_NOT_IMPLEMENTED`` contract.

Algorithm map
-------------
content     content-feature-v2  the user's own ratings + idf genres/decade/exposure
popular     popular-positive-mmr-v3  positive(>=4) rating count + MMR diversity rerank
itemcf      itemcf-cosine-v1    movie co-occurrence over positive ratings
cold_start  coldstart-v1        popularity fallback, then a genre profile built
                                from favourites / ratings once signals exist
fusion      fusion-rrf-v1       experimental-4 S1: RRF over the four members above
"""
from pathlib import Path

from ..contracts import Algorithm, PROVIDERS
from .als import ALSRecommender
from .cold_start import ColdStartRecommender
from .content_recommender import ContentFeatureRecommender
from .fusion import FusionRecommender
from .itemcf import ItemCFRecommender
from .popular import PopularRecommender

_MODELS = Path(__file__).resolve().parents[2] / "models" / "business"

PROVIDERS[Algorithm.content] = ContentFeatureRecommender(_MODELS / "content" / "model_manifest.json")
PROVIDERS[Algorithm.popular] = PopularRecommender(_MODELS / "popular-v1" / "manifest.json")
PROVIDERS[Algorithm.itemcf] = ItemCFRecommender(_MODELS / "itemcf-v1" / "manifest.json")
PROVIDERS[Algorithm.cold_start] = ColdStartRecommender(_MODELS / "coldstart-v1" / "manifest.json")
PROVIDERS[Algorithm.als] = ALSRecommender(_MODELS / "als-v1" / "manifest.json")
PROVIDERS[Algorithm.fusion] = FusionRecommender(_MODELS / "fusion-v1" / "manifest.json")
