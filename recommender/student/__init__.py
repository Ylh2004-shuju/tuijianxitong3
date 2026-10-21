"""Student recommendation algorithms for 实验一 / 实验二.

Every provider is registered explicitly with a sidecar manifest under
``models/business/<version>/``.  Algorithms without a provider keep the 501
``ALGORITHM_NOT_IMPLEMENTED`` contract.

Algorithm map
-------------
content     content-genre-v1   the user's own ratings + movie genres
popular     popular-bayes-v1   global Bayesian rating average (non-personalized)
itemcf      itemcf-cosine-v1   movie co-occurrence over positive ratings
cold_start  coldstart-v1       popularity fallback, then a genre profile built
                               from favourites / ratings once signals exist
"""
from pathlib import Path

from ..contracts import Algorithm, PROVIDERS
from .cold_start import ColdStartRecommender
from .content_recommender import ContentGenreRecommender
from .itemcf import ItemCFRecommender
from .popular import PopularRecommender

_MODELS = Path(__file__).resolve().parents[2] / "models" / "business"

PROVIDERS[Algorithm.content] = ContentGenreRecommender(_MODELS / "content" / "model_manifest.json")
PROVIDERS[Algorithm.popular] = PopularRecommender(_MODELS / "popular-v1" / "manifest.json")
PROVIDERS[Algorithm.itemcf] = ItemCFRecommender(_MODELS / "itemcf-v1" / "manifest.json")
PROVIDERS[Algorithm.cold_start] = ColdStartRecommender(_MODELS / "coldstart-v1" / "manifest.json")
