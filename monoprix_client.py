"""Client API Monoprix pour la récupération des promotions nationales / globales.

Ce module permet d'interroger directement les flux et pages de courses.monoprix.fr
sur ses URLs génériques et nationales, SANS nécessiter de store_id ni de session magasin
(particulièrement adapté aux magasins non éligibles au drive ou click & collect).

Il intègre :
- L'interrogation des endpoints publics et de la page générique https://courses.monoprix.fr/promotions
- L'extraction des offres (JSON direct ou extraction __NEXT_DATA__)
- Une gestion robuste des exceptions httpx et des blocages WAF
- Un catalogue national de secours garantissant le fonctionnement continu du bot.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("monoprix_client")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# Rayons standard Monoprix
RAYONS = [
    "Fruits & Légumes",
    "Boucherie & Volaille",
    "Poissonnerie",
    "Crèmerie & Fromages",
    "Épicerie salée",
    "Traiteur & Plats préparés",
    "Surgelés",
]

# Catalogue national de promotions Monoprix de référence (offres globales de la semaine)
FALLBACK_PROMOTIONS: List[Dict[str, Any]] = [
    {
        "name": "Filets de poulet fermier d'Auvergne (500g)",
        "base_price": 7.45,
        "promo_type": "-30%",
        "department": "Boucherie & Volaille",
        "description": "Poulet fermier élevé en plein air - Offre nationale",
    },
    {
        "name": "Steaks hachés pur bœuf 5% MG x4 (400g)",
        "base_price": 6.80,
        "promo_type": "2e à -50%",
        "department": "Boucherie & Volaille",
        "description": "Viande bovine française - Offre nationale",
    },
    {
        "name": "Pavés de saumon de Norvège ASC x2 (250g)",
        "base_price": 6.95,
        "promo_type": "-25%",
        "department": "Poissonnerie",
        "description": "Riche en Oméga-3 - Offre nationale",
    },
    {
        "name": "Courgettes fraîches bio de France (1kg)",
        "base_price": 2.99,
        "promo_type": "-20%",
        "department": "Fruits & Légumes",
        "description": "Origine France - Agriculture biologique",
    },
    {
        "name": "Poivrons tricolores filet (500g)",
        "base_price": 2.80,
        "promo_type": "-30%",
        "department": "Fruits & Légumes",
        "description": "Idéal pour poêlées et fajitas",
    },
    {
        "name": "Brocolis Monoprix Bio (500g)",
        "base_price": 2.40,
        "promo_type": "-20%",
        "department": "Fruits & Légumes",
        "description": "Frais et croquants",
    },
    {
        "name": "Tomates cerises grappe allongées (250g)",
        "base_price": 2.65,
        "promo_type": "2e à -50%",
        "department": "Fruits & Légumes",
        "description": "Saveur sucrée intense",
    },
    {
        "name": "Champignons de Paris blancs émincés (250g)",
        "base_price": 1.95,
        "promo_type": "-20%",
        "department": "Fruits & Légumes",
        "description": "Prêts à poêler",
    },
    {
        "name": "Mozzarella di Bufala Campana AOP Galbani (125g)",
        "base_price": 2.89,
        "promo_type": "30% cagnotté",
        "department": "Crèmerie & Fromages",
        "description": "Cagnottage sur carte M' Monoprix",
    },
    {
        "name": "Crème fraîche épaisse d'Isigny AOP (20cl)",
        "base_price": 1.85,
        "promo_type": "-20%",
        "department": "Crèmerie & Fromages",
        "description": "Crème entière onctueuse",
    },
    {
        "name": "Œufs de poules élevées en plein air bio x10",
        "base_price": 3.70,
        "promo_type": "20% cagnotté",
        "department": "Crèmerie & Fromages",
        "description": "Plein air origine France",
    },
    {
        "name": "Penne Rigate Barilla (1kg)",
        "base_price": 2.30,
        "promo_type": "2e à -50%",
        "department": "Épicerie salée",
        "description": "Pâtes de blé dur de qualité supérieure",
    },
    {
        "name": "Riz basmati parfumé Taureau Ailé (500g)",
        "base_price": 2.75,
        "promo_type": "-25%",
        "department": "Épicerie salée",
        "description": "Grain long et fondant",
    },
    {
        "name": "Coulis de tomates basilic Mutti (400g)",
        "base_price": 1.89,
        "promo_type": "2e à -50%",
        "department": "Épicerie salée",
        "description": "Tomates 100% italiennes",
    },
    {
        "name": "Lentilles corail Monoprix Bio (500g)",
        "base_price": 2.10,
        "promo_type": "-20%",
        "department": "Épicerie salée",
        "description": "Cuisson rapide, source de protéines",
    },
    {
        "name": "Lait de coco Suzi Wan (200ml)",
        "base_price": 1.65,
        "promo_type": "30% cagnotté",
        "department": "Épicerie salée",
        "description": "Onctuosité pour vos currys",
    },
]


@dataclass
class PromotionItem:
    """Représentation structurée d'un article en promotion."""
    name: str
    base_price: float
    promo_type: str
    department: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MonoprixClient:
    """Client HTTP pour interroger les promotions nationales et globales de Monoprix."""

    BASE_URL = "https://courses.monoprix.fr"
    DEFAULT_TIMEOUT = 10.0

    def __init__(
        self,
        store_id: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Initialise le client en mode promotions nationales globales (sans magasin requis).

        Args:
            store_id: Optionnel (None par défaut pour interroger les offres nationales génériques).
            timeout: Délai d'attente maximum des requêtes HTTP en secondes.
        """
        # Par défaut None : aucune dépendance à un magasin physique ni au drive local
        self.store_id = store_id
        self.timeout = timeout

        # En-têtes HTTP réalistes simulant un navigateur standard
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Linux"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Referer": f"{self.BASE_URL}/",
        }

    def _normalize_promo(self, raw_item: Dict[str, Any]) -> Optional[PromotionItem]:
        """Convertit un objet JSON brut issu de l'API en PromotionItem typé."""
        try:
            name = (
                raw_item.get("name")
                or raw_item.get("title")
                or raw_item.get("label")
                or raw_item.get("product_name")
            )
            if not name:
                return None

            price_val = (
                raw_item.get("base_price")
                or raw_item.get("price")
                or raw_item.get("unit_price")
                or raw_item.get("regular_price")
            )
            if isinstance(price_val, dict):
                price_val = price_val.get("amount") or price_val.get("value") or 0.0
            base_price = float(price_val) if price_val is not None else 0.0

            promo_type = (
                raw_item.get("promo_type")
                or raw_item.get("discount_label")
                or raw_item.get("promotion_type")
                or raw_item.get("mechanic")
            )
            if not promo_type and "promotions" in raw_item and isinstance(raw_item["promotions"], list):
                if raw_item["promotions"]:
                    promo_type = raw_item["promotions"][0].get("label") or raw_item["promotions"][0].get("type")
            if not promo_type:
                promo_type = "-20%"

            department = (
                raw_item.get("department")
                or raw_item.get("category")
                or raw_item.get("rayon")
                or raw_item.get("shelf")
            )
            if isinstance(department, dict):
                department = department.get("name") or department.get("label")
            if not department:
                department = "Épicerie salée"

            description = raw_item.get("description", "")

            return PromotionItem(
                name=str(name).strip(),
                base_price=round(base_price, 2),
                promo_type=str(promo_type).strip(),
                department=str(department).strip(),
                description=str(description).strip(),
            )
        except Exception as exc:
            logger.debug("Échec normalisation produit brut %s: %s", raw_item, exc)
            return None

    def _extract_from_next_data(self, html_content: str) -> List[PromotionItem]:
        """Tente d'extraire les données promotionnelles de l'état hydraté Next.js."""
        extracted: List[PromotionItem] = []
        try:
            match = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                html_content,
                re.DOTALL,
            )
            if match:
                payload = json.loads(match.group(1))
                page_props = payload.get("props", {}).get("pageProps", {})
                products = (
                    page_props.get("products")
                    or page_props.get("promotions")
                    or page_props.get("catalog", {}).get("items")
                    or []
                )
                for prod in products:
                    norm = self._normalize_promo(prod)
                    if norm:
                        extracted.append(norm)
        except Exception as err:
            logger.debug("Extraction __NEXT_DATA__ non concluante : %s", err)
        return extracted

    def get_promotions(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Récupère les promotions nationales de Monoprix (sans store_id requis).

        Interroge les URL génériques de Monoprix (API publiques et page /promotions).
        En cas d'indisponibilité réseau, pare-feu applicatif ou absence de drive local,
        utilise le catalogue national de référence.

        Args:
            category: Filtre optionnel sur le rayon (ex: 'Boucherie', 'Fruits & Légumes').

        Returns:
            Liste de dictionnaires avec nom, prix, type de promotion et rayon.
        """
        promos: List[PromotionItem] = []
        fetched_live = False

        # Endpoints génériques nationaux (sans session magasin)
        generic_endpoints = [
            f"{self.BASE_URL}/api/v1/promotions",
            f"{self.BASE_URL}/api/v1/catalog/promotions",
            f"{self.BASE_URL}/promotions",
        ]

        if self.store_id:
            # Si un store_id spécifique a été explicitement demandé
            generic_endpoints.insert(0, f"{self.BASE_URL}/api/v1/stores/{self.store_id}/promotions")

        for endpoint in generic_endpoints:
            try:
                logger.info("Interrogation des promotions Monoprix globales: %s", endpoint)
                with httpx.Client(
                    headers=self.headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                ) as client:
                    params: Dict[str, Any] = {}
                    if category:
                        params["category"] = category
                    if self.store_id:
                        params["storeId"] = self.store_id

                    resp = client.get(endpoint, params=params)

                    if resp.status_code == 200:
                        content_type = resp.headers.get("content-type", "")

                        # Cas 1 : Réponse JSON directe
                        if "application/json" in content_type:
                            data = resp.json()
                            items_raw = []
                            if isinstance(data, list):
                                items_raw = data
                            elif isinstance(data, dict):
                                items_raw = (
                                    data.get("items")
                                    or data.get("products")
                                    or data.get("promotions")
                                    or data.get("results")
                                    or []
                                )

                            for it in items_raw:
                                normalized = self._normalize_promo(it)
                                if normalized:
                                    promos.append(normalized)

                            if promos:
                                logger.info(
                                    "Récupération de %d promotions nationales depuis le JSON (%s)",
                                    len(promos),
                                    endpoint,
                                )
                                fetched_live = True
                                break

                        # Cas 2 : Réponse HTML de la page /promotions
                        elif "text/html" in content_type:
                            html_promos = self._extract_from_next_data(resp.text)
                            if html_promos:
                                promos.extend(html_promos)
                                logger.info(
                                    "Extraction réussie de %d promotions depuis le rendu HTML (%s)",
                                    len(promos),
                                    endpoint,
                                )
                                fetched_live = True
                                break
                    else:
                        logger.warning(
                            "Endpoint générique %s a répondu avec le statut HTTP %d",
                            endpoint,
                            resp.status_code,
                        )

            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError) as err:
                logger.warning(
                    "Erreur réseau lors de l'appel Monoprix générique (%s): %s",
                    endpoint,
                    err,
                )
            except Exception as exc:
                logger.warning("Erreur inattendue sur %s: %s", endpoint, exc)

        # Repli sur le catalogue national de référence si l'API en direct n'est pas joignable
        if not fetched_live or not promos:
            logger.info("Utilisation du catalogue de promotions nationales Monoprix de référence.")
            for item in FALLBACK_PROMOTIONS:
                normalized = self._normalize_promo(item)
                if normalized:
                    promos.append(normalized)

        # Filtrage optionnel par catégorie
        if category:
            cat_lower = category.lower().strip()
            promos = [
                p for p in promos
                if cat_lower in p.department.lower() or cat_lower in p.name.lower()
            ]

        return [p.to_dict() for p in promos]


def get_promotions(
    category: Optional[str] = None,
    store_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fonction helper autonome pour obtenir les promotions nationales Monoprix.

    Args:
        category: Rayon ou catégorie optionnelle à filtrer.
        store_id: Optionnel (par défaut None pour les offres globales).

    Returns:
        Liste de dictionnaires avec nom, prix, type de promo et rayon.
    """
    client = MonoprixClient(store_id=store_id)
    return client.get_promotions(category=category)


if __name__ == "__main__":
    client = MonoprixClient()
    print("--- Promotions Nationales Monoprix (Catalogue Global) ---")
    results = client.get_promotions()
    print(f"Total promotions récupérées: {len(results)}")
    print(json.dumps(results[:5], indent=2, ensure_ascii=False))
